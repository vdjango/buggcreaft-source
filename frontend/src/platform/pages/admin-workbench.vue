<script setup lang="ts">
import {computed,defineAsyncComponent,onMounted,onUnmounted,watch} from 'vue';
import {useRoute} from 'vue-router';
import {useCollections} from '../../stores/collections';
import AdminAccess from '../AdminAccess.vue';
import '../../style.css';

const props=defineProps<{seed:Record<string,string>;views:any}>();
const route=useRoute(),store=useCollections();
const components={
  artifacts:defineAsyncComponent(()=>import('../../views/ArtifactsView.vue')),
  artifact:defineAsyncComponent(()=>import('../../views/ArtifactView.vue')),
  collections:defineAsyncComponent(()=>import('../../views/CollectionJobsView.vue')),
  create:defineAsyncComponent(()=>import('../../views/CollectionCreateView.vue')),
  storage:defineAsyncComponent(()=>import('../../views/StorageView.vue')),
  settings:defineAsyncComponent(()=>import('../../views/SettingsView.vue')),
  jobs:defineAsyncComponent(()=>import('../../views/SearchJobsView.vue')),
  status:defineAsyncComponent(()=>import('../../views/SearchStatusView.vue')),
};
const current=computed(()=>{
  const path=route.path;
  if(path.startsWith('/content/artifacts/'))return components.artifact;
  if(path==='/content/artifacts')return components.artifacts;
  if(path==='/operations/collections/new')return components.create;
  if(path==='/operations/collections')return components.collections;
  if(path==='/operations/storage')return components.storage;
  if(path==='/settings')return components.settings;
  return path==='/operations/search/jobs'?components.jobs:components.status;
});
function authorized(){props.views.adminAccess.authenticated(true);void store.connect('');}
watch(()=>props.views.access.authenticated,value=>{if(!value)store.disconnect();});
onMounted(()=>{
  props.views.adminAccess.mount({session:'/v1/admin/session',login:'/v1/admin/session',setup:'/v1/admin/setup',mode:props.seed.authMode||'login',authorized});
  if(props.seed.authMode==='session'||props.views.access.authenticated)authorized();
});
onUnmounted(store.disconnect);
</script>
<template>
  <main class="admin-content">
    <section v-if="views.access.visible" class="admin-access-host"><AdminAccess v-if="views.access.options" :options="views.access.options" :visible="true"/></section>
    <div v-if="views.access.authenticated" class="dependency-workspace">
      <p v-if="store.error" class="error" role="alert">{{store.error}}</p>
      <p v-if="!store.connected" class="connection-status" role="status">正在连接管理服务… <button type="button" class="secondary" @click="store.connect('')">重新连接</button></p>
      <component :is="current" :key="route.path"/>
    </div>
  </main>
</template>
