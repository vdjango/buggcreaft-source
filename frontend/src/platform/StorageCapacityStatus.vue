<script setup lang="ts">
import {computed} from 'vue';
import WorkspaceCardHeader from './WorkspaceCardHeader.vue';
import {bytes,useMirrorWorkspaceStore} from './stores/mirrorWorkspace';
const store=useMirrorWorkspaceStore();
const profiles=computed<any[]>(()=>store.data.cloud_storage?.profiles||[]);
const warnings:Record<string,string>={low:'容量预警：自动模式将切换下一个空间',full:'空间已满：等待容量或下一个空间',unavailable:'空间暂不可用，请检查挂载或配置'};
const egressWarning=(profile:any)=>{
  const e=profile.egress||{};
  if(!e.limit_bytes)return '';
  if(e.warning==='blocked')return `已达流量分流线：${bytes(e.bytes)} / ${bytes(e.limit_bytes)}，新下载将回退来源`;
  if(e.warning==='near')return `接近流量分流线：${bytes(e.bytes)} / ${bytes(e.limit_bytes)}`;
  return '';
};
const available=computed(()=>{
  let total=0;const disks=new Map<string,{sum:number;limit:number}>();
  for(const profile of profiles.value){
    if(!profile.configured)continue;
    const c=profile.capacity||{},n=c.automatic_available_bytes||0;
    if(c.disk_device===null||c.disk_device===undefined){total+=n;continue;}
    const key=String(c.disk_device),disk=disks.get(key)||{sum:0,limit:c.disk_available_bytes||0};
    disk.sum+=n;disk.limit=Math.min(disk.limit,c.disk_available_bytes||0);disks.set(key,disk);
  }
  for(const disk of disks.values())total+=Math.min(disk.sum,disk.limit);
  return total;
});
</script>
<template>
<section class="workspace-card">
  <WorkspaceCardHeader title="存储容量" description="自动模式按配置顺序写入，达到 95% 水位、磁盘保留线或当日预算后切换下一个空间。"><RouterLink to="/admin/mirror/settings/cloud">管理空间与顺序 →</RouterLink></WorkspaceCardHeader>
  <p class="workspace-hint">本轮最多可新增约 {{bytes(available)}}。本地目录按实际磁盘去重，云端按本服务账本估算；其他程序占用及云端实际余量可能影响写入。</p>
  <p v-for="p in profiles.filter(p=>p.capacity?.warning)" :key="p.id" role="alert" class="workspace-alert">{{p.name||p.id}} · {{warnings[p.capacity.warning]}}<small v-if="p.capacity.error">{{p.capacity.error}}</small></p>
  <p v-for="p in profiles.filter(p=>egressWarning(p))" :key="p.id+'-egress'" role="alert" class="workspace-alert">{{p.name||p.bucket||p.id}} · {{egressWarning(p)}}</p>
  <div class="capacity-list"><article v-for="(p,index) in profiles" :key="p.id" class="capacity-row"><strong>{{index+1}} · {{p.name||p.id}}</strong><span v-if="p.egress?.limit_bytes" class="capacity-egress">月流量 {{bytes(p.egress.bytes)}} / {{bytes(p.egress.limit_bytes)}} · 分流阈值 {{bytes(p.egress.stop_bytes)}}</span><span><small>已存 / 配额</small>{{bytes(p.capacity?.used_bytes)}} / {{bytes(p.capacity?.limit_bytes)}}</span><span><small>自动模式可新增</small>{{p.configured?bytes(p.capacity?.automatic_available_bytes):'未配置凭据'}}</span></article><p v-if="!profiles.length" class="workspace-empty">尚未添加存储空间，当前使用默认缓存目录。</p></div>
  <p class="workspace-footnote">计划文件合计不要求单个空间容纳。所有空间均无可写容量时暂停新增，保留缓存并自动复查，不会伪报完成。默认缓存目录另有暂存与保留预算。</p>
</section>
</template>
<style scoped>
.capacity-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1fr);gap:12px;padding:12px 8px;border-bottom:1px solid var(--workspace-border)}
.capacity-row strong{font-weight:500}.capacity-row span{min-width:0;overflow-wrap:anywhere}.capacity-row small{display:block;color:var(--muted);margin-bottom:4px}
.capacity-row .capacity-egress{grid-column:1/-1;color:var(--muted);font-size:12px}
.workspace-empty{padding:20px 8px;text-align:center}
@media(max-width:620px){.capacity-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.capacity-row strong{grid-column:1/-1}.capacity-row small{display:block;margin-bottom:4px}}
</style>
