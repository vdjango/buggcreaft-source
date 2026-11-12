<script setup lang="ts">
import {onUnmounted,ref,watch} from 'vue'
import {useCollections} from '../stores/collections'
import {isCancelled} from '../api'
import {type SearchStatus} from '../search'
const connection=useCollections(),status=ref<SearchStatus|null>(null),error=ref('')
let controller:AbortController|undefined
async function load(){controller?.abort();status.value=null;error.value='';if(!connection.connected)return;const active=controller=new AbortController()
  try{const value=await connection.searchStatus(active.signal);if(!active.signal.aborted)status.value=value}catch(cause){if(!active.signal.aborted&&!isCancelled(cause))error.value=cause instanceof Error?cause.message:'读取失败'}}
watch(()=>connection.connected,load,{immediate:true})
onUnmounted(()=>controller?.abort())
</script>
<template><header><h1>搜索索引状态</h1><p>当前覆盖与目录同步水位。作业操作请进入「搜索索引作业」。</p></header><section class="panel"><div class="section-heading"><h2>索引覆盖</h2><button class="secondary" :disabled="!connection.connected" @click="load">刷新</button></div><p v-if="error" role="alert" class="error">{{error}}</p><p v-if="!connection.connected">请先连接管理服务。</p><template v-if="status"><dl class="metrics"><div><dt>已同步项目</dt><dd>{{status.projects}}</dd></div><div><dt>全文就绪</dt><dd>{{status.lexical_ready}}</dd></div><div><dt>语义就绪</dt><dd>{{status.semantic_ready}}</dd></div><div><dt>事件水位 / 目录水位</dt><dd>{{status.cursor}} / {{status.watermark}}</dd></div></dl><p class="muted">就绪数表示当前索引配置的项目数；不代表上游全站覆盖。</p><dl class="search-diagnostics"><dt>目录代次</dt><dd>{{status.epoch||'尚未连接'}}</dd><dt>模型空间</dt><dd>{{status.model_revision||'未配置语义模型'}}</dd><dt>最近目录同步</dt><dd>{{status.updated_at}}</dd></dl></template></section></template>
