import {beforeEach,describe,expect,it,vi} from 'vitest'
import {createPinia,setActivePinia} from 'pinia'
import {useSearch} from './search'
import {highlightParts,searchParameters,type SearchFilters,type SearchPage} from '../search'
import {http} from '../api'
const filters:SearchFilters={q:'铜制漏斗',mode:'hybrid',source:'',resource_type:'',loader:'',mc_version:'',language:'zh-Hans'}
const page=(name:string)=>({items:[],next_cursor:null,has_more:false,effective_mode:name,degraded:false,degradation_reason:null,candidate_window_exhausted:false}) satisfies SearchPage
describe('resource search lifecycle',()=>{
  beforeEach(()=>{setActivePinia(createPinia());vi.restoreAllMocks()})
  it('keeps Unicode code point highlights, including emoji, in text nodes',()=>{
    expect(highlightParts({text:'😀铜制漏斗',highlights:[{start:1,end:3}],field:'body',language:'zh-Hans',is_translation:false})).toEqual([{text:'😀',marked:false},{text:'铜制',marked:true},{text:'漏斗',marked:false}])
  })
  it('shares all query filters and cursor without credentials in the URL',()=>{
    const params=new URLSearchParams(searchParameters({...filters,mc_version:'1.21',loader:'fabric'},'opaque.1'))
    expect(params.get('mc_version')).toBe('1.21');expect(params.get('loader')).toBe('fabric');expect(params.get('cursor')).toBe('opaque.1');expect(params.has('token')).toBe(false)
  })
  it('aborts prior queries and rejects a late response',async()=>{
    const pending:{resolve:(value:unknown)=>void;signal?:AbortSignal}[]=[]
    vi.spyOn(http,'request').mockImplementation(config=>new Promise(resolve=>{pending.push({resolve,signal:config.signal as AbortSignal})}) as never)
    const store=useSearch(),first=store.load(filters),second=store.load({...filters,q:'月辉合金'})
    expect(pending[0]!.signal!.aborted).toBe(true)
    pending[1]!.resolve({data:page('new')});await second
    pending[0]!.resolve({data:page('old')});await first
    expect(store.page?.effective_mode).toBe('new')
    const third=store.load(filters);store.cancel();pending[2]!.resolve({data:page('cancelled')});await third
    expect(store.page).toBeNull();expect(store.busy).toBe(false)
  })
})
