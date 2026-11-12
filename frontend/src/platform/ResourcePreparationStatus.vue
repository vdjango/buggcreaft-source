<script setup lang="ts">
import {computed,onBeforeUnmount,ref} from 'vue';
import WorkspaceCardHeader from './WorkspaceCardHeader.vue';
import StorageCapacityStatus from './StorageCapacityStatus.vue';
import {bytes,filename,stamp,useMirrorWorkspaceStore} from './stores/mirrorWorkspace';
const store=useMirrorWorkspaceStore(),busy=ref(false),error=ref(''),page=ref<any>(null);
const status=computed(()=>page.value||store.data.resource_prefetch||{}),job=computed(()=>store.data.job||{});
const active=computed(()=>job.value.state==='running');
const running=computed(()=>active.value&&job.value.action==='resource_prefetch');
const reason=computed(()=>status.value.paused?'已暂停：缓存保留，点击继续后从已有进度接续。':running.value?'正在准备热门与固定资源。':status.value.requested?'已排队：当前任务完成后自动开始。':job.value.action==='resource_prefetch'&&job.value.state==='disk_limit'?'暂存空间不足：等待上传释放暂存或调整存储预算。':active.value?'当前正在处理其他资源，可安排准备任务排队。':'当前没有资源准备任务，可随时开始或重试。');
let revision=0;
onBeforeUnmount(()=>{revision++;});
async function command(action:string,offset?:number){
  const current=++revision;busy.value=true;error.value='';
  try{const value=await store.request({action,...(offset===undefined?{}:{offset})});
    if(current!==revision)return;
    if(action==='resource_files')page.value=value;else{page.value=null;store.hydrate(value);}
  }catch(cause){if(current===revision)error.value=(cause as Error).message;}finally{if(current===revision)busy.value=false;}
}
</script>
<template>
<StorageCapacityStatus/>
<section class="workspace-card">
  <WorkspaceCardHeader title="缓存准备情况" description="游戏文件由自动同步分批准备；下面可单独开始或暂停热门排行与固定项目。"><button type="button" :disabled="busy||running" @click="command('resource_prefetch_start')">{{status.paused?'继续准备':status.requested?'已排队':'准备排行与固定项'}}</button><button v-if="!status.paused" type="button" class="secondary" :disabled="busy" @click="command('resource_prefetch_pause')">暂停准备</button></WorkspaceCardHeader>
  <p class="workspace-status">{{reason}}</p><p class="workspace-hint">下次检查 {{status.paused?'已暂停':stamp(status.next_at)}} · 开始按已保存的设置执行，重复点击不会新建第二个任务。</p><p v-if="error" role="alert" class="workspace-alert">{{error}}</p>
  <div v-if="job.action==='resource_prefetch'" class="workspace-status">{{active?(job.phase==='resource_catalogue'?'正在获取官方排行…':'下载并校验中…'):job.state==='finished'?'本轮已完成':job.error||job.state==='disk_limit'&&'空间不足，等待可用容量'||'本轮已停止'}} · 发现 {{job.discovered||0}} 个文件 · 已处理 {{job.done||0}} / {{job.total||0}} · 失败 {{job.errors||0}}</div>
  <p class="workspace-hint">已就绪 {{status.ready||0}} / {{status.total||0}} · 固定 {{status.pinned||0}} · 文件合计 {{bytes(status.bytes)}} · 最近检查 {{stamp(status.checked_at)}}</p>
  <div class="workspace-table"><table><thead><tr><th>资源 / 文件</th><th>来源</th><th>大小</th><th>状态</th></tr></thead><tbody><tr v-for="file in status.files||[]" :key="file.url"><td class="file-name">{{file.title}}<small>{{filename(file.url)}}{{file.pinned?' · 固定保留':''}}</small></td><td>{{file.provider==='curseforge'?'CurseForge':'Modrinth'}}</td><td>{{bytes(file.size)}}</td><td>{{file.ready?'已缓存':'待准备'}}</td></tr><tr v-if="!status.files?.length"><td colspan="4" class="workspace-empty">还没有准备记录，保存配置后点击“立即准备”。</td></tr></tbody></table></div>
  <div class="workspace-pagination"><button v-if="status.offset" type="button" class="secondary" :disabled="busy" @click="command('resource_files',Math.max(0,status.offset-50))">上一页</button><button v-if="status.has_more" type="button" class="secondary" :disabled="busy" @click="command('resource_files',(status.offset||0)+50)">下一页</button></div>
</section>
</template>
