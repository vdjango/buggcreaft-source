<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { RouterLink } from 'vue-router'
import { useCollections } from '../stores/collections'
import { stateLabels, formatMiB } from '../labels'
const store = useCollections()
const { artifacts, artifactsLoading, connected } = storeToRefs(store)
const cursor = ref<string | null>(null)
function reload() { void store.loadArtifacts(cursor.value) }
watch(cursor, reload)
watch(connected, value => { if (value) { if (cursor.value !== null) cursor.value = null; else reload() } })
onMounted(reload)
onUnmounted(store.stopArtifacts)
</script>

<template>
  <header><p class="eyebrow">内容管理 / ARTIFACTS</p><h1>制品库</h1>
    <p class="muted">已从本地 JAR 采集的真实制品。选择一份扫描，查看包内名称与依赖。</p></header>
  <section class="panel">
    <div class="section-heading"><h2>入库扫描</h2><button :disabled="!connected || artifactsLoading" @click="reload">刷新列表</button></div>
    <p v-if="!connected" class="muted">连接管理服务后查看制品库。</p>
    <p v-else-if="artifactsLoading && !artifacts" class="muted" role="status">正在获取制品…</p>
    <p v-else-if="artifacts && !artifacts.items.length" class="muted">暂无入库制品，可前往<RouterLink to="/operations/collections/new">新建采集</RouterLink>。</p>
    <div v-for="item in artifacts?.items ?? []" :key="item.scan_id" class="artifact-row">
      <RouterLink class="artifact-link" :to="{ path: `/content/artifacts/${item.artifact_sha256}`, query: { parser_version: item.parser_version } }"><code>{{ item.artifact_sha256 }}</code></RouterLink>
      <div class="artifact-meta"><span>{{ stateLabels[item.status] ?? item.status }}</span><span>{{ formatMiB(item.size) }}</span><span>解析器 {{ item.parser_version }}</span></div>
    </div>
    <div v-if="artifacts?.items.length" class="pagination"><small>本页 {{ artifacts.items.length }} 条 · 每页最多 50 条</small>
      <button v-if="cursor" :disabled="artifactsLoading" @click="cursor = null">返回首页</button>
      <button v-if="artifacts.next_cursor" :disabled="artifactsLoading" @click="cursor = artifacts.next_cursor">下一页</button></div>
  </section>
</template>
