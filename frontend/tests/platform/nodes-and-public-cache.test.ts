// @vitest-environment jsdom
vi.mock('../../src/transport',()=>({platformFetch:(input:RequestInfo|URL,init?:RequestInit)=>globalThis.fetch(input,init)}));
import {afterEach,expect,it,vi} from 'vitest';
import {createApp,nextTick,reactive,type App} from 'vue';
import {createPinia} from 'pinia';
import Nodes from '../../src/platform/pages/admin-nodes.vue';
import Mirror from '../../src/platform/pages/mirror.vue';

let app:App|undefined;
afterEach(()=>{app?.unmount();app=undefined;document.body.replaceChildren();vi.unstubAllGlobals();vi.useRealTimers();});
async function settle(){for(let i=0;i<12;i++)await nextTick();}
function mount(component:any,props:any){const root=document.createElement('div');document.body.append(root);app=createApp(component,props);app.use(createPinia());app.mount(root);return root;}

it('loads nodes when entering from an authenticated SPA and stops polling after leaving',async()=>{
  vi.useFakeTimers();
  const fetch=vi.fn(async(url:string)=>({ok:true,json:async()=>({nodes:url.includes('room-cache')?[]:[{id:'edge',name:'测试节点',state:'active',online:true}]})}));
  vi.stubGlobal('fetch',fetch);
  const views={access:reactive({authenticated:true,visible:false}),adminAccess:{mount:vi.fn(),authenticated:vi.fn()}};
  const root=mount(Nodes,{seed:{authMode:'login'},views});await settle();
  expect(root.textContent).toContain('测试节点');
  expect(fetch).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(10000);await settle();expect(fetch).toHaveBeenCalledTimes(4);
  app!.unmount();app=undefined;
  await vi.advanceTimersByTimeAsync(20000);expect(fetch).toHaveBeenCalledTimes(4);
});

it('uses unique total cache bytes and never substitutes old alias counts',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({running:true,cached_files:15516,cached_bytes:0,content_cache:{files:8123,bytes:3*1024**3},source_mappings:2321})})));
  const root=mount(Mirror,{seed:{publicMode:'mirror'}});await settle();
  const metrics=root.querySelector('.source-status')!.textContent!;
  expect(metrics).toContain('8,123');expect(metrics).toContain('3.00 GiB');
  expect(metrics).not.toContain('15,516');expect(metrics).not.toContain('本地缓存大小');
});
