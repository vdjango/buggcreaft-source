<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue';
import {adminItemActive,adminSectionFor,adminSections} from './adminNavigation';
import {usePlatformNavigation} from './usePlatformNavigation';
const props=defineProps<{authenticated:boolean}>();
const sidebar=ref<HTMLElement>();
const {path,navigate}=usePlatformNavigation();
const section=computed(()=>adminSections.find(item=>item.id===adminSectionFor(path.value))!);
function revealCurrent(){
  const current=sidebar.value?.querySelector<HTMLElement>('[aria-current="page"]');
  if(current&&sidebar.value&&innerWidth<=760)sidebar.value.scrollLeft=current.offsetLeft-sidebar.value.offsetLeft-12;
}
onMounted(()=>window.addEventListener('resize',revealCurrent));
onUnmounted(()=>window.removeEventListener('resize',revealCurrent));
watch([path,()=>props.authenticated],async()=>{
  await nextTick();
  revealCurrent();
},{immediate:true,flush:'post'});
</script>
<template>
  <aside ref="sidebar" v-if="authenticated" class="admin-sidebar" :aria-label="section.label+'导航'">
    <span class="eyebrow">{{section.label}}</span>
    <template v-for="item in section.items" :key="item.href">
      <a :href="item.href" :class="{active:adminItemActive(path,item.href)}" :aria-current="adminItemActive(path,item.href)?'page':undefined" @click="navigate($event,item.href)">{{item.label}}</a>
    </template>
    <div class="sidebar-bottom">Buggcraft 管理后台</div>
  </aside>
</template>
