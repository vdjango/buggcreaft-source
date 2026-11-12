<script setup lang="ts">
import WorkspaceCardHeader from './WorkspaceCardHeader.vue';
import WorkspaceDrawer from './WorkspaceDrawer.vue';
import RoomNodeInvitation from './RoomNodeInvitation.vue';
import RoomStorageSettings from './RoomStorageSettings.vue';
import {ref} from 'vue';
import {useRoomClientCacheStore} from './stores/roomClientCache';
const cache=useRoomClientCacheStore();
const s=cache.settings;
const onboarding=ref(false);
</script>
<template>
  <section class="workspace-card">
    <WorkspaceCardHeader title="客户端文件加速" description="整合包导入、下载补齐与房间同步共用同一份资源索引。"><RouterLink to="/admin/mirror/temporary">查看临时副本 →</RouterLink></WorkspaceCardHeader>
    <div class="workspace-footnote"><span>授权联机节点后即可使用源站补齐房间文件，接入密钥自动生成。</span><button type="button" class="secondary" @click="onboarding=true">接入联机节点</button></div>
    <div class="workspace-form-grid">
      <label class="workspace-check"><input v-model="s.room_cache_enabled" type="checkbox">启用临时文件加速</label>
      <label class="workspace-check"><input v-model="s.room_cache_public_lookup_enabled" type="checkbox">后台补查模组的公开发布地址</label>
      <label>缓存方式<select v-model="s.room_cache_policy"><option value="adaptive">按需缓存</option><option value="full">完整缓存</option></select><small>{{s.room_cache_policy==='full'?'源站接管房间模组、资源包和光影，只补传缺少的文件。':'优先使用快速公开来源，缺失或慢速文件再补传。'}}</small></label>
      <label>保留时间 / 小时<input v-model.number="s.room_cache_ttl_hours" type="number" min="1" max="32"></label>
      <label>单文件上限 / MiB<input v-model.number="s.room_cache_max_file_mib" type="number" min="1" max="2048"></label>
    </div>
    <div class="workspace-footnote">两种方式都按文件摘要查重，改名不会重复缓存。房主在启动器开启“加速缓存”后生效，完成进度以逐文件校验为准。<RouterLink to="/admin/mirror/settings/sources">管理公共来源 →</RouterLink></div>
    <details class="workspace-help"><summary>匹配范围与有效时间</summary><p>依赖库、游戏核心等匹配阿里云、华为云、Maven 和已启用的 Minecraft 镜像；模组、资源包、光影匹配公开发布地址。只认相同哈希与文件大小，不按名称替换。</p><p>公共来源查询与临时补传开关独立；仓库映射按下载来源设置参与选路。补查开关只控制后台查找模组发布地址。保留时间仅约束玩家补传的临时副本；普通镜像副本按各类别的存储规则管理。</p></details>
  </section>
  <RoomStorageSettings/>
  <WorkspaceDrawer v-if="onboarding" title="接入联机节点" @close="onboarding=false"><RoomNodeInvitation/></WorkspaceDrawer>
</template>
