<script setup lang="ts">
import {productLinks} from './publicSeo';
</script>
<template><footer class="product-footer" aria-label="Bugg 产品服务"><strong>Bugg · 和朋友，再开一个世界</strong><div class="product-footer-links" aria-label="产品服务"><a v-for="link in productLinks" :key="link.href" :href="link.href">{{link.label}}</a><a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">京ICP备2021040327号</a></div><small class="product-footer-note">译文可能有误，请以作者原文为准。项目版权归原作者所有。</small></footer></template>
<style scoped>.product-footer{max-width:1280px;margin:2rem auto;padding:1.5rem var(--page-gutter,24px);border-top:1px solid var(--line,#e4dfe9);display:flex;gap:1rem;justify-content:space-between;flex-wrap:wrap;font-size:.8rem;color:var(--muted,#696372)}.product-footer-links{display:flex;gap:1.25rem;flex-wrap:wrap}.product-footer a{color:inherit;text-decoration:none}.product-footer a:hover{text-decoration:underline}.product-footer-note{flex-basis:100%;font-size:.75rem}</style>
