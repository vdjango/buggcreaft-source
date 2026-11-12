<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue';
import WorkspaceDrawer from './WorkspaceDrawer.vue';
import {pinKey,useResourcePinsStore,type ResourcePin,type PinProject} from './stores/resourcePins';
const props=defineProps<{initial?:ResourcePin;curseforge:boolean}>();
const emit=defineEmits<{close:[];select:[pin:ResourcePin]}>();
const catalog=useResourcePinsStore(),provider=ref(props.initial?.provider||'modrinth'),kind=ref('mod'),query=ref(''),version=ref(props.initial?.version||'');
const initialLabel=computed(()=>props.initial?catalog.labels[pinKey(props.initial)]:undefined);
const missingInitial=computed(()=>props.initial?.version&&!catalog.versions.some(item=>item.id===props.initial?.version));
const search=()=>{void catalog.search(provider.value,kind.value,query.value.trim());};
const choose=(project:PinProject)=>{version.value='';void catalog.choose(provider.value,project);};
const confirm=()=>{if(catalog.selected)emit('select',{provider:provider.value,project:catalog.selected.id,version:version.value});};
watch([provider,kind],search);
if(props.initial){
  const label=catalog.labels[[props.initial.provider,props.initial.project,props.initial.version].join('/')];
  void catalog.choose(provider.value,{id:props.initial.project,title:label?.title||'当前资源',description:'',icon:''});
}else search();
onBeforeUnmount(()=>catalog.cancel());
</script>
<template>
<WorkspaceDrawer :title="initial?'调整固定版本':'添加固定缓存'" @close="emit('close')">
  <template v-if="!catalog.selected">
    <form class="workspace-form" @submit.prevent="search">
      <label>搜索资源<input v-model="query" placeholder="输入资源名称，例如 Iris、Sodium" maxlength="200"></label>
      <div class="workspace-form-grid"><label>资源类型<select v-model="kind"><option value="mod">模组 / 光影核心</option><option value="resourcepack">资源包</option><option value="shader">光影包</option><option value="modpack">整合包</option></select></label><label>发布平台<select v-model="provider"><option value="modrinth">Modrinth</option><option value="curseforge" :disabled="!curseforge">CurseForge{{curseforge?'':' · 暂不可用'}}</option></select></label></div>
      <button type="submit" :disabled="catalog.loading">{{catalog.loading?'正在搜索…':'搜索'}}</button>
    </form>
    <p v-if="catalog.error" role="alert" class="workspace-alert">{{catalog.error}}</p>
    <p v-else-if="catalog.loading" role="status" class="workspace-empty">正在查找资源…</p>
    <div v-else-if="catalog.results.length" class="resource-pin-results">
      <button v-for="project in catalog.results" :key="project.id" type="button" class="resource-pin-result secondary" @click="choose(project)"><img v-if="project.icon" :src="project.icon" alt="" loading="lazy"><span><strong>{{project.title}}</strong><small>{{project.description||'选择资源以查看可固定版本'}}</small></span><span aria-hidden="true">›</span></button>
    </div>
    <p v-else class="workspace-empty">没有找到资源，试试完整名称或切换资源类型。</p>
  </template>
  <template v-else>
    <button v-if="!initial" type="button" class="workspace-link" @click="catalog.cancel();catalog.selected=null">← 重新选择资源</button>
    <h3>{{catalog.selected.title}}</h3>
    <p class="workspace-hint">自动跟随时，按缓存规则为每个游戏版本选取最新兼容发布；同一发布只保存一份。也可指定一个版本长期保留。</p>
    <p v-if="catalog.versionLoading" role="status">正在读取版本…</p>
    <template v-else-if="catalog.versionError"><p role="alert" class="workspace-alert">{{catalog.versionError}}</p><button type="button" class="secondary" @click="catalog.choose(provider,catalog.selected)">重试读取版本</button></template>
    <label v-else-if="catalog.versions.length||missingInitial" class="workspace-form">缓存版本<select v-model="version"><option value="">按游戏版本规则跟随兼容发布</option><option v-if="missingInitial" :value="initial!.version">{{initialLabel?.version||'当前已固定版本'}} · 保留当前选择</option><option v-for="item in catalog.versions" :key="item.id" :value="item.id">{{item.name}} · {{item.games.slice(0,3).join('、')}}{{item.loaders.length?' · '+item.loaders.join(' / '):''}}{{item.release?'':' · 测试版'}}</option></select><small v-if="provider==='curseforge'">显示最近发布的 50 个版本。</small></label>
    <p v-else class="workspace-empty">此资源暂时没有可缓存的文件，请重新选择资源。</p>
    <p class="workspace-hint">{{version?'固定此版本，后续更新不会替换它。':'按各游戏版本的缓存数量准备最新兼容发布。'}}添加后点击页面底部保存。</p>
  </template>
  <template #footer><button type="button" class="secondary" @click="emit('close')">取消</button><button v-if="catalog.selected" type="button" :disabled="catalog.versionLoading||!!catalog.versionError||(!catalog.versions.length&&!missingInitial)" @click="confirm">{{initial?'确认版本':'添加到固定缓存'}}</button></template>
</WorkspaceDrawer>
</template>
<style scoped>
.resource-pin-results{display:grid;gap:8px;margin-top:20px;min-width:0}.resource-pin-result{display:flex;align-items:center;gap:12px;width:100%;min-width:0;box-sizing:border-box;white-space:normal;text-align:left;padding:12px!important}.resource-pin-result>span:first-of-type{flex:1;min-width:0;overflow-wrap:anywhere}.resource-pin-result strong{display:block}.resource-pin-result small{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;white-space:normal;overflow:hidden;line-height:1.6;color:var(--muted)}.resource-pin-result img{width:36px;height:36px;flex-shrink:0;object-fit:contain;border-radius:8px}
</style>
