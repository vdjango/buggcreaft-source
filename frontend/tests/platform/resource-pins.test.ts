// @vitest-environment jsdom
import {beforeEach,afterEach,expect,it,vi} from 'vitest';
vi.mock('../../src/transport',()=>({platformFetch:(input:RequestInfo|URL,init?:RequestInit)=>globalThis.fetch(input,init)}));
import {createApp,nextTick} from 'vue';
import {createPinia,setActivePinia} from 'pinia';
import ResourcePrefetchSettings from '../../src/platform/ResourcePrefetchSettings.vue';
import {useResourcePinsStore,pinKey} from '../../src/platform/stores/resourcePins';
import {useMirrorWorkspaceStore} from '../../src/platform/stores/mirrorWorkspace';

const pin={provider:'modrinth',project:'opaque-project',version:'opaque-file'};
const p={id:pin.project,title:'Iris Shaders',description:'Shader support',icon_url:''};
const v={id:pin.version,version_number:'1.8.8',game_versions:['1.21.1'],loaders:['fabric'],version_type:'release'};
const response=(data:any,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
const settle=async()=>{await new Promise(resolve=>setTimeout(resolve,0));await nextTick();};
beforeEach(()=>setActivePinia(createPinia()));
afterEach(()=>vi.unstubAllGlobals());

it('uses one catalog gateway and official kind filters for both providers',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce(response({hits:[{...p,project_id:p.id}]})).mockResolvedValueOnce(response({data:[{id:42,name:'Iris',summary:'core'}]}));
  vi.stubGlobal('fetch',fetch);const store=useResourcePinsStore();
  await store.search('modrinth','shader','Iris');
  let url=new URL(fetch.mock.calls[0][0],'https://source.test');
  expect(url.pathname).toBe('/v1/catalog/modrinth/search');
  expect(JSON.parse(url.searchParams.get('facets')!)).toEqual([['project_type:shader']]);
  expect(store.results[0].title).toBe('Iris Shaders');
  await store.search('curseforge','resourcepack','Faithful');
  url=new URL(fetch.mock.calls[1][0],'https://source.test');
  expect(url.pathname).toBe('/v1/catalog/curseforge/mods/search');expect(url.searchParams.get('classId')).toBe('12');
  expect(store.results[0]).toMatchObject({id:'42',title:'Iris'});
});

it('cancels late version and search responses after switching source or closing',async()=>{
  let finishVersion!:(value:Response)=>void,finishSearch!:(value:Response)=>void;
  const fetch=vi.fn().mockImplementationOnce(()=>new Promise(resolve=>finishVersion=resolve)).mockImplementationOnce(()=>new Promise(resolve=>finishSearch=resolve)).mockResolvedValueOnce(response({data:[]}));
  vi.stubGlobal('fetch',fetch);const store=useResourcePinsStore();
  const pendingVersion=store.choose('modrinth',{id:p.id,title:p.title,description:'',icon:''});
  const pendingSearch=store.search('modrinth','mod','Iris');
  await store.search('curseforge','mod','Iris');
  finishVersion(response([v]));finishSearch(response({hits:[p]}));await Promise.all([pendingVersion,pendingSearch]);
  expect(store.selected).toBeNull();expect(store.versions).toEqual([]);expect(store.results).toEqual([]);
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);expect(fetch.mock.calls[1][1].signal.aborted).toBe(true);
  store.cancel();expect(fetch.mock.calls[2][1].signal.aborted).toBe(true);
});

it('resolves old IDs once, shows real version and loader, and reuses metadata',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce(response(p)).mockResolvedValueOnce(response(v));vi.stubGlobal('fetch',fetch);
  const store=useResourcePinsStore();await store.describe([pin]);await store.describe([pin]);
  expect(store.labels[pinKey(pin)]).toEqual({title:'Iris Shaders',version:'1.8.8',context:'1.21.1 · fabric'});
  expect(fetch).toHaveBeenCalledTimes(2);
  await store.describe([{...pin,version:''}]);expect(fetch).toHaveBeenCalledTimes(2);
  expect(store.labels[pinKey({...pin,version:''})].version).toBe('按游戏版本规则跟随兼容发布');
});

it('keeps cached names on catalog failure and allows retry without changing the pin',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce(response({},503)).mockResolvedValueOnce(response(p)).mockResolvedValueOnce(response(v));vi.stubGlobal('fetch',fetch);
  const store=useResourcePinsStore();const original=JSON.stringify(pin);
  await store.describe([pin],[{...pin,title:'Iris Shaders'}]);expect(store.labels[pinKey(pin)].title).toBe('Iris Shaders');
  expect(store.labelErrors[pinKey(pin)]).toBe(true);
  await store.describe([pin],[],true);expect(store.labels[pinKey(pin)].version).toBe('1.8.8');expect(store.labelErrors[pinKey(pin)]).toBeUndefined();expect(JSON.stringify(pin)).toBe(original);
});

it('recognizes a legacy short name and the selected official project as the same pin',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(response(p)).mockResolvedValueOnce(response(v)));
  const store=useResourcePinsStore(),legacy={...pin,project:'iris'};
  await store.describe([legacy]);
  expect(store.identity(legacy)).toBe(store.identity(pin));
  expect(store.labels[pinKey(legacy)].title).toBe('Iris Shaders');
  expect(legacy.project).toBe('iris');
});

it('adds by name and readable version, prevents duplicates, and preserves drafts across polling',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL)=>String(input).includes('/search?')?response({hits:[{...p,project_id:p.id}]}):String(input).includes('/version?')?response([v]):response(p)));
  const store=useMirrorWorkspaceStore(),catalog=useResourcePinsStore();
  const data={settings:{resource_prefetch:{enabled:false,limit:0,refresh_hours:24,types:['mod'],providers:['modrinth'],pins:[]}},resource_prefetch:{files:[]}};
  store.hydrate(data);
  const host=document.createElement('div');document.body.append(host);
  const app=createApp(ResourcePrefetchSettings);app.use((await import('pinia')).getActivePinia()!);app.component('RouterLink',{template:'<a><slot/></a>'});app.mount(host);
  const button=(text:string,scope:ParentNode=document)=>[...scope.querySelectorAll<HTMLButtonElement>('button')].find(node=>node.textContent?.trim()===text)!;
  try{
    button('添加固定资源',host).click();await settle();expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Iris Shaders');
    button('Iris ShadersShader support›').click();await settle();
    const select=document.querySelector<HTMLSelectElement>('[role="dialog"] select')!;
    expect(select.textContent).toContain('1.8.8 · 1.21.1 · fabric');select.value=v.id;select.dispatchEvent(new Event('change'));await nextTick();
    button('添加到固定缓存').click();await settle();
    expect(store.draft.resource_prefetch.pins).toEqual([pin]);expect(host.textContent).toContain('Iris Shaders');expect(host.textContent).toContain('1.8.8');
    expect(host.textContent).not.toContain(pin.project);expect(host.textContent).not.toContain(pin.version);
    store.hydrate(data);expect(store.draft.resource_prefetch.pins).toEqual([pin]);
    // The same exact pin cannot be added twice; existing configuration stays intact.
    button('添加固定资源',host).click();await settle();button('Iris ShadersShader support›').click();await settle();
    const again=document.querySelector<HTMLSelectElement>('[role="dialog"] select')!;again.value=v.id;again.dispatchEvent(new Event('change'));await nextTick();
    button('添加到固定缓存').click();await settle();expect(store.draft.resource_prefetch.pins).toHaveLength(1);expect(host.textContent).toContain('此资源版本已经在固定缓存中');
    expect(catalog.labels[pinKey(pin)].title).toBe(p.title);
  }finally{app.unmount();host.remove();}
});


it('saves catalogue secrets only to the authenticated server and retries node sync without retyping',async()=>{
  const fetch=vi.fn().mockImplementation(async(input,init)=>{
    const path=String(input);
    if(path==='/v1/admin/settings')return response({curseforge_key_configured:true});
    if(path==='/v1/admin/mirror'&&init?.method==='POST')return response({configured:true});
    return response({settings:{resource_prefetch:{pins:[]}}});
  });
  vi.stubGlobal('fetch',fetch);const store=useMirrorWorkspaceStore();
  expect(await store.saveCatalogKey(' private-catalog-key ')).toBe(true);
  const posted=fetch.mock.calls.find(call=>String(call[0])==='/v1/admin/settings');
  expect(JSON.parse(posted![1].body)).toEqual({curseforge_key:'private-catalog-key'});
  const sync=fetch.mock.calls.find(call=>String(call[0])==='/v1/admin/mirror'&&call[1]?.method==='POST');
  expect(JSON.parse(sync![1].body)).toEqual({action:'catalog_credentials'});
  expect(store.catalogConfigured).toBe(true);
  fetch.mockClear();expect(await store.saveCatalogKey('')).toBe(true);
  expect(fetch.mock.calls.some(call=>String(call[0])==='/v1/admin/settings')).toBe(false);
});
