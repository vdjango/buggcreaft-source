<script setup lang="ts">
import WorkspaceCardHeader from './WorkspaceCardHeader.vue';
import {ref} from 'vue';
import {mirrorCategories} from './stores/mirrorWorkspace';
import {profileName,useCloudStorageStore} from './stores/cloudStorage';
import WorkspaceDrawer from './WorkspaceDrawer.vue';
const cloud=useCloudStorageStore(),editing=ref('');
const policies:Record<string,string>={cache:'缓存并提供下载',redirect:'仅重定向',backup:'备份并重定向'};
const publishers:Record<string,string>={core:'Mojang',loaders:'Forge / NeoForge / Fabric / Quilt',mods:'CurseForge / Modrinth',java:'Mojang Java',libraries:'公共 Maven / Mojang',assets:'Mojang assets'};
function target(key:string){const route=cloud.routes[key];return route?.mode==='auto'?'自动 · 按空间顺序':route?.mode==='local'?'默认缓存目录':(cloud.profiles.find(p=>p.id===route?.profile)?.name||route?.profile||'未指定')+(route?.mode==='copy'?' + 本地副本':'');}
</script>
<template>
<section class="workspace-card">
  <WorkspaceCardHeader title="资源下载规则" description="缓存策略优先使用已有副本；缺失时后台准备，存储去向决定由本机或对象存储提供下载。"><RouterLink to="/admin/mirror/settings/cloud">管理存储空间 →</RouterLink></WorkspaceCardHeader>
  <div class="workspace-table"><table><thead><tr><th>类别</th><th>下载策略</th><th>存储去向</th><th>原始发布地址</th><th></th></tr></thead><tbody>
    <tr v-for="[key,name] in mirrorCategories" :key="key"><td><strong>{{name}}</strong></td><td>{{policies[cloud.policies[key]]||'仅重定向'}}</td><td>{{target(key)}}<small v-if="cloud.policies[key]==='redirect'">仅迁移已有副本，不预下载</small></td><td class="workspace-hint">{{publishers[key]}}</td><td><button type="button" class="workspace-link" @click="editing=key">编辑</button></td></tr>
  </tbody></table></div>
  <details class="workspace-help"><summary>三种策略的区别</summary><dl class="workspace-details"><dt>缓存并提供下载</dt><dd>优先使用已就绪的本机或对象存储副本，并按指定去向主动准备缺失文件。未命中时立即返回可用来源，后台补缓存。</dd><dt>仅重定向</dt><dd>直接跳转已核验来源，未命中时跳转该类别的原始发布地址。不下载新副本。</dd><dt>备份并重定向</dt><dd>平时跳转来源，同时在后台备份；来源故障后使用已核验副本。</dd></dl></details>
</section>
<WorkspaceDrawer v-if="editing&&cloud.routes[editing]" :title="mirrorCategories.find(([key])=>key===editing)?.[1]+' · 下载规则'" @close="editing=''">
  <div class="workspace-form"><label>下载策略<select v-model="cloud.policies[editing]"><option v-for="(name,key) in policies" :key="key" :value="key">{{name}}</option></select></label><label>存储去向<select :value="cloud.routes[editing].mode==='auto'?'@auto':cloud.routes[editing].mode==='local'?'':cloud.routes[editing].profile" @change="cloud.selectStorage(editing,($event.target as HTMLSelectElement).value)"><option value="">默认缓存目录</option><option value="@auto">自动 · 按存储顺序切换</option><option v-for="profile in cloud.profiles" :key="profile.id" :value="profile.id">{{profileName(profile)}}</option></select></label><label v-if="!['local','auto'].includes(cloud.routes[editing].mode)">本地副本<select v-model="cloud.routes[editing].mode"><option value="cloud">仅目标空间，核验后释放暂存</option><option value="copy">默认缓存与目标空间同时保留</option></select></label><label v-if="cloud.routes[editing].mode!=='local'">空间内路径<input v-model.trim="cloud.routes[editing].path" maxlength="80"></label><p v-if="cloud.routeErrors[editing]" class="workspace-alert">{{cloud.routeErrors[editing]}}</p></div>
  <template #footer><span class="workspace-hint">关闭后，点击页面底部保存生效。</span><button type="button" @click="editing=''">完成编辑</button></template>
</WorkspaceDrawer>
</template>
