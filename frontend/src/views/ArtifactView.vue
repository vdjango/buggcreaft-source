<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { RouterLink, useRoute } from 'vue-router'
import { useCollections } from '../stores/collections'
import DependencyTable from '../components/DependencyTable.vue'
import { stateLabels } from '../labels'
const store = useCollections(), route = useRoute()
const { scan, scanLoading, connected } = storeToRefs(store)
function load() {
  const version = typeof route.query.parser_version === 'string' ? route.query.parser_version : '1.0.0'
  void store.openArtifact(String(route.params.digest), version)
}
watch(() => route.fullPath, load)
watch(connected, value => { if (value) load() })
onMounted(load)
onUnmounted(store.stopScan)
</script>

<template>
  <header><RouterLink to="/content/artifacts">← 制品库</RouterLink><p class="eyebrow">内容管理 / ARTIFACT</p><h1>制品详情</h1>
    <p class="muted">来自包内元数据的原始声明，清单完整性不代表运行兼容性。</p></header>
  <section v-if="!scan" class="panel"><p class="muted">{{ !connected ? '连接管理服务后查看制品详情。' : scanLoading ? '正在获取包内声明…' : '未能获取该制品，请检查错误信息或重试。' }}</p><button v-if="connected && !scanLoading" @click="load">重新加载</button></section>
  <template v-else>
    <section class="panel"><h2>扫描记录 <span>{{ stateLabels[scan.status] ?? scan.status }}</span></h2>
      <p class="digest"><code>SHA-256 {{ scan.artifact_sha256 }}</code></p>
      <p class="muted">解析器 {{ scan.parser_version }} · {{ scan.mods.length }} 个包内模组 · {{ scan.evidence.length }} 份原始证据</p>
      <ul v-if="scan.issues.length" class="issues"><li v-for="(issue, index) in scan.issues" :key="index">{{ issue.code }} · {{ issue.path }}</li></ul>
    </section>
    <section class="panel"><h2>包内声明</h2><p v-if="!scan.mods.length" class="muted">没有可展示的受支持模组声明，不代表零依赖。</p>
      <article v-for="(mod, index) in scan.mods" :key="index">
        <h3>{{ mod.declared_name ?? '包内未声明名称' }} <code>{{ mod.mod_id }}</code></h3>
        <p class="muted">{{ mod.loader }} · 原始版本 {{ mod.version_raw ?? '未声明' }} · 解析版本 {{ mod.version_resolved ?? '未确定' }}</p>
        <p class="digest"><code>{{ mod.container_path }}!/{{ mod.metadata_path }}</code></p>
        <DependencyTable :rows="mod.dependencies" />
      </article>
    </section>
    <section class="panel"><h2>原始证据</h2><p v-if="!scan.evidence.length" class="muted">无原始证据记录。</p>
      <div v-for="(item, index) in scan.evidence" :key="index" class="evidence-row"><strong>{{ item.member_path }}</strong><p class="digest"><code>SHA-256 {{ item.sha256 }}</code></p></div>
    </section>
  </template>
</template>
