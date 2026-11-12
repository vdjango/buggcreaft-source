<script setup lang="ts">
import {platformFetch as fetch} from '../../transport';
import {computed,onMounted,onUnmounted,watch} from 'vue';
import {useRoute,useRouter} from 'vue-router';
import AdminAccess from '../AdminAccess.vue';
import WorkspaceTabs from '../WorkspaceTabs.vue';
import MirrorResourceList from '../MirrorResourceList.vue';
import RuntimeCatalog from '../RuntimeCatalog.vue';
import MirrorSyncTasks from '../MirrorSyncTasks.vue';
import MirrorSourceCoverage from '../MirrorSourceCoverage.vue';
import CloudStorageTasks from '../CloudStorageTasks.vue';
import SourceQualityPanel from '../SourceQualityPanel.vue';
import MirrorSettings from '../MirrorSettings.vue';
import {useMirrorWorkspaceStore} from '../stores/mirrorWorkspace';
import '../mirrorWorkspace.css';
import '../mirrorWorkspaceCompact.css';
const props=defineProps<{seed:Record<string,string>;views:any}>();
const route=useRoute(),router=useRouter(),workspace=useMirrorWorkspaceStore();
const settings=computed(()=>route.path.startsWith('/admin/mirror/settings'));
const section=computed(()=>route.path.split('/')[settings.value?4:3]||(settings.value?'downloads':'resources'));
const titles:Record<string,string>={runtimes:'加载器与核心',quality:'来源质量',temporary:'客户端临时文件',transfers:'云迁移任务',resources:'缓存资源',tasks:'自动任务',sources:'资源索引'};
const title=computed(()=>settings.value?'镜像设置':titles[section.value]||'缓存资源');
const subtitle=computed(()=>({quality:'测速、健康与来源覆盖观测',temporary:'房间分发的临时副本',transfers:'独立查看云复制与迁移作业',resources:'查看实际保留的副本和存放位置',tasks:'自动同步、重试与云端复制',sources:'清单文件、仓库映射与来源质量'} as Record<string,string>)[section.value]||(settings.value?'按资源类别配置去向、来源与容量':''));
let timer:ReturnType<typeof setInterval>|undefined;
function access(){props.views.adminAccess.mount({session:'/v1/admin/session',login:'/v1/admin/session',setup:'/v1/admin/setup',mode:props.seed.authMode||'login',authorized(){workspace.unauthorized=false;props.views.adminAccess.authenticated(true);void workspace.refresh();}});if(props.seed.authMode==='session'){props.views.adminAccess.authenticated(true);void workspace.refresh();}}
async function logout(){await fetch('/v1/admin/session',{method:'DELETE',credentials:'same-origin'});props.views.adminAccess.authenticated(false);props.views.access.options={...props.views.access.options,mode:'login'};}
watch(()=>workspace.unauthorized,value=>{if(value)props.views.adminAccess.authenticated(false);});
watch(()=>route.path,path=>{
  const aliases:Record<string,string>={'/admin/mirror':'/admin?view=resources','/admin/mirror/settings/distribution':'/admin/mirror/settings/storage?view=egress'};
  if(aliases[path])void router.replace(aliases[path]);
  document.title=title.value+' · Buggcraft';
},{immediate:true});
onMounted(()=>{access();timer=setInterval(()=>{if(props.views.access.authenticated&&!document.hidden)void workspace.refresh();},5000);});
onUnmounted(()=>{if(timer)clearInterval(timer);});
</script>
<template>
<main class="admin-content mirror-workspace">
  <section v-if="views.access.visible" class="admin-access-host"><AdminAccess v-if="views.access.options" :options="views.access.options" :visible="true"/></section>
  <template v-if="views.access.authenticated">
    <header class="workspace-header"><div><h1>{{title}}</h1><p>{{subtitle}}</p></div><div class="workspace-header-actions"><span class="workspace-live"><i/> 自动更新</span><button type="button" class="secondary" :disabled="workspace.loading" @click="workspace.refresh">刷新数据</button><button type="button" class="workspace-link" @click="logout">退出</button></div></header>
    <p v-if="workspace.error" class="workspace-alert" role="status">{{workspace.error}}</p>
    <div v-if="!workspace.ready" class="workspace-card" aria-busy="true"><span v-for="n in 7" :key="n" class="workspace-skeleton"/></div>
    <div v-else class="workspace-stack">
      <template v-if="settings"><MirrorSettings :key="section" :section="section"/><footer v-if="section!=='cloudflare'" class="workspace-save"><span>只保存本页设置，其他页面的草稿独立保留。</span><button type="button" :disabled="workspace.saving" @click="workspace.save(section)">{{workspace.saving?'正在保存…':'保存本页设置'}}</button></footer></template>
      <MirrorResourceList v-else-if="section==='resources'" :temporary="false"/>
      <RuntimeCatalog v-else-if="section==='runtimes'"/>
      <MirrorResourceList v-else-if="section==='temporary'" :temporary="true"/>
      <MirrorSyncTasks v-else-if="section==='tasks'"/>
      <CloudStorageTasks v-else-if="section==='transfers'"/>
      <SourceQualityPanel v-else-if="section==='quality'"/>
      <MirrorSourceCoverage v-else-if="section==='sources'"/>
    </div>
  </template>
</main>
</template>
