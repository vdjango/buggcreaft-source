<script setup lang="ts">
import WorkspaceCardHeader from './WorkspaceCardHeader.vue';
import {computed} from 'vue';
import {cloudKinds,profileName,useCloudStorageStore} from './stores/cloudStorage';
import {useRoomClientCacheStore} from './stores/roomClientCache';
const cloud=useCloudStorageStore(),room=useRoomClientCacheStore();
const kinds=cloudKinds.filter(([key])=>key.startsWith('room_'));
const local=computed({get:()=>room.settings.room_cache_quota_mib/1024,set:(n:number)=>{room.settings.room_cache_quota_mib=Math.round(n*1024);}});
const staging=computed({get:()=>room.settings.room_cache_staging_mib/1024,set:(n:number)=>{room.settings.room_cache_staging_mib=Math.round(n*1024);}});
</script>
<template>
<section v-if="cloud.ready" class="workspace-card"><WorkspaceCardHeader title="客户端临时文件存储"><RouterLink to="/admin/mirror/settings/room-cache">保留与缓存规则 →</RouterLink></WorkspaceCardHeader>
  <div class="workspace-form-grid"><label>本地保留上限 / GiB<input v-model.number="local" type="number" min="0" max="1024" step="0.01"><small>0 表示不新增本地保留副本，不影响云端容量。</small></label><label>上传中转暂存 / GiB<input v-model.number="staging" type="number" min="0.01" max="1024" step="0.01"><small>云端核验后释放；玩家直传云端时不占此项。</small></label></div>
  <div class="workspace-table"><table><thead><tr><th>类别</th><th>存储空间</th><th>本地副本</th></tr></thead><tbody><tr v-for="[key,label] in kinds" :key="key"><td>{{label}}</td><td><select :value="cloud.routes[key]?.mode==='auto'?'@auto':cloud.routes[key]?.mode==='local'?'':cloud.routes[key]?.profile" :aria-label="label+'存储空间'" @change="cloud.selectStorage(key,($event.target as HTMLSelectElement).value)"><option value="">默认缓存目录</option><option value="@auto">自动 · 私有空间按顺序切换</option><option v-for="p in cloud.profiles.filter(p=>p.private)" :key="p.id" :value="p.id">{{profileName(p)}}</option></select><small v-if="cloud.routeErrors[key]">{{cloud.routeErrors[key]}}</small></td><td><select v-if="!['local','auto'].includes(cloud.routes[key]?.mode)" v-model="cloud.routes[key].mode" :aria-label="label+'本地副本'"><option value="cloud">仅目标空间</option><option value="copy">主目录与目标空间</option></select><span v-else>{{cloud.routes[key]?.mode==='auto'?'核验后释放暂存':'本地保留'}}</span><small v-if="!local&&!['cloud','auto'].includes(cloud.routes[key]?.mode)">本地上限为 0，请选择仅目标空间。</small></td></tr></tbody></table></div>
  <p class="workspace-hint">读取时按文件摘要复用所有空间中的已核验副本，包括普通资源缓存。这里仅决定缺失文件写到哪里；自动模式按空间顺序切换，房间文件只写入私有空间。</p>
</section>
</template>
