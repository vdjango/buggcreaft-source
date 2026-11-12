<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { RouterLink, useRouter } from 'vue-router'
import { useCollections } from '../stores/collections'
const store = useCollections(), router = useRouter()
const { busy, connected } = storeToRefs(store)
const url = ref(''), expectedHash = ref(''), algorithm = ref('sha256')
let mounted = true
onUnmounted(() => { mounted = false })
async function enqueue() {
  const job = await store.enqueue({ url: url.value, expected_hash: expectedHash.value || null, hash_algorithm: algorithm.value })
  if (job && mounted) await router.push('/operations/collections')
}
</script>

<template>
  <header><RouterLink to="/operations/collections">← 采集任务</RouterLink><p class="eyebrow">作业管理 / NEW COLLECTION</p><h1>新建采集</h1>
    <p class="muted">下载完整 JAR，再从本地包内读取名称与依赖声明。</p></header>
  <section class="panel form-panel">
    <form class="collection-form" @submit.prevent="enqueue">
      <label>JAR 下载地址<input v-model="url" type="url" placeholder="https://cdn.modrinth.com/.../example.jar" required></label>
      <div class="connection"><label>上游预期摘要（可选）<input v-model="expectedHash" spellcheck="false"></label>
        <label>算法<select v-model="algorithm"><option>sha256</option><option>sha512</option><option>sha1</option></select></label></div>
      <p class="muted">仅接受已配置的发布源；相同请求合并。未提供摘要时标为未核验；需独立采集 worker 执行。</p>
      <button :disabled="busy || !connected">{{ busy ? '正在提交…' : '提交采集任务' }}</button>
    </form>
  </section>
</template>
