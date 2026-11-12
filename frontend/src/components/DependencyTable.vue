<script setup lang="ts">
import type { Dependency } from '../api'
defineProps<{ rows: Dependency[] }>()
</script>

<template>
  <p v-if="rows.length === 0" class="muted">此份元数据未声明依赖；不代表运行时绝对没有依赖。</p>
  <p v-else class="muted table-hint">左右滑动表格，查看完整声明。</p>
  <div v-if="rows.length" class="table-wrap"><table>
    <thead><tr><th>目标 ID</th><th>声明关系</th><th>原始版本约束</th><th>环境 / 顺序</th></tr></thead>
    <tbody><tr v-for="(row, index) in rows" :key="index">
      <td><code>{{ row.target_id }}</code></td><td>{{ row.relation }}</td>
      <td><code>{{ JSON.stringify(row.version_raw) }}</code></td>
      <td>{{ row.environment ?? '未声明' }} / {{ row.ordering ?? '未声明' }}</td>
    </tr></tbody>
  </table></div>
</template>
