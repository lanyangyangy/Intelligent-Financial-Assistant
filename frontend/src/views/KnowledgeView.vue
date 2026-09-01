<script setup lang="ts">
import { ref } from 'vue'
import { knowledgeApi, type KnowledgeHit } from '../api/knowledge'
const query=ref(''); const hits=ref<KnowledgeHit[]>([]); const loading=ref(false); const error=ref(''); const mode=ref('')
async function search(){if(!query.value.trim())return; loading.value=true; error.value=''; try{const r=await knowledgeApi.search(query.value); hits.value=r.data.data.hits; mode.value=r.data.data.retrieval_mode}catch(e:any){error.value=e.message}finally{loading.value=false}}
</script>
<template><section><div class="section-heading"><div><span class="eyebrow">KNOWLEDGE SEARCH</span><h2>知识库问答基础链路</h2><p>输入问题，调用 Qwen Embedding 和 pgvector + FTS 混合检索。</p></div><span v-if="mode" class="pill">{{ mode }}</span></div><div class="search-box"><input v-model="query" @keyup.enter="search" placeholder="例如：投资者适当性管理"/><button class="button" :disabled="loading" @click="search">{{ loading?'检索中…':'开始检索' }}</button></div><p v-if="error" class="error">{{ error }}</p><div v-if="!loading && !hits.length" class="empty">还没有检索结果，输入问题开始。</div><article v-for="hit in hits" :key="hit.id" class="result"><div class="result-meta"><span>Chunk {{ hit.chunk_index }}</span><span>score {{ hit.score.toFixed(4) }}</span></div><p>{{ hit.content }}</p></article></section></template>
