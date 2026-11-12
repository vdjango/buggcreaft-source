<script setup lang="ts">
import {resourceEndpoints as endpoints, resourceDownloadNotice} from "../resourceDownloads";
import {platformFetch as fetch} from '../../transport';
import {onMounted, onUnmounted, ref} from 'vue';
import {publicLinks} from '../publicSeo';
const props=defineProps<{seed:Record<string,string>}>();
const links=publicLinks(props.seed.publicMode==='mirror');
const state=ref('正在检查镜像服务'), count=ref('—'), size=ref('—');
const policies=ref<Record<string,string>>({});
const nodes=ref<{url:string,weight:number}[]>([]), accepting=ref(true);
const sourceCount=ref<number|null>(null);
const loading=ref(true), controller=new AbortController();
let deadline:number|undefined;
onUnmounted(()=>{controller.abort();window.clearTimeout(deadline);});
const modes:Record<string,string>={cache:'缓存并提供下载',redirect:'仅重定向',backup:'备份并重定向',disabled:'未启用 · 来源回退'};
onMounted(async()=>{deadline=window.setTimeout(()=>controller.abort(),5000);try{const response=await fetch('/mirror-api/status',{signal:controller.signal});if(!response.ok)throw Error();const data=await response.json();state.value=data.running?'镜像服务在线':'镜像服务待启动';policies.value=data.policies||{};nodes.value=data.nodes||[];accepting.value=data.accepting!==false;sourceCount.value=data.source_mappings||0;count.value=data.content_cache?Number(data.content_cache.files).toLocaleString('zh-CN'):'—';size.value=data.content_cache?(Number(data.content_cache.bytes)/1024**3).toFixed(2)+' GiB':'—';}catch{state.value='暂时无法读取镜像状态';}finally{loading.value=false;window.clearTimeout(deadline);}});
const mirrorAddress=props.seed.publicOrigin||(typeof location==='undefined'?'https://mirror.bugg.icu':location.origin);
const copyState=ref('复制地址');
async function copy(){try{await navigator.clipboard.writeText(mirrorAddress);copyState.value='地址已复制';}catch{copyState.value='请手动复制下方地址';}}
</script>

<template>
<main class="mirror-page source-home">
  <section class="source-intro">
    <div class="source-intro-copy">
      <div class="eyebrow">BUGG / RESOURCE STATION</div>
      <h1>准备好资源。<br><span>继续你的世界。</span></h1>
      <p>从第一次安装，到下一次出发。<br>游戏、加载器和运行依赖，在这里找到下载入口。</p>
      <div class="mirror-actions"><a class="button" :href="links.translation">发现模组与整合包 ↗</a><a class="button secondary" href="#mirror-connect">接入镜像</a></div>
      <p class="source-live" role="status"><span aria-hidden="true">◉</span> {{ state }}</p>
    </div>
    <div class="source-scene" aria-label="与新版 Bugg 启动器相同的世界风景">
      <div class="source-scene-brand"><span>B</span> Bugg <small>资源服务</small></div>
      <div class="source-scene-caption"><span>下一段旅程，从这里开始。</span><strong>世界很大。<br>准备好，再出发。</strong><div><span>游戏与运行环境</span><span>模组与译文</span></div></div>
    </div>
  </section>
  <section class="source-status" aria-label="镜像状态" :aria-busy="loading"><div><span>已缓存资源文件</span><strong :class="{'loading-value':loading}">{{ count }}</strong></div><div><span>资源缓存总量</span><strong :class="{'loading-value':loading}">{{ size }}</strong></div><div><span>内容完整性校验</span><strong>SHA-1 / SHA-256</strong></div><div><span>可用来源规则</span><strong>{{ sourceCount ?? '—' }}</strong></div></section>
  <p class="muted small">按内容摘要去重，合计本机暂存及已核验的本地、对象存储副本；不含等待下载的计划和房间私有临时文件。来源规则数不是缓存文件数。</p>
  <section class="mirror-section"><div class="mirror-section-head"><div><div class="eyebrow">PACK LIGHT. GO FURTHER.</div><h2>把等待，留给日出。</h2></div><p>按需获取资源，保留官方内容。<br>支持 Range 的下载可以从中断处继续。</p></div><div class="mirror-feature-grid"><article><span class="mirror-feature-number">01 / YOUR WORLD</span><h3>从一个原版世界开始</h3><p>游戏清单、客户端、服务端，还有声音与语言文件。按文件哈希保存缓存，避免重复存储。</p><span class="mirror-pill">游戏与原版资源</span></article><article><span class="mirror-feature-number">02 / YOUR PLAYSTYLE</span><h3>给玩法，多一些可能</h3><p>Forge、NeoForge、Fabric 与 Quilt 的元数据和运行依赖。实际可下载内容以当前策略为准。</p><span class="mirror-pill">加载器与依赖</span></article><article><span class="mirror-feature-number">03 / YOUR NEXT FIND</span><h3>不只下载，也读懂它</h3><p>在资源目录查看项目介绍与已有译文，发现模组和整合包。项目介绍与真实包内声明分开展示。</p><a :href="links.translation">去资源目录看看 →</a></article></div></section>
  <section id="mirror-connect" class="mirror-connect"><div class="mirror-section-head"><div><div class="eyebrow">FOR LAUNCHERS & CREATORS</div><h2>让你的启动器，也从这里出发。</h2></div><button class="secondary" type="button" @click="copy">{{ copyState }}</button></div><p class="muted">公开下载无需申请密钥。按接口文档映射下载地址，保留官方哈希校验，并设置启动器名称与版本号作为 User-Agent。</p><div class="mirror-address"><span>当前镜像地址</span><code>{{ mirrorAddress }}</code><small>支持 HEAD 与 Range；收到重定向时跟随目标地址。</small></div><div class="table-scroll"><table class="mirror-endpoints"><thead><tr><th>资源类型</th><th>接口路径</th><th>内容来源</th><th>当前策略</th></tr></thead><tbody><tr v-for="row in endpoints" :key="row[0]"><td>{{ row[0] }}</td><td><code>{{ row[1] }}</code></td><td>{{ row[2] }}</td><td>{{ modes[policies[row[3]]] || (loading ? '读取中' : '暂时无法读取') }}</td></tr></tbody></table></div><p class="muted small">{{ resourceDownloadNotice }}</p><div class="mirror-actions"><a class="button" href="/developers">查看开发者接入 →</a><a class="button secondary" href="/docs">阅读使用文档</a></div></section>
  <section id="mirror-network" class="mirror-section"><div class="mirror-section-head"><div><div class="eyebrow">A SHARED WAY FORWARD</div><h2>每一份资源，都有自己的去处。</h2></div><a href="/developers">了解源站与节点接入 →</a></div><div class="source-network"><div><h3>本地缓存、公开来源，协同提供下载。</h3><p class="muted">资源按后台策略缓存或跳转到可用来源。文件仍由启动器校验；第三方资源的版权归原权利人所有。</p><p class="muted small">资源下载节点和游戏联机节点是不同服务，接入方式与用途分别说明。</p></div><div class="source-network-state"><span class="eyebrow">CURRENT NETWORK</span><strong>{{ loading ? '正在读取分发状态' : state === '暂时无法读取镜像状态' ? '分发状态暂不可用' : accepting ? '本机按预算提供下载' : '本机优先转移资源下载' }}</strong><span>已通过检查的协作节点：{{ loading || state === '暂时无法读取镜像状态' ? '—' : nodes.length }}</span><a v-for="node in nodes" :key="node.url" :href="node.url+'/mirror-api/status'" target="_blank" rel="noopener noreferrer">{{ node.url }} ↗</a><small v-if="!loading && state !== '暂时无法读取镜像状态' && !nodes.length">暂无已通过检查的外部资源节点，下载按当前策略访问本地或上游。</small></div></div></section>
</main>
</template>
