<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { knowledgeApi, type KnowledgeDocument } from '../api/knowledge'

const docs = ref<KnowledgeDocument[]>([])
const loading = ref(false)
const error = ref('')
const message = ref('')

async function load() {
  loading.value = true; error.value = ''
  try {
    const r = await knowledgeApi.list()
    docs.value = r.data.data
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
}

async function removeDoc(doc: KnowledgeDocument) {
  if (!window.confirm(`确认删除文档「${doc.file_name}」？`)) return
  try {
    await knowledgeApi.delete(doc.id)
    message.value = `已删除「${doc.file_name}」`
    await load()
  } catch (e: any) { error.value = e.message }
}

function fileSize(size: number) {
  if (size > 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB'
  return Math.round(size / 1024) + ' KB'
}

onMounted(load)
</script>

<template>
  <section class="table-module">
    <div class="module-header">
      <div><span class="module-kicker">KNOWLEDGE BASE</span><h2>知识库管理</h2>
        <p>文档元数据（fin_knowledge_meta）· 上传 / 查询 / 删除</p></div>
      <button class="button" :disabled="loading" @click="load">刷新</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="message" class="success">{{ message }}</p>
    <div v-if="loading" class="empty">正在加载…</div>
    <div v-else-if="!docs.length" class="empty">暂无知识文档</div>
    <table v-else class="data-table">
      <thead><tr><th>文件名</th><th>类型</th><th>分类</th><th>大小</th><th>状态</th><th>上传时间</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="d in docs" :key="d.id">
          <td>{{ d.file_name }}</td>
          <td>{{ d.file_type }}</td>
          <td><span class="pill">{{ d.category }}</span></td>
          <td>{{ fileSize(d.file_size) }}</td>
          <td><span class="pill" :class="d.status === 'active' ? 'pill-ok' : ''">{{ d.status }}</span></td>
          <td class="time-cell">{{ d.created_at.slice(0, 19).replace('T', ' ') }}</td>
          <td><button class="table-action danger" @click="removeDoc(d)">删除</button></td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.data-table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; }
.data-table th { background: #f4f7fb; text-align: left; padding: 10px 14px; font-size: 12px; color: #55617a; }
.data-table td { padding: 10px 14px; border-top: 1px solid #eef1f6; font-size: 13px; }
.time-cell { color: #8a94a6; font-size: 12px; }
.pill-ok { background: #e6f7e6; color: #2e7d32; }
.table-action { border: 1px solid #e0a3a3; color: #c62828; background: #fff; border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
</style>
