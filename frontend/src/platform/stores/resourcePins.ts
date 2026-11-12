import {platformFetch as fetch} from '../../transport';
import {defineStore} from 'pinia';
import {ref} from 'vue';

export type ResourcePin={provider:string;project:string;version:string};
export type PinProject={id:string;title:string;description:string;icon:string};
export type PinVersion={id:string;name:string;games:string[];loaders:string[];release:boolean};
type PinLabel={title:string;version:string;context:string};
const classes:Record<string,number>={mod:6,resourcepack:12,shader:6552,modpack:4471};
export const pinKey=(pin:ResourcePin)=>[pin.provider,pin.project,pin.version].join('/');
const context=(version:PinVersion)=>[version.games.slice(0,3).join('、'),version.loaders.join(' / ')].filter(Boolean).join(' · ');

export const useResourcePinsStore=defineStore('resource-pins',()=>{
  const results=ref<PinProject[]>([]),versions=ref<PinVersion[]>([]),selected=ref<PinProject|null>(null);
  const loading=ref(false),versionLoading=ref(false),error=ref(''),versionError=ref(''),labels=ref<Record<string,PinLabel>>({});
  const labelErrors=ref<Record<string,boolean>>({}),describing=ref(false);
  let searchController:AbortController|undefined,versionController:AbortController|undefined,labelController:AbortController|undefined;
  const projects=new Map<string,PinProject>(),releases=new Map<string,PinVersion>();
  const identity=(pin:ResourcePin)=>pinKey({...pin,project:projects.get(pin.provider+'/'+pin.project)?.id||pin.project});
  async function read(provider:string,path:string,signal:AbortSignal,params:Record<string,string>={}){
    let response:Response;
    try{response=await fetch('/v1/catalog/'+provider+path+'?'+new URLSearchParams(params),{signal:AbortSignal.any([signal,AbortSignal.timeout(20000)]),cache:'no-store'});}
    catch(cause){if(signal.aborted)throw cause;throw Error('资源目录暂时无法连接，请重试。');}
    if(!response.ok)throw Error(response.status===503?'资源目录暂时无法连接，请稍后重试。':'未能读取资源信息，请重试。');
    return response.json();
  }
  const project=(provider:string,value:any):PinProject=>provider==='modrinth'
    ?{id:value.project_id||value.id,title:value.title,description:value.description||'',icon:value.icon_url||''}
    :{id:String(value.id),title:value.name,description:value.summary||'',icon:value.logo?.thumbnailUrl||''};
  const version=(provider:string,value:any):PinVersion=>provider==='modrinth'
    ?{id:value.id,name:value.version_number||value.name,games:value.game_versions||[],loaders:value.loaders||[],release:value.version_type==='release'}
    :{id:String(value.id),name:value.displayName||value.fileName,games:(value.gameVersions||[]).filter((name:string)=>/^\d/.test(name)),loaders:(value.gameVersions||[]).filter((name:string)=>/^(forge|neoforge|fabric|quilt)$/i.test(name)),release:value.releaseType===1};
  function remember(provider:string,p:PinProject,v?:PinVersion){
    projects.set(provider+'/'+p.id,p);
    if(v)releases.set(provider+'/'+v.id,v);
    labels.value[pinKey({provider,project:p.id,version:v?.id||''})]={title:p.title,version:v?.name||'按游戏版本规则跟随兼容发布',context:v?context(v):'按每游戏缓存数量准备最新兼容正式发布'};
  }
  async function search(provider:string,kind:string,query:string){
    versionController?.abort();selected.value=null;versions.value=[];versionLoading.value=false;versionError.value='';
    searchController?.abort();const controller=searchController=new AbortController();
    loading.value=true;error.value='';results.value=[];
    try{
      const value=await read(provider,provider==='modrinth'?'/search':'/mods/search',controller.signal,
        provider==='modrinth'?{query,facets:JSON.stringify([['project_type:'+kind]]),index:query?'relevance':'downloads',limit:'12'}
        :{gameId:'432',classId:String(classes[kind]),searchFilter:query,sortField:query?'1':'6',sortOrder:'desc',pageSize:'12'});
      if(controller.signal.aborted)return;
      results.value=(provider==='modrinth'?value.hits:value.data).map((item:any)=>project(provider,item));
    }catch(cause){if(!controller.signal.aborted)error.value=(cause as Error).message;}
    finally{if(searchController===controller)loading.value=false;}
  }
  async function choose(provider:string,p:PinProject){
    versionController?.abort();const controller=versionController=new AbortController();
    selected.value=p;versions.value=[];versionLoading.value=true;versionError.value='';
    try{
      const value=await read(provider,provider==='modrinth'?'/project/'+encodeURIComponent(p.id)+'/version':'/mods/'+p.id+'/files',controller.signal,
        provider==='curseforge'?{pageSize:'50'}:{});
      if(controller.signal.aborted)return;
      versions.value=(provider==='modrinth'?value:value.data).map((item:any)=>version(provider,item));
      remember(provider,p);for(const v of versions.value)remember(provider,p,v);
    }catch(cause){if(!controller.signal.aborted)versionError.value=(cause as Error).message;}
    finally{if(versionController===controller)versionLoading.value=false;}
  }
  async function describe(pins:ResourcePin[],files:any[]=[],retry=false){
    labelController?.abort();const controller=labelController=new AbortController();
    const pending=pins.filter(pin=>!labels.value[pinKey(pin)]||(retry&&labelErrors.value[pinKey(pin)]));
    describing.value=!!pending.length;
    // Only the visible page is resolved, with three requests in progress at most.
    await Promise.all(Array.from({length:Math.min(3,pending.length)},async()=>{
      while(pending.length&&!controller.signal.aborted){
        const pin=pending.shift()!;const key=pinKey(pin);
        try{
          let p=projects.get(pin.provider+'/'+pin.project);
          if(!p){
            const raw=await read(pin.provider,pin.provider==='modrinth'?'/project/'+encodeURIComponent(pin.project):'/mods/'+pin.project,controller.signal);
            p=project(pin.provider,pin.provider==='modrinth'?raw:raw.data);
            projects.set(pin.provider+'/'+pin.project,p);
          }
          if(controller.signal.aborted)return;
          let v:PinVersion|undefined;
          if(pin.version){
            const existing=releases.get(pin.provider+'/'+pin.version);
            const raw=existing?null:await read(pin.provider,pin.provider==='modrinth'?'/version/'+encodeURIComponent(pin.version):'/mods/'+pin.project+'/files/'+pin.version,controller.signal);
            v=existing||version(pin.provider,pin.provider==='modrinth'?raw:raw.data);
          }
          if(!controller.signal.aborted){remember(pin.provider,p,v);labels.value[key]=labels.value[pinKey({provider:pin.provider,project:p.id,version:pin.version})];delete labelErrors.value[key];}
        }catch{
          // Existing cached names remain useful when the upstream is unavailable.
          const file=files.find(f=>f.provider===pin.provider&&f.project===pin.project&&(!pin.version||f.version===pin.version));
          if(!controller.signal.aborted){
            labels.value[key]={title:file?.title||'资源信息暂时无法读取',version:pin.version?'已固定版本':'按游戏版本规则跟随兼容发布',context:'可重试读取名称和版本，原配置仍保留'};
            labelErrors.value[key]=true;
          }
        }
      }
    }));
    if(labelController===controller)describing.value=false;
  }
  function cancel(){searchController?.abort();versionController?.abort();loading.value=false;versionLoading.value=false;}
  function dispose(){cancel();labelController?.abort();}
  return {results,versions,selected,loading,versionLoading,error,versionError,labels,labelErrors,describing,identity,search,choose,describe,cancel,dispose};
});
