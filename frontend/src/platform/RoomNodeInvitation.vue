<script setup lang="ts">
import {useRoomClientCacheStore} from './stores/roomClientCache';
const rooms=useRoomClientCacheStore();
async function invite(){try{await rooms.invite();}catch{}}
</script>
<template>
  <div class="room-node-invitation">
    <p>为支持源站接入协议的节点生成一次性邀请。节点维护者领取后保存密钥，此处可以撤销授权。</p>
    <form class="workspace-form" @submit.prevent="invite">
      <label>节点名称<input v-model.trim="rooms.nodeName" maxlength="80" placeholder="例如 上海"></label>
      <button type="submit" :disabled="rooms.busy||!rooms.nodeName.trim()">{{rooms.busy?'正在生成…':'生成一次性接入邀请'}}</button>
    </form>
    <div v-if="rooms.invitation" class="workspace-alert workspace-invite" role="status">
      <strong>一次性接入邀请，10 分钟内有效</strong>
      <p>联机节点不提供网页后台。请将邀请交给节点维护者，先确认节点版本支持源站接入，再通过对应接入工具领取。</p>
      <code>{{rooms.invitation}}</code>
      <button type="button" class="secondary" @click="rooms.copy()">复制接入邀请</button>
    </div>
    <p v-if="rooms.feedback" class="workspace-hint" role="status">{{rooms.feedback}}</p>
    <p v-if="rooms.error" class="workspace-alert" role="alert">{{rooms.error}}</p>
    <RouterLink to="/admin/nodes">在节点管理中查看或撤销授权 →</RouterLink>
  </div>
</template>
<style scoped>
.room-node-invitation,.workspace-invite{display:grid;gap:14px}.room-node-invitation p{margin:0}.workspace-invite code{overflow-wrap:anywhere;user-select:all}
.workspace-invite{color:var(--text);background:var(--raised);border-color:var(--line)}
</style>
