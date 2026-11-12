<script setup lang="ts">
import {computed,ref} from 'vue';
import {useMirrorWorkspaceStore} from './stores/mirrorWorkspace';
const store=useMirrorWorkspaceStore();
const rules=computed(()=>store.draft.resource_prefetch.compatibility);
const games=computed({get:()=>rules.value.games.join('\n'),set:(value:string)=>{rules.value.games=[...new Set(value.split(/[\s,，;；]+/).filter(Boolean))];}});
const extra=ref(''),count=ref(5),error=ref('');
function add(){
  const game=extra.value.trim();
  if(!/^[A-Za-z0-9_.+ -]{1,64}$/.test(game)||!Number.isInteger(count.value)||count.value<0||count.value>200){error.value='请填写游戏版本，数量为 0–200。';return;}
  rules.value.overrides[game]=count.value;extra.value='';error.value='';
}
</script>
<template>
  <h3>按游戏版本准备</h3>
  <p class="workspace-hint">热门模组与加载器、光影核心共用这里的兼容范围。每个项目按发布时间从新到旧选择，问题版本先排除再计数；同一文件覆盖多个游戏版本时只存一份。</p>
  <div class="workspace-form-grid">
    <label>游戏范围<select v-model="rules.mode" aria-label="游戏范围"><option value="recent">最近 N 个正式游戏版本</option><option value="selected">指定游戏版本</option><option value="all">全部正式游戏版本</option></select></label>
    <label v-if="rules.mode==='recent'">最近游戏版本数<input v-model.number="rules.recent" type="number" min="1" max="200"></label>
    <label>每个游戏版本、每个项目的发布版本数<input v-model.number="rules.per_game" aria-label="每个游戏版本、每个项目的发布版本数" type="number" min="0" max="200"><small>例如填 5：为每个游戏版本准备最新 5 个兼容发布版本。</small></label>
  </div>
  <div v-if="rules.mode==='selected'" class="workspace-form"><label>指定的游戏版本<textarea v-model="games" aria-label="指定的游戏版本" rows="4" placeholder="1.21.1&#10;1.21.2&#10;1.21.3"></textarea><small>每行一个，也可用逗号分隔；按发布方声明的支持列表匹配。</small></label></div>
  <details class="workspace-help"><summary>单独调整某个游戏版本的数量 · {{Object.keys(rules.overrides).length}}</summary>
    <div v-for="(_,game) in rules.overrides" :key="game" class="workspace-toolbar"><label>{{game}}<input v-model.number="rules.overrides[game]" :aria-label="game+' 缓存数量'" type="number" min="0" max="200"></label><button type="button" class="secondary" @click="delete rules.overrides[game]">移除例外</button></div>
    <form class="workspace-toolbar" @submit.prevent="add"><input v-model="extra" aria-label="例外游戏版本" placeholder="例如 1.21.1" maxlength="64"><input v-model.number="count" aria-label="例外缓存数量" type="number" min="0" max="200"><button type="submit" class="secondary">添加数量例外</button></form>
    <p v-if="error" role="alert" class="workspace-alert">{{error}}</p>
  </details>
  <p class="workspace-hint">如果 1.21.1、1.21.2、1.21.3 都设为 5，且最新 5 个文件都支持这三个游戏版本，只缓存这 5 个文件。支持列表中的空档不会按“最低至最高”擅自补全；不足数量时保留实际可用版本。</p>
</template>
