<script setup lang="ts">
import {onUnmounted,ref,watch} from 'vue'
import {useCollections} from '../stores/collections'
import {isCancelled} from '../api'
import {type SearchStatus} from '../search'
const connection=useCollections(),status=ref<SearchStatus|null>(null),error=ref(''),busy=ref(false)
let controller:AbortController|undefined,rebuildController:AbortController|undefined,timer:ReturnType<typeof setInterval>|undefined
function stop(){clearInterval(timer);controller?.abort();rebuildController?.abort();controller=undefined;rebuildController=undefined;status.value=null;busy.value=false;error.value=''}
async function load(){if(!connection.connected||controller)return;const active=controller=new AbortController()
  try{const value=await connection.searchStatus(active.signal);if(!active.signal.aborted)status.value=value;error.value=''}catch(cause){if(!isCancelled(cause)&&!active.signal.aborted)error.value=cause instanceof Error?cause.message:'读取失败'}finally{if(controller===active)controller=undefined}}
async function rebuild(){if(busy.value||!connection.connected)return;const active=rebuildController=new AbortController();busy.value=true;try{await connection.rebuildSearch(active.signal);if(!active.signal.aborted)await load()}catch(cause){if(!isCancelled(cause)&&!active.signal.aborted)error.value=cause instanceof Error?cause.message:'排队失败'}finally{if(rebuildController===active){rebuildController=undefined;busy.value=false}}}
watch(()=>connection.connected,value=>{stop();if(value){void load();timer=setInterval(load,3000)}},{immediate:true})
onUnmounted(stop)
const states:Record<string,string>={queued:'排队中',running:'处理中',succeeded:'已完成',failed:'失败'}
</script>
<style scoped>.table-scroll table{min-width:620px}th,td{white-space:nowrap}</style>
<template><header><h1>搜索索引作业</h1><p>查看正文与语义索引的处理结果；关闭页面不会中止后台任务。</p></header><section class="panel"><div class="section-heading"><h2>项目处理记录</h2><button :disabled="!connection.connected||busy" @click="rebuild">{{busy?'提交中…':'重新建立索引'}}</button></div><p class="muted">重新排队已收录项目，逐项目更新。处理期间可能暂时缺少新配置的结果。</p><p v-if="error" role="alert" class="error">{{error}}</p><p v-if="!connection.connected">请先连接管理服务。</p><p v-else-if="status&&!status.jobs.length">暂无索引作业；等待目录变更或手动重建。</p><div v-if="status" class="table-scroll"><table v-if="status.jobs.length"><thead><tr><th>项目</th><th>状态</th><th>执行节点</th><th>尝试</th><th>错误</th></tr></thead><tbody><tr v-for="job in status.jobs" :key="job.project_id"><td>{{job.project_id}}</td><td>{{states[job.state]||job.state}}</td><td>{{job.node_id||'尚未领取'}}</td><td>{{job.attempts}}</td><td>{{job.error||'—'}}</td></tr></tbody></table></div></section></template>
