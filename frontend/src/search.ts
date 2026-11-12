import {request} from './api'

export interface SearchFilters {q:string; mode:'hybrid'|'lexical'|'semantic'; resource_type:string; source:string; mc_version:string; loader:string; language:string}
export interface SearchSnippet {text:string; highlights:{start:number;end:number}[]; field:string; language:string; is_translation:boolean; preview?:boolean}
export interface SearchItem {project_id:string; project_title:string; translated_title:string|null; resource_type:string; resource:{provider:string;project_id:string}; source_url:string; match_types:string[]; coverage:{truncated:boolean;body_available:boolean}; snippets:SearchSnippet[]}
export interface SearchPage {items:SearchItem[]; next_cursor:string|null; has_more:boolean; effective_mode:string; degraded:boolean; degradation_reason:string|null; candidate_window_exhausted:boolean}
export interface SearchStatus {projects:number;lexical_ready:number;semantic_ready:number;cursor:number;watermark:number;epoch:string;updated_at:string;model_revision:string|null;jobs:{project_id:string;revision:number;state:string;attempts:number;node_id:string|null;error:string|null;updated_at:string}[]}
export interface SearchCapabilities {lexical:boolean;semantic:boolean;reason?:string;indexed_projects?:number;semantic_projects?:number}

export function searchParameters(filters:SearchFilters,cursor?:string):string {
  const params=new URLSearchParams()
  for(const [key,value] of Object.entries(filters))if(value)params.set(key,value)
  if(cursor)params.set('cursor',cursor)
  return params.toString()
}
export function searchResources(filters:SearchFilters,cursor:string|undefined,signal:AbortSignal){return request<SearchPage>('',`/search?${searchParameters(filters,cursor)}`,undefined,signal)}
export function highlightParts(snippet:SearchSnippet):{text:string;marked:boolean}[]{
  const characters=Array.from(snippet.text),parts:{text:string;marked:boolean}[]=[]
  let offset=0
  for(const range of snippet.highlights){
    const start=Math.max(offset,range.start),end=Math.min(characters.length,range.end)
    if(end<=start)continue
    if(start>offset)parts.push({text:characters.slice(offset,start).join(''),marked:false})
    parts.push({text:characters.slice(start,end).join(''),marked:true});offset=end
  }
  if(offset<characters.length)parts.push({text:characters.slice(offset).join(''),marked:false})
  return parts
}
