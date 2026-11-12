<script setup lang="ts">
import {computed} from 'vue';
import links from '../../platform/navigation.json';
import {adminSectionFor,adminSections} from './adminNavigation';
import {usePlatformNavigation} from './usePlatformNavigation';
import {isMirrorHome,publicLinks} from './publicSeo';
import {isAdminPath} from '../navigation';
const props=defineProps<{authenticated?:boolean;initialPath?:string;mirrorHome?:boolean}>();
const {path,navigate}=usePlatformNavigation(props.initialPath);
const menu=computed(()=>{const hrefs=publicLinks(props.mirrorHome??isMirrorHome());return links.map(link=>({...link,href:link.href==='/mirror'?hrefs.mirror:link.href==='/'?hrefs.translation:link.href}));});
const admin=computed(()=>isAdminPath(path.value));
const selected=computed(()=>admin.value?adminSectionFor(path.value):'');
const active=(href:string)=>href==='/'?path.value==='/'||path.value.startsWith('/projects/'):path.value===href||path.value.startsWith(href+'/');
</script>
<template>
  <nav v-show="!admin||authenticated" class="site-nav" :class="{'site-nav-admin':admin}" data-vue-nav :aria-label="admin?'后台主导航':'站点主导航'">
    <a class="brand" :href="admin?'/admin':'/mirror'" @click="navigate($event,admin?'/admin':'/mirror')"><span class="mark">B</span><span class="brand-type"><strong>Bugg 源站</strong><small>{{admin?'管理工作台':'让每一次出发，更轻松'}}</small></span></a>
    <div class="nav-links site-desktop-links"><template v-if="admin"><a v-for="section in adminSections" :key="section.id" :href="section.href" :class="{'nav-active':selected===section.id}" :aria-current="selected===section.id?'page':undefined" @click="navigate($event,section.href)">{{section.label}}</a></template><template v-else><a v-for="link in menu" :key="link.href" :href="link.href" :class="{'nav-active':active(link.href)}" :aria-current="active(link.href)?'page':undefined" @click="navigate($event,link.href)">{{link.label}}</a></template></div>
    <div class="site-nav-tools"><details class="site-mobile-menu"><summary aria-label="打开导航菜单">菜单 <span aria-hidden="true">☰</span></summary><div><template v-if="admin"><a v-for="section in adminSections" :key="section.id" :href="section.href" :aria-current="selected===section.id?'page':undefined" @click="navigate($event,section.href)">{{section.label}}</a></template><template v-else><a v-for="link in menu" :key="link.href" :href="link.href" :aria-current="active(link.href)?'page':undefined" @click="navigate($event,link.href)">{{link.label}}</a></template></div></details></div>
  </nav>
</template>
