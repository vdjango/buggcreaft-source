import {platformFetch as fetch} from '../../transport';
import {defineStore} from 'pinia';
import {ref} from 'vue';
import {useCloudStorageStore} from './cloudStorage';
import {useRoomClientCacheStore} from './roomClientCache';
import {useSourceQualityStore} from './sourceQuality';
import {useMirrorPrefetchStore} from './mirrorPrefetch';
import {useNotificationsStore} from './notifications';

export const mirrorCategories=[['core','游戏核心'],['loaders','加载器'],['mods','模组'],['java','Java 运行时'],['libraries','依赖库'],['assets','原版资源']];
export const bytes=(n:number=0)=>n>=1024**3?(n/1024**3).toFixed(2)+' GiB':n>=1024**2?(n/1024**2).toFixed(1)+' MiB':(n/1024).toFixed(1)+' KiB';
export const stamp=(n:number)=>n?new Date(n*1000).toLocaleString('zh-CN',{hour12:false}):'—';
export const filename=(url:string)=>{try{return decodeURIComponent(url.split('/').pop()||url);}catch{return url;}};
export const useMirrorWorkspaceStore=defineStore('mirror-workspace',()=>{
  const cloud=useCloudStorageStore(),room=useRoomClientCacheStore(),quality=useSourceQualityStore(),prefetch=useMirrorPrefetchStore(),notices=useNotificationsStore();
  const data=ref<any>({}),draft=ref<any>({}),ready=ref(false),loading=ref(false),saving=ref(false),error=ref(''),unauthorized=ref(false),baseline=ref('');
  const catalogConfigured=ref(false),catalogBusy=ref(false),catalogError=ref('');
  async function catalogSettings(key?:string){
    const response=await fetch('/v1/admin/settings',{method:key===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(20000),headers:{'Content-Type':'application/json'},...(key===undefined?{}:{body:JSON.stringify({curseforge_key:key})})});
    const value=await response.json();
    if(!response.ok)throw Error(value.message||value.error||value.code||'无法保存平台凭据');
    catalogConfigured.value=Boolean(value.curseforge_key_configured);
  }
  async function loadCatalogKey(){
    try{await catalogSettings();}catch(cause){catalogError.value=(cause as Error).message;}
  }
  async function saveCatalogKey(key:string){
    if(catalogBusy.value)return false;
    catalogBusy.value=true;catalogError.value='';
    try{
      if(key.trim())await catalogSettings(key.trim());
      await request({action:'catalog_credentials'});
      await refresh();notices.notify('平台凭据已保存并同步到分发节点。');return true;
    }catch(cause){catalogError.value='保存或同步未完成：'+(cause as Error).message;return false;}
    finally{catalogBusy.value=false;}
  }
  async function request(body?:any,signal?:AbortSignal){
    const response=await fetch('/v1/admin/mirror',{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(20000)]):AbortSignal.timeout(20000),headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
    if(response.status===401){unauthorized.value=true;throw Error('管理会话已过期，请重新登录。');}
    const value=await response.json();
    signal?.throwIfAborted();
    if(!response.ok)throw Error(value.message||value.error||value.code||'暂时无法读取服务');
    return value;
  }
  function hydrate(value:any,replace=false){
    data.value=value;
    if(!ready.value||replace){draft.value=structuredClone(value.settings||{});baseline.value=JSON.stringify(draft.value);}
    cloud.hydrate(value.cloud_storage||{},value.settings||{},replace,value);
    room.hydrate(value.settings||{},replace);
    room.catalog={...room.catalog,...value.room_client_cache?.catalog};room.discovery={...room.discovery,...value.room_client_cache?.discovery};
    quality.hydrate(value,replace);prefetch.hydrate(value.settings?.prefetch_platform,replace);
    ready.value=true;
  }
  async function refresh(){
    if(loading.value)return;
    loading.value=true;
    try{hydrate(await request());error.value='';}
    catch(cause){error.value=cause instanceof Error&&['TimeoutError','AbortError'].includes(cause.name)?'连接超时，稍后自动重试；当前数据保留。':String((cause as Error).message||cause);}
    finally{loading.value=false;}
  }
  async function save(section:string){
    if(saving.value||!ready.value)return;
    const invalid=['downloads','cloud'].includes(section)&&Object.values(cloud.routeErrors).find(Boolean);
    if(invalid){notices.notify(String(invalid),true);return;}
    saving.value=true;
    try{
      const fields:Record<string,string[]>={
        downloads:['enabled','public_url','resource_prefetch','auto_refresh','prefetch_hot_limit','prefetch_category_limits'],
        runtimes:['runtime_prefetch'],
        sources:['auto_sources','source_prefer_cn','source_cleanup','source_registries'],
        storage:['refresh_minutes','concurrency','metadata_ttl','monthly_egress_gib','egress_threshold','bandwidth_mbps','max_serving','node_weight','nodes'],
      };
      const body:Record<string,unknown>={};
      for(const key of fields[section]||[])if(draft.value[key]!==undefined)body[key]=JSON.parse(JSON.stringify(draft.value[key]));
      if(section==='downloads'&&body.prefetch_category_limits)(body.prefetch_category_limits as any).loaders=data.value.settings.prefetch_category_limits.loaders;
      if(section==='downloads'&&body.resource_prefetch)(body.resource_prefetch as any).compatibility=JSON.parse(JSON.stringify(data.value.settings.resource_prefetch.compatibility));
      if(section==='runtimes')body.resource_prefetch={...JSON.parse(JSON.stringify(data.value.settings.resource_prefetch)),compatibility:JSON.parse(JSON.stringify(draft.value.resource_prefetch.compatibility))};
      const storage=cloud.payload();
      if(['downloads','room-cache'].includes(section)){
        const routes=JSON.parse(JSON.stringify(data.value.settings.qiniu_routes||{}));
        for(const [key,route] of Object.entries(storage.qiniu_routes))if(key.startsWith('room_')===(section==='room-cache'))routes[key]=route;
        body.qiniu_routes=routes;
      }
      if(section==='downloads')body.policies=storage.policies;
      if(section==='cloud')for(const key of ['quota_gib','reserve_gib','eviction','protect_latest','qiniu_profiles','qiniu_signed_url_minutes'] as const)body[key]=storage[key];
      if(section==='room-cache')Object.assign(body,room.payload());
      if(section==='quality')Object.assign(body,quality.payload());
      if(section==='downloads')body.prefetch_platform=prefetch.payload();
      if(!Object.keys(body).length)return;
      const value=await request(body);hydrate(value);
      // Update only the saved domain. Other pages retain their unsaved drafts.
      for(const key of fields[section]||[])if(key==='prefetch_category_limits')draft.value[key]={...value.settings[key],loaders:draft.value[key].loaders};else if(key==='resource_prefetch')draft.value[key]={...structuredClone(value.settings[key]),compatibility:draft.value[key].compatibility};else draft.value[key]=structuredClone(value.settings[key]);
      if(section==='runtimes')draft.value.resource_prefetch.compatibility=structuredClone(value.settings.resource_prefetch.compatibility);
      if(section==='room-cache')room.hydrate(value.settings,true);
      if(section==='quality')quality.hydrate(value,true);
      notices.notify('本页设置已保存。');
    }catch(cause){notices.notify((cause as Error).message,true);}
    finally{saving.value=false;}
  }
  return {data,draft,ready,loading,saving,error,unauthorized,baseline,request,hydrate,refresh,save,catalogConfigured,catalogBusy,catalogError,loadCatalogKey,saveCatalogKey};
});
