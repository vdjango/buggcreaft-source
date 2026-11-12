import {ref} from 'vue'
import {defineStore} from 'pinia'
import {isCancelled} from '../api'
import {searchResources,type SearchFilters,type SearchPage} from '../search'

export const useSearch=defineStore('resourceSearch',()=>{
  const page=ref<SearchPage|null>(null),busy=ref(false),error=ref('')
  let controller:AbortController|undefined,sequence=0
  function cancel(){sequence++;controller?.abort();controller=undefined;busy.value=false}
  async function load(filters:SearchFilters,cursor?:string){
    cancel();const current=++sequence,active=controller=new AbortController()
    busy.value=true;error.value='';page.value=null
    try {const result=await searchResources({...filters},cursor,active.signal)
      if(current===sequence&&!active.signal.aborted)page.value=result
    }catch(cause){if(current===sequence&&!isCancelled(cause))error.value=cause instanceof Error?cause.message:'搜索失败'}
    finally{if(current===sequence){busy.value=false;controller=undefined}}
  }
  return {page,busy,error,load,cancel}
})
