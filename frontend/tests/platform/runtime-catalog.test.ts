// @vitest-environment jsdom
vi.mock('../../src/transport',()=>({platformFetch:(input:RequestInfo|URL,init?:RequestInit)=>globalThis.fetch(input,init)}));
import {afterEach,expect,it,vi} from 'vitest';
import {createApp,nextTick,type App} from 'vue';
import {createPinia,setActivePinia} from 'pinia';
import {createRouter,createMemoryHistory} from 'vue-router';
import RuntimeCatalog from '../../src/platform/RuntimeCatalog.vue';
import {useMirrorWorkspaceStore} from '../../src/platform/stores/mirrorWorkspace';
let app:App|undefined;
afterEach(()=>{app?.unmount();document.body.replaceChildren();vi.unstubAllGlobals();});
async function settle(){for(let i=0;i<15;i++)await nextTick();}

it('marks and restores one release with a reason, using the shared endpoint and canceling page reads',async()=>{
 const pinia=createPinia();setActivePinia(pinia);const store=useMirrorWorkspaceStore();store.ready=true;
 const file={family:'sodium',version:'0.9.2+mc26.3',path:'/mods/core.jar',url:'https://cdn.modrinth.com/core.jar',blocked:false,ready:true};
 const requests:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_url:string,init:RequestInit)=>{const body=JSON.parse(String(init.body));requests.push({body,signal:init.signal});return {ok:true,status:200,json:async()=>body.action==='runtime_files'?{families:{sodium:'Sodium'},files:[file],blocks:[],total:1}:{ok:true}};}));
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/admin/mirror/runtimes',component:RuntimeCatalog}]});await router.push('/admin/mirror/runtimes');
 const root=document.createElement('div');document.body.append(root);app=createApp(RuntimeCatalog);app.use(pinia);app.use(router);app.mount(root);await settle();
 expect(root.textContent).toContain('Sodium');
 const button=[...root.querySelectorAll('button')].find(b=>b.textContent==='标记问题')!;button.click();await settle();
 const drawer=document.querySelector('.workspace-drawer')||document.body;
 const stop=[...drawer.querySelectorAll('button')].find(b=>b.textContent==='停止分发')!;
 expect(stop.disabled).toBe(true);
 const reason=drawer.querySelector('textarea')!;reason.value='测试：核心兼容问题';reason.dispatchEvent(new Event('input',{bubbles:true}));await settle();stop.click();await settle();
 expect(requests.find(r=>r.body.action==='runtime_mark')?.body).toMatchObject({family:'sodium',version:'0.9.2+mc26.3',blocked:true,reason:'测试：核心兼容问题'});
 expect(root.textContent).toContain('已停止分发');
 app.unmount();app=undefined;expect(requests[0].signal.aborted).toBe(true);
});

it('saves compatibility rules once without overwriting other page drafts',async()=>{
 setActivePinia(createPinia());const store=useMirrorWorkspaceStore();
 const settings={runtime_prefetch:{enabled:false,cache_loaders:true,cores:['iris'],refresh_hours:24},resource_prefetch:{limit:10,compatibility:{mode:'recent',recent:3,per_game:5,games:[],overrides:{}}},prefetch_category_limits:{core:10,loaders:5},qiniu_routes:{},policies:{},prefetch_platform:['windows']};
 store.hydrate({settings});store.draft.resource_prefetch.compatibility.per_game=7;store.draft.prefetch_category_limits.core=12;
 const bodies:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_url:string,init:RequestInit)=>{const body=JSON.parse(String(init.body));bodies.push(body);return {ok:true,status:200,json:async()=>({settings:{...settings,...body}})};}));
 await store.save('downloads');expect(bodies[0].resource_prefetch.compatibility.per_game).toBe(5);expect(store.draft.resource_prefetch.compatibility.per_game).toBe(7);
 store.draft.resource_prefetch.limit=50;
 await store.save('runtimes');expect(bodies[1].resource_prefetch.limit).toBe(10);expect(bodies[1].resource_prefetch.compatibility.per_game).toBe(7);expect(store.draft.resource_prefetch.limit).toBe(50);
});
