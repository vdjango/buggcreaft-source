<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue';
import WorkspaceCardHeader from './WorkspaceCardHeader.vue';
import PrefetchPlatformSettings from './PrefetchPlatformSettings.vue';
import ResourcePinPicker from './ResourcePinPicker.vue';
import {pinKey,useResourcePinsStore,type ResourcePin} from './stores/resourcePins';
import {useMirrorWorkspaceStore} from './stores/mirrorWorkspace';
const store=useMirrorWorkspaceStore();
const catalogKey=ref('');
onMounted(()=>void store.loadCatalogKey());
async function saveCatalogKey(){if(await store.saveCatalogKey(catalogKey.value))catalogKey.value='';}
const settings=computed(()=>store.draft.resource_prefetch),s=computed(()=>store.draft);
const status=computed(()=>store.data.resource_prefetch||{});
const types=[['mod','模组'],['resourcepack','资源包'],['shader','光影包'],['modpack','整合包']];
const catalog=useResourcePinsStore(),picker=ref(false),editing=ref<number|null>(null),pinPage=ref(0),pinError=ref('');
const visiblePins=computed<ResourcePin[]>(()=>(settings.value?.pins||[]).slice(pinPage.value*10,pinPage.value*10+10));
const pinPages=computed(()=>Math.max(1,Math.ceil((settings.value?.pins.length||0)/10)));
const failedNames=computed(()=>visiblePins.value.some((pin:ResourcePin)=>catalog.labelErrors[pinKey(pin)]));
const resolveNames=(retry=false)=>catalog.describe(visiblePins.value,status.value.files||[],retry);
watch(()=>visiblePins.value.map((pin:ResourcePin)=>pinKey(pin)).join('|'),()=>void resolveNames(),{immediate:true});
watch(pinPages,count=>{pinPage.value=Math.min(pinPage.value,count-1);});
function openPicker(index:number|null=null){editing.value=index;pinError.value='';picker.value=true;}
function selectPin(pin:ResourcePin){
  const duplicate=settings.value.pins.findIndex((item:ResourcePin,index:number)=>index!==editing.value&&catalog.identity(item)===catalog.identity(pin));
  if(duplicate>=0){pinError.value='此资源版本已经在固定缓存中。';picker.value=false;return;}
  if(editing.value===null){settings.value.pins.push(pin);pinPage.value=pinPages.value-1;}
  else settings.value.pins.splice(editing.value,1,pin);
  picker.value=false;
}
onBeforeUnmount(()=>catalog.dispose());
</script>
<template>
<section class="workspace-card">
  <WorkspaceCardHeader title="主动缓存" description="提前准备的规则统一在这里配置；执行状态与操作在同步任务页。"><RouterLink to="/admin/mirror/tasks">查看缓存准备任务 →</RouterLink></WorkspaceCardHeader>
  <label class="workspace-check"><input v-model="s.auto_refresh" type="checkbox">自动更新游戏清单并准备所选类别</label>
  <label>优先准备的热门文件数<input v-model.number="s.prefetch_hot_limit" type="number" min="0" max="10000"></label>
    <PrefetchPlatformSettings/>
    <h3>游戏文件与已知资源</h3>
    <div v-if="s.prefetch_category_limits" class="workspace-form-grid">
      <label v-for="(name,key) in {core:'游戏核心',libraries:'运行依赖库',assets:'游戏资源',java:'Java 运行时',mods:'已知模组文件'}" :key="key">{{name}} / 文件数<input v-model.number="s.prefetch_category_limits[key]" type="number" min="0" max="10000"></label>
    </div>
    <p class="workspace-hint">加载器与光影核心的版本缓存、问题标记统一在 <RouterLink to="/admin/mirror/runtimes">加载器与核心</RouterLink> 管理。</p>
    <p class="workspace-hint">无需先有玩家请求：清单中按近 7 天请求热度优先，其余按游戏版本新旧准备前 N 个文件。0 暂停该类主动准备，不删除现有副本。仅处理选择“缓存并提供下载”或“备份兜底”的类别，仍遵守平台、容量和回源并发限制。排行中尚未进入文件清单的项目，由下面的热门排行与固定项目补充。两者按同一文件索引复用，不会重复下载。</p>
    <div class="workspace-footnote">只准备客户端所需文件，跳过服务端、开发映射和重复压缩表示。失败单独退避重试。</div>
</section>
<section v-if="settings" class="workspace-card">
  <WorkspaceCardHeader title="热门排行与固定项目" description="按平台下载量选择热门项目，再按游戏版本分别准备兼容的最新发布版本。"><RouterLink to="/admin/mirror/tasks">查看传输与失败明细 →</RouterLink></WorkspaceCardHeader>
  <label class="workspace-check"><input v-model="settings.enabled" type="checkbox">自动更新热门资源及固定缓存</label>
  <div class="workspace-form-grid">
    <label>每个平台、每类前 N 个热门项目<input v-model.number="settings.limit" type="number" min="0" max="10000"><small>0 表示只准备固定资源。项目数量与每个项目的兼容版本数量分别计算。</small></label>
    <label>更新间隔 / 小时<input v-model.number="settings.refresh_hours" type="number" min="1" max="720"></label>
  </div>
  <p class="workspace-hint">游戏版本范围、每个游戏版本准备多少个发布版本，统一在 <RouterLink to="/admin/mirror/runtimes?view=cache">加载器与核心 → 缓存规则</RouterLink> 设置。跨游戏版本、跨平台的相同文件共用缓存。</p>
  <div class="workspace-form"><span>资源类别</span><div class="workspace-inline-stats"><label v-for="[value,name] in types" :key="value" class="workspace-check"><input v-model="settings.types" type="checkbox" :value="value">{{name}}</label></div>
  <span>排行平台</span><div class="workspace-inline-stats"><label class="workspace-check"><input v-model="settings.providers" type="checkbox" value="modrinth">Modrinth</label><label class="workspace-check"><input v-model="settings.providers" type="checkbox" value="curseforge">CurseForge</label></div><small class="workspace-hint">分别准备两个平台的前 N 个项目；相同摘要与大小的文件共用一份缓存。</small></div>
  <details class="workspace-help"><summary>CurseForge 接入 · {{store.catalogConfigured?'已配置':'待配置'}}</summary><p class="workspace-hint">填写你申请的平台 API 密钥，用于资源目录及文件下载。密钥只保存在服务器，不向启动器公开。</p><form class="resource-key-form" @submit.prevent="saveCatalogKey"><label>API 密钥<input v-model="catalogKey" type="password" autocomplete="new-password" :placeholder="store.catalogConfigured?'留空保留原密钥':'填写平台 API 密钥'"></label><button type="submit" :disabled="store.catalogBusy||(!catalogKey.trim()&&!store.catalogConfigured)">{{store.catalogBusy?'正在同步…':catalogKey.trim()||!store.catalogConfigured?'保存并同步':'同步到分发节点'}}</button></form><p v-if="store.catalogError" role="alert" class="workspace-alert">{{store.catalogError}}</p></details>
  <details class="workspace-help" open><summary>固定缓存 · {{settings.pins.length}}</summary><p class="workspace-hint">搜索要长期准备的项目。自动跟随会按各游戏版本的缓存数量选取最新兼容发布；指定版本会一直保留。文件仍受存储预算限制。</p>
    <p v-if="pinError" role="alert" class="workspace-alert">{{pinError}}</p>
    <div class="resource-pin-list"><article v-for="(pin,index) in visiblePins" :key="pinKey(pin)" class="resource-pin-row">
      <div class="resource-pin-info"><strong>{{catalog.labels[pinKey(pin)]?.title||'正在读取资源名称…'}}</strong><small>{{pin.provider==='curseforge'?'CurseForge':'Modrinth'}} · {{catalog.labels[pinKey(pin)]?.version||(pin.version?'固定版本':'按游戏版本规则跟随兼容发布')}}</small><small v-if="catalog.labels[pinKey(pin)]?.context">{{catalog.labels[pinKey(pin)].context}}</small></div>
      <div class="resource-pin-actions"><button type="button" class="secondary" @click="openPicker(pinPage*10+index)">调整版本</button><button type="button" class="workspace-link" @click="settings.pins.splice(pinPage*10+index,1)">移除</button></div>
    </article></div>
    <p v-if="!settings.pins.length" class="workspace-hint">还没有固定资源。常用模组、光影核心都可以搜索添加。</p>
    <div v-if="pinPages>1" class="workspace-pagination"><button type="button" class="secondary" :disabled="pinPage===0" @click="pinPage--">上一页</button><span>{{pinPage+1}} / {{pinPages}}</span><button type="button" class="secondary" :disabled="pinPage+1>=pinPages" @click="pinPage++">下一页</button></div>
    <div class="resource-pin-actions"><button type="button" class="secondary" :disabled="settings.pins.length>=500" @click="openPicker()">添加固定资源</button><button v-if="failedNames" type="button" class="workspace-link" :disabled="catalog.describing" @click="resolveNames(true)">重试读取名称</button></div>
  </details>
  <div class="workspace-footnote">沿用“模组资源”的缓存策略、存储去向及下载并发。保存后应用配置；移除固定规则只解除保留，不删除文件。</div>
</section>

<ResourcePinPicker v-if="picker" :initial="editing===null?undefined:settings.pins[editing]" :curseforge="status.providers?.curseforge!==false" @close="picker=false" @select="selectPin"/>
</template>
<style scoped>
.resource-key-form{display:flex;align-items:end;gap:12px;flex-wrap:wrap}.resource-key-form label{flex:1;min-width:180px}.resource-key-form input{width:100%}
.resource-pin-list{margin:16px 0}.resource-pin-row{display:flex;align-items:center;gap:16px;padding:14px 0;border-top:1px solid var(--workspace-border)}.resource-pin-info{flex:1;min-width:0}.resource-pin-info strong,.resource-pin-info small{display:block;overflow-wrap:anywhere}.resource-pin-info small{color:var(--muted);line-height:1.7}.resource-pin-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.resource-pin-actions button{white-space:nowrap}
@media(max-width:580px){.resource-pin-row{align-items:flex-start;flex-direction:column;gap:8px}.resource-pin-actions{gap:16px}}
</style>
