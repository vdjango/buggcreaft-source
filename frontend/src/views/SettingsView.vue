<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useCollections } from '../stores/collections'
import { formatGiB as size } from '../labels'
const store = useCollections()
const { storage, connected } = storeToRefs(store)
onMounted(store.loadStorage)
onUnmounted(store.stopStorage)
watch(connected, value => { if (value) void store.loadStorage() })
</script>

<template>
  <header><p class="eyebrow">系统设置 / CONFIGURATION</p><h1>平台配置</h1>
    <p class="muted">当前生效配置，只读查看。修改部署配置并重启相关服务后生效。</p></header>
  <section class="panel">
    <div class="section-heading"><h2>采集设置</h2><button :disabled="!connected" @click="store.loadStorage">刷新配置</button></div>
    <p v-if="!connected" class="muted">连接管理服务后查看配置。</p>
    <p v-else-if="!storage" class="muted" role="status">正在获取配置…</p>
    <dl v-else class="metrics">
      <div><dt>待处理任务上限</dt><dd>{{ storage.max_pending_jobs }}</dd></div>
      <div><dt>全局并发任务上限</dt><dd>{{ storage.max_running_jobs }}</dd></div>
      <div><dt>每日下载预算上限</dt><dd>{{ size(storage.daily_download_bytes) }}</dd></div>
      <div><dt>本地磁盘保留阈值</dt><dd>{{ size(storage.min_free_disk_bytes) }}</dd></div>
    </dl>
  </section>
  <section class="panel"><h2>雨云存储设置</h2>
    <p v-if="!storage" class="muted">{{ connected ? '正在获取存储配置…' : '连接后查看存储配置。' }}</p>
    <p v-else-if="!storage.cloud.configured" class="muted">尚未配置对象存储，当前使用本地存储。</p>
    <template v-else>
      <dl class="metrics">
        <div><dt>存储配置 ID</dt><dd>{{ storage.cloud.profile_id }}</dd></div>
        <div><dt>存储提供商</dt><dd>{{ storage.cloud.provider === 'rainyun' ? '雨云对象存储' : storage.cloud.provider }}</dd></div>
        <div><dt>平台托管容量上限</dt><dd>{{ size(storage.cloud.capacity_bytes) }}</dd></div>
        <div><dt>每日上传预算上限</dt><dd>{{ size(storage.cloud.daily_upload_bytes) }}</dd></div>
      </dl>
      <p class="muted">仅显示服务端配置概况，存储密钥保留在服务端。</p>
    </template>
  </section>
</template>
