<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { RouterLink } from 'vue-router'
import { useCollections } from '../stores/collections'
import { stateLabels } from '../labels'
const store = useCollections()
const { jobs, connected, collectionControl, discovery, busy, error } = storeToRefs(store)
onMounted(store.startPolling)
onUnmounted(store.stopPolling)
</script>

<template>
  <header><p class="eyebrow">作业管理 / DEPENDENCIES</p><h1>依赖采集</h1>
    <p class="muted">缓存文件、实际请求与热点准备共用采集队列。按文件内容分别保存声明，解析完成不代表已经验证游戏可运行。</p></header>
  <section class="panel">
    <div class="section-heading"><h2>{{ collectionControl?.paused ? '采集已暂停' : '采集队列' }}</h2>
      <button :disabled="!connected || busy || !collectionControl" @click="store.pauseCollection(!collectionControl?.paused)">{{ collectionControl?.paused ? '继续采集' : '暂停采集' }}</button></div>
    <p class="muted">暂停后保留队列与已解析的文件；正在处理的任务在安全检查点暂停。恢复后继续处理，失败任务可单独重试。</p>
    <p v-if="collectionControl" class="muted">等待 {{ collectionControl.states.queued ?? 0 }} · 运行 {{ collectionControl.states.running ?? 0 }} · 采集完成 {{ collectionControl.states.succeeded ?? 0 }} · 失败 {{ collectionControl.states.failed ?? 0 }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
  <section v-if="discovery?.configured" class="panel">
    <div class="section-heading"><h2>请求与热点的递归发现</h2>
      <button :disabled="!connected || busy || !!collectionControl?.paused" @click="store.retryDiscovery()">重试未完成项</button></div>
    <p class="muted">待发现 {{ discovery.states.pending ?? 0 }} · 已展开 {{ discovery.states.expanded ?? 0 }} · 未完成 {{ (discovery.states.failed ?? 0) + (discovery.states.partial ?? 0) }} · 待确认项目对应关系 {{ discovery.unresolved_bindings ?? 0 }}</p>
    <p class="muted">已展开表示已读取该文件并跟进发布者列出的依赖；项目对应关系来自实际文件中的模组身份。未找到可靠对应关系时保留待处理，不能代表任意游戏环境都可运行。网络失败保留任务并退避重试；重试未完成项每次最多重新排入 100 个失败任务。</p>
    <div v-for="item in discovery.items" :key="item.provider + item.release_id" class="job">
      <div class="job-info"><strong>{{ item.provider }} · {{ item.release_id }}</strong>
        <small>{{ ({request:'实时请求',hotspot:'热点准备',dependency:'依赖继续发现'} as Record<string,string>)[item.origin] ?? item.origin }} · 深度 {{ item.depth }} · {{ item.state === 'expanded' ? '已展开' : item.state === 'pending' ? '等待处理' : stateLabels[item.state] ?? item.state }}</small>
        <small v-if="item.error">{{ item.error }}</small></div>
    </div>
  </section>
  <section class="panel">
    <div class="section-heading"><h2>最近任务</h2><RouterLink class="button-link" to="/operations/collections/new">新建采集</RouterLink></div>
    <p v-if="!jobs.length" class="muted">{{ connected ? '暂无采集任务。' : '连接管理服务后查看采集任务。' }}</p>
    <div v-for="job in jobs" :key="job.id" class="job" :data-state="job.state">
      <div class="job-info"><strong>{{ stateLabels[job.state] ?? job.state }}</strong><code>{{ job.id }}</code>
        <small>{{ job.error ?? stateLabels[job.verification] ?? job.verification }}{{ job.cache_hit ? ' · 缓存命中' : '' }}</small></div>
      <RouterLink v-if="job.state === 'succeeded' && job.artifact_sha256" :to="{ path: `/content/artifacts/${job.artifact_sha256}`, query: { parser_version: job.parser_version } }">查看制品</RouterLink>
      <button v-if="['queued','running'].includes(job.state)" @click="store.act(job, 'cancel')">取消</button>
      <button v-if="['failed','cancelled'].includes(job.state)" @click="store.act(job, 'retry')">重新采集</button>
    </div>
  </section>
</template>
