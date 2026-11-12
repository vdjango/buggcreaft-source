<script setup lang="ts">
import {highlightParts,type SearchItem} from '../search'
defineProps<{items:SearchItem[]}>()
defineEmits<{similar:[item:SearchItem]}>()
const fields:Record<string,string>={title:'名称',summary:'简介',body:'正文'}
const types:Record<string,string>={mod:'模组',modpack:'整合包',resourcepack:'资源包',shader:'光影'}
</script>
<template>
  <article v-for="item in items" :key="item.project_id" class="panel search-result">
    <div class="result-eyebrow">{{item.resource.provider==='modrinth'?'Modrinth':'CurseForge'}} · {{types[item.resource_type]||item.resource_type}} <span v-if="item.match_types.includes('semantic')">· 语义匹配</span></div>
    <h2><a :href="`/projects/${item.resource.provider}/${item.resource.project_id}`">{{item.translated_title||item.project_title}}</a></h2>
    <p v-if="item.translated_title" class="muted">{{item.project_title}}</p>
    <div v-for="(snippet,index) in item.snippets" :key="index" class="search-snippet">
      <small>{{fields[snippet.field]||snippet.field}}{{snippet.preview?'预览':''}} · {{snippet.language}} · {{snippet.is_translation?'译文':'原文'}}</small>
      <p><template v-for="(part,i) in highlightParts(snippet)" :key="i"><mark v-if="part.marked">{{part.text}}</mark><template v-else>{{part.text}}</template></template></p>
    </div>
    <div class="result-actions"><a :href="item.source_url" target="_blank" rel="noopener noreferrer">原项目 ↗</a><button type="button" class="secondary" @click="$emit('similar',item)">功能相近</button><small>包内依赖尚未关联核验</small></div>
    <small v-if="item.coverage.truncated">正文超出本轮收录预算，当前搜索覆盖部分内容。</small>
  </article>
</template>
