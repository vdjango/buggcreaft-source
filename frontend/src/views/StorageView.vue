<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useCollections } from '../stores/collections'
import { stateLabels, formatGiB as size } from '../labels'
import { RouterLink } from 'vue-router'
const store = useCollections()
const { storage, connected } = storeToRefs(store)
onMounted(store.loadStorage)
onUnmounted(store.stopStorage)
watch(connected, value => { if (value) void store.loadStorage() })
</script>

<template>
  <header><p class="eyebrow">运维监控 / STORAGE</p><h1>存储与额度</h1><p class="muted">查看任务数量、容量预留与今日预算；限额及存储配置请前往<RouterLink to="/settings">系统设置</RouterLink>。</p></header>
  <section class="panel">
    <div class="section-heading"><h2>采集预算</h2><button :disabled="!connected" @click="store.loadStorage">刷新状态</button></div>
    <p v-if="!connected" class="muted">连接管理服务后查看额度。</p>
    <p v-else-if="!storage" class="muted" role="status">正在获取状态…</p>
    <dl v-else class="metrics">
      <div><dt>排队 / 运行</dt><dd>{{ storage.active.queued ?? 0 }} / {{ storage.active.running ?? 0 }}</dd></div>
      <div><dt>今日下载预算预留</dt><dd>{{ size(storage.reserved_download_bytes) }}</dd></div>
    </dl>
    <p v-if="storage" class="muted">预算日期 {{ storage.budget_day_utc }}（UTC）。预留按最坏下载量计算，失败不退还，不等于实测流量。</p>
  </section>
  <section class="panel"><h2>雨云对象归档</h2>
    <p v-if="!storage" class="muted">{{ connected ? '正在获取归档状态…' : '连接后查看归档任务。' }}</p>
    <p v-else-if="!storage.cloud.configured" class="muted">尚未配置对象存储。采集结果与原始 JAR 保存在本地。</p>
    <template v-else>
      <dl class="metrics">
        <div><dt>归档容量预留</dt><dd>{{ size(storage.cloud.reserved_capacity_bytes) }}</dd></div>
        <div><dt>今日上传预算预留</dt><dd>{{ size(storage.cloud.reserved_upload_bytes) }}</dd></div>
      </dl>
      <div class="table-wrap"><table><thead><tr><th>归档状态</th><th>对象数量</th></tr></thead><tbody><tr v-for="(count, state) in storage.cloud.states" :key="state"><td>{{ stateLabels[state] ?? state }}</td><td>{{ count }}</td></tr></tbody></table></div>
      <p v-if="!Object.keys(storage.cloud.states ?? {}).length" class="muted">暂无归档任务。</p>
      <p class="muted">独立归档 worker 核验云端 SHA-256 后才标记成功。失败保留本地文件，容量预留不自动释放。</p>
    </template>
  </section>
</template>
