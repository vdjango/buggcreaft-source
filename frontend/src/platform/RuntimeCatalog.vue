<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue';
import {useMirrorWorkspaceStore,bytes,stamp,filename} from './stores/mirrorWorkspace';
import WorkspaceTabs from './WorkspaceTabs.vue';
import WorkspaceCardHeader from './WorkspaceCardHeader.vue';
import WorkspaceDrawer from './WorkspaceDrawer.vue';
import GameCompatibilitySettings from './GameCompatibilitySettings.vue';
const store=useMirrorWorkspaceStore();
const page=ref<any>({files:[],blocks:[],families:{}}),family=ref(''),query=ref(''),offset=ref(0),busy=ref(false),changing=ref(false),error=ref(''),notice=ref('');
const selected=ref<any>(null),reason=ref(''),manualFamily=ref('neoforge'),manualVersion=ref('');
const settings=computed(()=>store.draft.runtime_prefetch);
let read:AbortController|undefined,revision=0;
async function load(reset=false){
  if(reset)offset.value=0;
  const current=++revision;read?.abort();const controller=read=new AbortController();busy.value=true;error.value='';
  try{const value=await store.request({action:'runtime_files',family:family.value,query:query.value,offset:offset.value},controller.signal);if(current===revision)page.value=value;}
  catch(e){if(!controller.signal.aborted)error.value=(e as Error).message;}
  finally{if(current===revision)busy.value=false;}
}
function move(delta:number){offset.value=Math.max(0,offset.value+delta*50);void load();}
function select(file:any){selected.value=file;reason.value=file.reason||'';error.value='';}
async function mark(blocked:boolean){
  if(changing.value||!selected.value)return;
  changing.value=true;error.value='';
  try{await store.request({action:'runtime_mark',family:selected.value.family,version:selected.value.version,blocked,reason:reason.value});selected.value=null;notice.value=blocked?'已停止分发；问题文件清理与可用版本补位已排队。':'已恢复分发；已清理的文件会重新缓存。';await load();}
  catch(e){error.value=(e as Error).message;}
  finally{changing.value=false;}
}
async function prepare(){
  if(changing.value)return;
  changing.value=true;error.value='';
  try{await store.request({action:'runtime_prepare'});notice.value='目录更新与缓存任务已排队，执行进度在同步任务页查看。';}
  catch(e){error.value=(e as Error).message;}
  finally{changing.value=false;}
}
function manual(){select({family:manualFamily.value,version:manualVersion.value,reason:''});}
watch(()=>store.ready,ready=>{if(ready)void load();},{immediate:true});
watch(()=>store.data.runtime_catalog?.checked_at,()=>{if(!busy.value)void load();});
onUnmounted(()=>{revision++;read?.abort();});
</script>
<template>
  <WorkspaceTabs :items="[['versions','版本目录'],['cache','缓存规则'],['blocked','停供记录']]" v-slot="{active}">
    <p v-if="error" class="workspace-alert" role="alert">{{error}}</p>
    <p v-if="notice" class="workspace-hint" role="status">{{notice}}</p>
    <section v-if="active==='versions'" class="workspace-card">
      <WorkspaceCardHeader title="版本目录" description="加载器和光影核心统一管理，问题版本可立即停供。"><button type="button" :disabled="changing" @click="prepare">更新目录并准备缓存</button><RouterLink to="/admin/mirror/tasks">查看同步任务 →</RouterLink></WorkspaceCardHeader>
      <form class="workspace-toolbar" @submit.prevent="load(true)"><select v-model="family" aria-label="加载器或核心"><option value="">全部类型</option><option v-for="(label,key) in page.families" :value="key" :key="key">{{label}}</option></select><input v-model="query" placeholder="搜索版本、游戏版本" aria-label="搜索版本"><button type="submit" class="secondary" :disabled="busy">查询</button></form>
      <div class="workspace-table" :aria-busy="busy"><table><thead><tr><th>类型 / 版本</th><th>适用范围</th><th>缓存</th><th>分发状态</th><th></th></tr></thead><tbody>
        <tr v-for="file in page.files" :key="file.url"><td><strong>{{page.families[file.family]}} · {{file.version}}</strong><small :title="file.path">{{filename(file.path)}}</small></td><td>{{file.game||'见发布方兼容说明'}}<small>{{file.loaders}}</small></td><td>{{file.ready?'已就绪':'未缓存'}}<small>{{file.size?bytes(file.size):'大小待核验'}}</small></td><td><span class="workspace-pill" :class="{warning:file.blocked}">{{file.blocked?'已停止分发':'允许分发'}}</span><small v-if="file.reason">{{file.reason}}</small></td><td><button type="button" class="text-action" @click="select(file)">{{file.blocked?'恢复 / 详情':'标记问题'}}</button></td></tr>
        <tr v-if="!page.files.length"><td colspan="5" class="workspace-empty">{{busy?'正在读取版本…':'暂无匹配版本，可更新目录或调整筛选。'}}</td></tr>
      </tbody></table></div>
      <footer class="workspace-pagination"><span>共 {{page.total||0}} 个文件 · 第 {{offset/50+1}} 页</span><button type="button" class="secondary" :disabled="busy||!offset" @click="move(-1)">上一页</button><button type="button" class="secondary" :disabled="busy||!page.has_more" @click="move(1)">下一页</button></footer>
      <p class="workspace-hint">停供同时拦截本站缓存和回源跳转，并从版本候选中排除。已有玩家本地文件、已发出的外部直链和正在传输的请求无法撤回。</p>
    </section>
    <section v-else-if="active==='cache'&&settings" class="workspace-card">
      <WorkspaceCardHeader title="主动缓存规则" description="与其他资源共用下载队列、存储和容量预算，不另存一份。"/>
      <label class="workspace-check"><input v-model="settings.enabled" type="checkbox">定期更新目录并缓存</label>
      <label class="workspace-check"><input v-model="settings.cache_loaders" type="checkbox">准备 Fabric、Quilt、Forge、NeoForge 加载器</label>
      <GameCompatibilitySettings v-if="store.draft.resource_prefetch?.compatibility"/>
      <div class="workspace-form-grid"><label>加载器与核心更新间隔 / 小时<input v-model.number="settings.refresh_hours" type="number" min="1" max="720"></label></div>
      <div class="workspace-inline-stats"><label v-for="key in ['iris','sodium','oculus','embeddium']" :key="key" class="workspace-check"><input v-model="settings.cores" type="checkbox" :value="key">{{page.families[key]||key}}</label></div>
      <p class="workspace-hint">Sodium、Embeddium 为核心依赖。实际下载仍遵守“下载策略”里的加载器/模组分发规则；跳转或禁用的类别不会主动缓存。保存后可在版本目录手动排队。</p>
      <footer class="workspace-save"><span>兼容范围只在这里设置，热门资源页直接复用。</span><button type="button" :disabled="store.saving" @click="store.save('runtimes')">{{store.saving?'正在保存…':'保存缓存规则'}}</button></footer>
    </section>
    <section v-else-if="active==='blocked'" class="workspace-card">
      <WorkspaceCardHeader title="停供记录" description="问题文件排队清理，发布身份保留；恢复后按规则重新缓存。"/>
      <p v-if="page.state?.cleanup_pending" class="workspace-hint">问题文件正在等待清理；使用中的传输结束后重试。进度见同步任务。</p>
      <div class="workspace-table"><table><thead><tr><th>类型 / 版本</th><th>原因</th><th>标记时间</th><th></th></tr></thead><tbody><tr v-for="item in page.blocks" :key="item.family+':'+item.version"><td>{{page.families[item.family]}} · {{item.version}}</td><td>{{item.reason}}</td><td>{{stamp(item.at)}}</td><td><button type="button" class="text-action" @click="select(item)">恢复 / 详情</button></td></tr><tr v-if="!page.blocks.length"><td colspan="4" class="workspace-empty">暂无停供版本</td></tr></tbody></table></div>
      <details class="workspace-help"><summary>标记尚未收录的版本</summary><form class="workspace-form" @submit.prevent="manual"><label>类型<select v-model="manualFamily"><option v-for="(label,key) in page.families" :key="key" :value="key">{{label}}</option></select></label><label>发布方精确版本<input v-model="manualVersion" maxlength="128" required placeholder="与发布方版本号完全一致"></label><button type="submit" :disabled="!manualVersion.trim()">填写原因并标记</button></form></details>
    </section>
  </WorkspaceTabs>
  <WorkspaceDrawer v-if="selected" :title="(page.families[selected.family]||selected.family)+' · '+selected.version" @close="selected=null">
    <p>该精确版本在本站停止分发，清理已索引的缓存副本，并补入下一个可用版本。正在传输的请求结束后清理。</p><div class="workspace-form"><label>问题说明<textarea v-model="reason" maxlength="300" rows="4" placeholder="例如：安装后启动失败，已确认此发布版本存在问题"></textarea></label></div>
    <p v-if="error" class="workspace-alert" role="alert">{{error}}</p>
    <div class="workspace-header-actions"><button v-if="selected.blocked||selected.at" type="button" :disabled="changing" @click="mark(false)">恢复分发</button><button v-else type="button" :disabled="changing||!reason.trim()" @click="mark(true)">停止分发</button><button type="button" class="secondary" @click="selected=null">取消</button></div>
  </WorkspaceDrawer>
</template>
