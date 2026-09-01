<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { profileApi } from '../api/profile'

const emit = defineEmits<{ updated: [] }>()
const conversationText = ref('')
const extracting = ref(false)
const result = ref<any>(null)
const conflicts = ref<any[]>([])
const message = ref('')
const error = ref('')

const TAG_LABELS: Record<string, string> = {
  OCCUPATION: '职业',
  MONTHLY_INCOME: '月收入',
  TOTAL_ASSETS: '总资产',
  INVESTMENT_EXPERIENCE_YEARS: '投资经验',
  INVESTMENT_GOAL: '投资目标',
  LOSS_TOLERANCE: '亏损容忍度',
  PREFERRED_PRODUCT_TYPES: '偏好产品',
  INVESTABLE_ASSETS: '可投资资产',
}

function formatValue(value: unknown) {
  if (Array.isArray(value)) return value.join('、')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '—')
}

function formatConfidence(value: unknown) {
  return `${Math.round(Number(value || 0) * 100)}%`
}

async function loadConflicts() {
  try {
    const response = await profileApi.myConflicts()
    conflicts.value = response.data.data?.conflicts || []
  } catch {
    // 冲突查询失败不影响画像抽取结果展示。
  }
}

async function extract() {
  if (conversationText.value.trim().length < 10) {
    message.value = '请至少输入 10 个字，便于模型提取有效信息。'
    return
  }
  extracting.value = true
  message.value = ''
  error.value = ''
  try {
    const response = await profileApi.extractConversation({
      conversation_text: conversationText.value,
    })
    result.value = response.data.data
    await loadConflicts()
    emit('updated')
    message.value = `已提取 ${result.value.tags?.length || 0} 个标签，并完成画像治理。`
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message || '画像提取失败'
  } finally {
    extracting.value = false
  }
}

onMounted(loadConflicts)
</script>

<template>
  <article class="portal-panel customer-profile-ai-panel">
    <header class="customer-profile-ai-header">
      <div>
        <span class="module-kicker">AI PROFILE DIALOGUE</span>
        <h2>和 AI 对话完善画像</h2>
        <p>在客户个人画像面板中直接补充信息，LLM 只提取你明确表达的内容。</p>
      </div>
      <span class="pill">LLM 提取</span>
    </header>

    <div class="customer-profile-ai-chat">
      <div class="customer-profile-ai-bubble assistant">你好，请告诉我你的职业、收入、资产、投资经验、风险承受能力或产品偏好。</div>
      <div v-if="result" class="customer-profile-ai-bubble user">{{ conversationText }}</div>
      <div v-if="result" class="customer-profile-ai-bubble assistant">
        {{ result.summary }}
        <strong v-if="result.conflict_ids?.length" class="customer-profile-ai-conflict">已发现 {{ result.conflict_ids.length }} 项标签冲突，原画像值保持不变。</strong>
      </div>
    </div>

    <textarea
      v-model="conversationText"
      rows="3"
      placeholder="例如：我的真实投资经验是1年，目前能接受10%的波动，偏好基金和债券。"
      :disabled="extracting"
    ></textarea>
    <p class="customer-profile-ai-hint">标签初始置信度：风评问卷 90% · LLM 提取 60% · 用户自述 40%</p>
    <div class="customer-profile-ai-actions">
      <button class="button" :disabled="extracting || conversationText.trim().length < 10" @click="extract">
        {{ extracting ? '提取并治理中…' : '提取信息并写入画像' }}
      </button>
    </div>
    <p v-if="message" class="notice">{{ message }}</p>
    <p v-if="error" class="error">{{ error }}</p>

    <div v-if="result?.tags?.length" class="customer-profile-ai-tags">
      <strong>本次提取结果</strong>
      <span v-for="tag in result.tags" :key="tag.tag_code" class="customer-profile-ai-tag">
        {{ TAG_LABELS[tag.tag_code] || tag.tag_code }}：{{ formatValue(tag.tag_value ?? tag.value) }} · {{ formatConfidence(tag.confidence) }}
      </span>
    </div>

    <div v-if="conflicts.length" class="customer-profile-ai-conflicts">
      <strong>画像标签冲突记录</strong>
      <div v-for="conflict in conflicts" :key="conflict.conflict_id" class="customer-profile-ai-conflict-row">
        <span>{{ TAG_LABELS[conflict.tag_code] || conflict.tag_code }}</span>
        <span>原画像：{{ formatValue(conflict.left_value) }}（{{ formatConfidence(conflict.left_confidence) }}）</span>
        <span>对话提取：{{ formatValue(conflict.right_value) }}（{{ formatConfidence(conflict.right_confidence) }}）</span>
        <span :class="conflict.status === 'OPEN' ? 'conflict-open' : 'conflict-resolved'">{{ conflict.status === 'OPEN' ? '待确认' : '已记录' }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.customer-profile-ai-panel { margin-bottom: 14px; border: 1px solid #dbe6f7; background: linear-gradient(135deg, #fbfcff, #f3f7ff); }
.customer-profile-ai-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.customer-profile-ai-header h2 { margin: 5px 0; color: #263b59; }
.customer-profile-ai-header p { margin: 0; color: #71809a; font-size: 13px; }
.customer-profile-ai-chat { display: grid; gap: 8px; margin: 16px 0 10px; }
.customer-profile-ai-bubble { max-width: 78%; border-radius: 11px; padding: 9px 12px; color: #526581; font-size: 13px; line-height: 1.55; }
.customer-profile-ai-bubble.assistant { background: #eef3fb; }
.customer-profile-ai-bubble.user { justify-self: end; background: #e4f7f3; color: #245d59; }
.customer-profile-ai-conflict { display: block; margin-top: 5px; color: #a66c17; }
.customer-profile-ai-panel textarea { box-sizing: border-box; width: 100%; border: 1px solid #d5ddeb; border-radius: 8px; padding: 10px; resize: vertical; font: inherit; }
.customer-profile-ai-hint { margin: 7px 0; color: #8a94a6; font-size: 12px; }
.customer-profile-ai-actions { display: flex; justify-content: flex-end; }
.customer-profile-ai-tags, .customer-profile-ai-conflicts { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
.customer-profile-ai-tags strong, .customer-profile-ai-conflicts > strong { width: 100%; color: #526581; font-size: 13px; }
.customer-profile-ai-tag { border: 1px solid #dbe6f7; border-radius: 99px; background: #fff; color: #526581; padding: 5px 9px; font-size: 12px; }
.customer-profile-ai-conflicts { display: grid; gap: 7px; }
.customer-profile-ai-conflict-row { display: grid; grid-template-columns: 110px 1fr 1fr 70px; gap: 8px; align-items: center; border: 1px solid #f0d9a6; border-radius: 8px; background: #fffaf0; padding: 8px 10px; color: #6b4f1d; font-size: 12px; }
.conflict-open { color: #c7791a; font-weight: 700; }
.conflict-resolved { color: #2e7d32; font-weight: 700; }
@media (max-width: 760px) { .customer-profile-ai-conflict-row { grid-template-columns: 1fr; } }
</style>
