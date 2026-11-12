// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useMirrorWorkspaceStore} from '../../src/platform/stores/mirrorWorkspace'
import {useCloudStorageStore} from '../../src/platform/stores/cloudStorage'
import {useSourceQualityStore} from '../../src/platform/stores/sourceQuality'

vi.mock('../../src/transport',()=>({platformFetch:(input:any,init:any)=>globalThis.fetch(input,init)}))
beforeEach(()=>setActivePinia(createPinia()))
it('saves only source settings and preserves unsaved quality/cloud drafts',async()=>{
  const settings={source_registries:[],auto_sources:false,source_prefer_cn:true,source_cleanup:false,qiniu_routes:{},quality_daily_mib:64}
  const workspace=useMirrorWorkspaceStore()
  workspace.hydrate({settings})
  workspace.draft.auto_sources=true
  useSourceQualityStore().settings.quality_daily_mib=123
  useCloudStorageStore().local.quota_gib=987
  const request=vi.fn(async(_input,init)=>{
    const body=JSON.parse(init.body)
    expect(Object.keys(body).sort()).toEqual(['auto_sources','source_cleanup','source_prefer_cn','source_registries'])
    return new Response(JSON.stringify({settings:{...settings,...body}}))
  })
  vi.stubGlobal('fetch',request)
  await workspace.save('sources')
  expect(request).toHaveBeenCalledOnce()
  expect(useSourceQualityStore().settings.quality_daily_mib).toBe(123)
  expect(useCloudStorageStore().local.quota_gib).toBe(987)
  vi.unstubAllGlobals()
})
it('does not submit a fake save for CDN instructions',async()=>{
  const workspace=useMirrorWorkspaceStore();workspace.hydrate({settings:{}})
  const request=vi.fn();vi.stubGlobal('fetch',request)
  await workspace.save('cloudflare')
  expect(request).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})
