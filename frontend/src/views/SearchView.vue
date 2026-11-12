<script setup lang="ts">
import {computed,onMounted,onUnmounted,reactive,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useSearch} from '../stores/search'
import {request,isCancelled} from '../api'
import {searchParameters,type SearchFilters,type SearchCapabilities,type SearchItem,type SearchPage} from '../search'
import SearchResults from '../components/SearchResults.vue'
const route=useRoute(),router=useRouter(),store=useSearch()
const filters=reactive<SearchFilters>({q:'',mode:'hybrid',resource_type:'',source:'',mc_version:'',loader:'',language:'zh-Hans'})
const capabilities=ref<SearchCapabilities|null>(null),similar=ref<SearchPage|null>(null),similarTitle=ref(''),similarError=ref(''),similarBusy=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined,similarController:AbortController|undefined
const capabilityController=new AbortController()
const semanticHint=computed(()=>capabilities.value?.semantic===false?'相近含义搜索暂不可用；综合模式将显示文字匹配结果。':'同时检索已收录的原文与译文正文。')
function cancelSimilar(){similarController?.abort();similarController=undefined;similar.value=null;similarTitle.value='';similarError.value='';similarBusy.value=false}
function readRoute(){
  clearTimeout(timer);cancelSimilar()
  for(const key of Object.keys(filters) as (keyof SearchFilters)[]){const value=route.query[key];if(typeof value==='string')(filters as Record<string,string>)[key]=value;else (filters as Record<string,string>)[key]=key==='mode'?'hybrid':key==='language'?'zh-Hans':''}
  void store.load({...filters},typeof route.query.cursor==='string'?route.query.cursor:undefined)
}
function submit(cursor?:string){
  clearTimeout(timer);cancelSimilar()
  const query=Object.fromEntries(new URLSearchParams(searchParameters(filters,cursor)))
  if(new URLSearchParams(query).toString()===new URLSearchParams(route.query as Record<string,string>).toString())void store.load({...filters},cursor)
  else void router.push({path:'/search',query})
}
function changed(){clearTimeout(timer);store.cancel();cancelSimilar();timer=setTimeout(()=>submit(),300)}
async function loadSimilar(item:SearchItem){
  cancelSimilar();const controller=similarController=new AbortController();similarTitle.value=item.translated_title||item.project_title;similarBusy.value=true
  try{const value=await request<SearchPage>('',`/projects/${encodeURIComponent(item.project_id)}/similar`,undefined,controller.signal);if(similarController===controller)similar.value=value}
  catch(cause){if(similarController===controller&&!isCancelled(cause))similarError.value='功能相近搜索暂不可用，请稍后重试。'}
  finally{if(similarController===controller)similarBusy.value=false}
}
watch(()=>route.fullPath,readRoute)
onMounted(()=>{readRoute();void request<SearchCapabilities>('','/search/capabilities',undefined,capabilityController.signal).then(value=>{if(!capabilityController.signal.aborted)capabilities.value=value}).catch(()=>{})})
onUnmounted(()=>{clearTimeout(timer);store.cancel();cancelSimilar();capabilityController.abort()})
</script>
<template>
  <header class="search-heading"><p class="eyebrow">BUGG RESOURCE SEARCH</p><h1>发现想玩的资源</h1><p>搜索名称、简介和正文，也可以描述你想实现的功能。</p></header>
  <section class="panel search-form"><form @submit.prevent="submit()">
    <div class="search-query"><label>查找资源<input v-model="filters.q" maxlength="200" placeholder="例如：让箱子里的物品自动分类" @input="changed"></label><button :disabled="store.busy">{{store.busy?'搜索中…':'搜索'}}</button></div>
    <div class="search-filters">
      <label>搜索方式<select v-model="filters.mode" @change="submit()"><option value="hybrid">综合搜索</option><option value="lexical">文字匹配</option><option value="semantic">相近含义</option></select></label>
      <label>资源类型<select v-model="filters.resource_type" @change="submit()"><option value="">全部资源</option><option value="mod">模组</option><option value="modpack">整合包</option><option value="resourcepack">资源包</option><option value="shader">光影</option></select></label>
      <label>来源<select v-model="filters.source" @change="submit()"><option value="">全部来源</option><option value="modrinth">Modrinth</option><option value="curseforge">CurseForge</option></select></label>
      <label>游戏版本<input v-model="filters.mc_version" maxlength="80" placeholder="如 1.21.1" @change="submit()"></label>
      <label>加载器<select v-model="filters.loader" @change="submit()"><option value="">不限加载器</option><option value="fabric">Fabric</option><option value="forge">Forge</option><option value="neoforge">NeoForge</option><option value="quilt">Quilt</option></select></label>
    </div><p class="muted search-help">{{semanticHint}}</p>
  </form></section>
  <p v-if="store.error" role="alert" class="panel error">{{store.error}} <button class="secondary" @click="submit()">重试</button></p>
  <p v-if="store.busy" role="status">正在查找资源…</p>
  <template v-if="store.page">
    <p v-if="store.page.degraded" role="status" class="search-notice">相近含义搜索暂不可用，已显示文字匹配结果。</p>
    <div class="section-heading"><h2>搜索结果</h2><small>{{store.page.effective_mode==='semantic'?'相近含义':store.page.effective_mode==='lexical'?'文字匹配':'综合搜索'}} · 已收录资源</small></div>
    <p v-if="!store.page.items.length" class="panel">没有找到符合条件的已收录资源。试试其他关键词或放宽筛选。</p>
    <SearchResults :items="store.page.items" @similar="loadSimilar"/>
    <div class="pagination"><button v-if="store.page.next_cursor" @click="submit(store.page.next_cursor||undefined)">下一页</button><button v-if="route.query.cursor" class="secondary" @click="submit()">返回第一页</button><small v-if="store.page.candidate_window_exhausted">结果窗口已达上限，可增加关键词缩小范围。</small></div>
  </template>
  <section v-if="similarTitle" class="similar-section"><h2>与「{{similarTitle}}」功能相近</h2><p class="muted">相关正文描述的功能相近，不表示可以兼容替换。</p><p v-if="similarBusy" role="status">正在查找…</p><p v-if="similarError" role="alert" class="error">{{similarError}}</p><p v-if="similar&&!similar.items.length">没有找到已收录的相近资源。</p><SearchResults v-if="similar" :items="similar.items" @similar="loadSimilar"/></section>
</template>
