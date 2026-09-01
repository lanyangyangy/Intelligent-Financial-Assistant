<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { chatApi, chatStream, AGENT_LABELS, type ChatHistoryMessage, type ChatResult } from '../api/chat'
import { isCustomerRoles } from '../constants/roles'
import { useAuth } from '../stores/auth'

interface ChatMessage {
  id: number
  role: 'user' | 'agent'
  content: string
  agent?: string
  agentLabel?: string
  confidence?: number
  requiresConfirmation?: boolean
  confirmed?: boolean
  confirmationId?: string
  evidence?: { source?: string; content?: string; score?: number }[]
  loading?: boolean
  error?: boolean
}

const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
const restoring = ref(false)
const scrollRef = ref<HTMLElement | null>(null)
const auth = useAuth()
const extractProfile = ref(false)
const canExtractProfile = computed(() => isCustomerRoles(auth.me.value?.roles))

// 会话 ID：同一会话多轮对话保持上下文（Redis 短期记忆）
const sessionId = ref('')

const isCustomer = computed(() => isCustomerRoles(auth.me.value?.roles))

// 建议问题：按需求文档 2.2 职责边界按角色展示（客户→客服；员工→其职责 Agent + 数据分析）
const SUGGESTIONS = computed(() => {
  if (isCustomer.value) {
    return [
      '你好，介绍一下你们有哪些理财产品？',
      '帮我推荐几款适合稳健型的产品',
      '资管新规对我的理财有什么影响？',
      '理财产品申购流程是怎样的？',
      '赎回资金几天到账？',
    ]
  }
  const roles = auth.me.value?.roles || []
  // 建议按钮按功能设计文档 6.2 意图权限（6.2 权限要求）
  const advisor = [
    '请为李伟推荐几款适合的产品',
    '请分析零售投资者的持仓',
    '请给李伟做资产配置方案',
    '帮零售投资者申购3000元国债逆回购优选',
    '赎回李伟持有的国债逆回购优选全部份额',
    '统计一下目前有多少客户',
  ]
  const risk = [
    '风控扫描一下零售投资者的交易记录',
    '上报零售投资者的可疑交易',
    '统计一下目前有多少客户',
  ]
  const operator = [
    '把李伟的20000元转到张明账户',
    '为李伟创建投诉工单',
    '把李伟的手机号改成13812345678',
    '统计一下目前有多少客户',
  ]
  const auditor = [
    '统计一下目前有多少客户',
    '查看待处理工单',
    '查看各产品平均收益率',
  ]
  if (roles.includes('financial_advisor')) return advisor
  if (roles.includes('risk_specialist')) return risk
  if (roles.includes('customer_manager')) return operator
  if (roles.includes('auditor')) return auditor
  // 系统管理员/其他：全部示例
  return [
    '请为李伟推荐几款适合的产品',
    '风控扫描一下零售投资者的交易记录',
    '帮零售投资者申购3000元国债逆回购优选',
    '把李伟的20000元转到张明账户',
    '统计一下目前有多少客户',
  ]
})

let nextId = 1

function chatSessionKey() {
  return `agent-chat:session:${auth.me.value?.id || auth.me.value?.username || 'anonymous'}`
}
function chatMessagesKey() {
  return `agent-chat:messages:${sessionId.value}`
}
function createSessionId() {
  return `web-${crypto.randomUUID().slice(0, 8)}`
}
function restoreMessages(history: ChatHistoryMessage[]) {
  messages.value = history.map((message) => ({
    id: nextId++,
    role: message.role === 'assistant' ? 'agent' : 'user',
    content: message.content,
  }))
}
function readBrowserMessages(): ChatHistoryMessage[] {
  try {
    const raw = sessionStorage.getItem(chatMessagesKey())
    const messages = raw ? JSON.parse(raw) : []
    return Array.isArray(messages) ? messages.filter((message) => (
      message?.role === 'user' || message?.role === 'assistant'
    )) : []
  } catch {
    return []
  }
}
function persistMessages() {
  if (!sessionId.value) return
  const history = messages.value
    .filter((message) => !message.loading && message.content)
    .slice(-20)
    .map((message) => ({
      role: message.role === 'agent' ? 'assistant' : 'user',
      content: message.content,
    }))
  sessionStorage.setItem(chatMessagesKey(), JSON.stringify(history))
}
async function restoreSession() {
  const savedSessionId = localStorage.getItem(chatSessionKey())
  sessionId.value = savedSessionId || createSessionId()
  localStorage.setItem(chatSessionKey(), sessionId.value)
  restoring.value = true
  try {
    const browserHistory = readBrowserMessages()
    const response = await chatApi.history(sessionId.value)
    const serverHistory = response.data.data || []
    restoreMessages(serverHistory.length >= browserHistory.length ? serverHistory : browserHistory)
  } catch {
    restoreMessages(readBrowserMessages())
  } finally {
    restoring.value = false
    scrollToBottom()
  }
}

watch(messages, persistMessages, { deep: true })

function pushMessage(msg: Omit<ChatMessage, 'id'>) {
  messages.value.push({ id: nextId++, ...msg })
  scrollToBottom()
}

function scrollToBottom() {
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

// 清空对话：清空消息、清浏览器本地缓存，并开启新会话（避免旧上下文干扰）
async function clearConversation() {
  if (sending.value || restoring.value) return
  if (messages.value.some((m) => m.requiresConfirmation && !m.confirmed)) {
    if (!window.confirm('存在待二次确认的操作，清空对话后将无法确认，确定清空吗？')) return
  }
  messages.value = []
  try { sessionStorage.removeItem(chatMessagesKey()) } catch { /* 忽略 */ }
  sessionId.value = createSessionId()
  try { localStorage.setItem(chatSessionKey(), sessionId.value) } catch { /* 忽略 */ }
  input.value = ''
}

onMounted(restoreSession)

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || sending.value || restoring.value) return
  input.value = ''
  pushMessage({ role: 'user', content })
  const lastMsg = pushMessageLoading()
  sending.value = true
  try {
    // SSE 流式：逐字打字机效果（Phase 5 F5.3）
    await chatStream(
      { message: content, session_id: sessionId.value, extract_profile: extractProfile.value },
      (delta) => {
        if (lastMsg.loading) {
          lastMsg.loading = false
          lastMsg.content = ''
        }
        lastMsg.content += delta
        scrollToBottom()
      },
      (meta) => {
        // SSE meta：agent + 二次确认凭据（requires_confirmation/confirmation_id）
        if (meta.agent) {
          lastMsg.agent = meta.agent
          lastMsg.agentLabel = AGENT_LABELS[meta.agent] || meta.agent
        }
        if (meta.requires_confirmation !== undefined) {
          lastMsg.requiresConfirmation = meta.requires_confirmation
        }
        if (meta.confirmation_id) {
          lastMsg.confirmationId = meta.confirmation_id
        }
      },
    )
    // SSE 流式后按 meta 状态补全（确认请求时 requiresConfirmation 已由
    // meta 设置；无输出时回退普通接口）
    if (!lastMsg.content) {
      // 流式无输出时回退到普通接口
      const r = await chatApi.send({ message: content, session_id: sessionId.value, extract_profile: extractProfile.value })
      applyResult(lastMsg, r.data.data)
    } else if (lastMsg.agentLabel === undefined || !lastMsg.agent) {
      lastMsg.agentLabel = '智能客服'
    }
  } catch (e: any) {
    // 流式失败降级为非流式
    try {
      const r = await chatApi.send({ message: content, session_id: sessionId.value, extract_profile: extractProfile.value })
      applyResult(lastMsg, r.data.data)
    } catch (e2: any) {
      if (lastMsg.loading) {
        lastMsg.loading = false
        lastMsg.content = `请求失败：${e2.message}`
        lastMsg.error = true
      }
    }
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

function pushMessageLoading(): ChatMessage {
  const msg: ChatMessage = { id: nextId++, role: 'agent', content: '', loading: true }
  messages.value.push(msg)
  // 返回响应式数组中的代理对象；直接返回 push 前的原始对象会导致
  // SSE 增量修改不触发 Vue 重渲染，最终只能在 sending 结束时一次性显示。
  return messages.value[messages.value.length - 1]!
}

function applyResult(msg: ChatMessage, data: ChatResult) {
  msg.loading = false
  msg.content = data.summary
  msg.agent = data.agent
  msg.agentLabel = AGENT_LABELS[data.agent] || data.agent
  msg.confidence = data.confidence
  msg.requiresConfirmation = data.requires_confirmation
  msg.confirmationId = (data.data as { confirmation_id?: string } | undefined)?.confirmation_id
  msg.confirmed = false
  msg.evidence = data.evidence?.slice(0, 3) || []
  msg.error = data.status === 'error'
}

// 找到最近一条用户消息（确认/取消时作为 payload.message 回传）
function lastUserMessage(): string {
  const found = messages.value
    .slice()
    .reverse()
    .find((m) => m.role === 'user' && !m.error)
  return found?.content || ''
}

async function confirm(msg: ChatMessage) {
  if (msg.confirmed || msg.loading) return
  msg.confirmed = true
  const userText = lastUserMessage()
  if (!userText) return
  msg.loading = true
  msg.content = '正在确认执行…'
  try {
    const payload: Parameters<typeof chatApi.send>[0] = {
      message: userText,
      session_id: sessionId.value,
    }
    // 确认需 confirmation_id 结构化协议；无凭据则提示重新发起。
    // 旧布尔 confirmed 会重发原指令，凭据丢失时可能被重新解析误执行，
    // 因此无凭据一律提示，不自动确认。
    if (msg.confirmationId) {
      payload.decision = 'confirm'
      payload.confirmation_id = msg.confirmationId
    } else {
      msg.loading = false
      msg.confirmed = false
      msg.content = '确认凭据已失效，请重新发起该操作后再选择确认。'
      msg.error = true
      scrollToBottom()
      return
    }
    const r = await chatApi.send(payload)
    applyResult(msg, r.data.data)
  } catch (e: any) {
    msg.loading = false
    msg.content = `确认失败：${e.message}`
    msg.error = true
  }
  scrollToBottom()
}

async function cancel(msg: ChatMessage) {
  if (msg.confirmed || msg.loading) return
  msg.confirmed = true
  const userText = lastUserMessage()
  if (!userText) return
  msg.loading = true
  msg.content = '正在取消…'
  try {
    const payload: Parameters<typeof chatApi.send>[0] = {
      message: userText,
      session_id: sessionId.value,
    }
    // 取消需 confirmation_id 结构化协议；无凭据则提示重新发起。
    // 绝不能把原指令当普通消息重发——后端会重新解析并可能直接执行！
    if (msg.confirmationId) {
      payload.decision = 'cancel'
      payload.confirmation_id = msg.confirmationId
    } else {
      msg.loading = false
      msg.confirmed = false
      msg.content = '确认凭据已失效，请重新发起该操作后再选择取消。'
      msg.error = true
      scrollToBottom()
      return
    }
    const r = await chatApi.send(payload)
    const data: ChatResult = r.data.data
    msg.loading = false
    msg.content = data.summary
    msg.agent = data.agent
    msg.agentLabel = AGENT_LABELS[data.agent] || data.agent
    msg.confidence = data.confidence
    msg.requiresConfirmation = false
    msg.confirmed = true
    msg.evidence = data.evidence?.slice(0, 3) || []
    msg.error = data.status === 'error'
  } catch (e: any) {
    msg.loading = false
    msg.content = `取消失败：${e.message}`
    msg.error = true
  }
  scrollToBottom()
}
</script>

<template>
  <section class="chat-module">
    <div class="section-heading">
      <div>
        <span class="eyebrow">INTELLIGENT ASSISTANT</span>
        <h2>智能助手</h2>
      </div>
      <button
        v-if="messages.length"
        class="chat-clear-btn"
        :disabled="sending || restoring"
        @click="clearConversation"
        title="清空当前对话并开启新会话"
      >
        🗑 清空对话
      </button>
    </div>

    <div ref="scrollRef" :class="['chat-window', { 'is-empty': !messages.length }]">
      <div v-if="!messages.length" class="chat-empty">
        <p class="chat-empty-title">试试以下问题 👇</p>
        <div class="chat-suggestions">
          <button v-for="s in SUGGESTIONS" :key="s" class="chat-suggestion" @click="send(s)">{{ s }}</button>
        </div>
      </div>

      <div v-for="msg in messages" :key="msg.id" :class="['chat-row', msg.role]">
        <div class="chat-avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
        <div class="chat-bubble-wrap">
          <div v-if="msg.role === 'agent'" class="chat-meta">
            <span class="chat-agent-tag" :class="'agent-' + (msg.agent || '')">
              {{ msg.agentLabel || '智能助手' }}
            </span>
            <span v-if="msg.confidence !== undefined" class="chat-confidence">
              置信度 {{ (msg.confidence * 100).toFixed(0) }}%
            </span>
          </div>
          <div :class="['chat-bubble', { 'chat-error': msg.error }]">
            <p class="chat-text" v-html="msg.content.replace(/\n/g, '<br>')"></p>
            <div v-if="msg.requiresConfirmation && msg.confirmationId && !msg.confirmed && !msg.loading" class="chat-confirm-bar">
              <span>该操作涉及资金变动，需二次确认。</span>
              <button class="button button-sm" :disabled="sending" @click="confirm(msg)">确认执行</button>
              <button class="button button-sm chat-cancel-btn" :disabled="sending" @click="cancel(msg)">取消</button>
            </div>
          </div>
          <div v-if="msg.evidence?.length" class="chat-evidence">
            <details v-for="(ev, i) in msg.evidence" :key="i">
              <summary>来源{{ i + 1 }}：{{ ev.source || '内部知识库' }}（score {{ (ev.score || 0).toFixed(4) }}）</summary>
              <p>{{ ev.content }}</p>
            </details>
          </div>
        </div>
      </div>
    </div>

    <div class="chat-input-bar">
      <input
        v-model="input"
        placeholder="输入指令或问题，例如：帮我申购3千元的现金管理保本计划"
        @keyup.enter="send()"
        :disabled="sending || restoring"
      />
      <button class="button" :disabled="sending || restoring || !input.trim()" @click="send()">
        {{ sending ? '发送中…' : restoring ? '恢复中…' : '发送' }}
      </button>
    </div>
    <label v-if="canExtractProfile" class="profile-extraction-consent">
      <input v-model="extractProfile" type="checkbox" :disabled="sending || restoring" />
      同意从本轮对话提取明确的个人信息并写入画像标签（风评 90% · LLM 提取 60% · 用户自述 40%）
    </label>
  </section>
</template>

<style scoped>
.chat-module { display: flex; flex-direction: column; gap: 16px; height: max(520px, calc(100dvh - 190px)); }
.chat-window {
  flex: 1 1 auto; min-height: 0; height: auto; overflow-y: auto; background: #fff;
  border: 1px solid #e3e8f0; border-radius: 12px; padding: 20px;
  display: flex; flex-direction: column; justify-content: flex-start; gap: 18px;
}
.chat-window::before { content: ''; flex: 1 0 auto; }
.chat-window.is-empty { justify-content: center; }
.chat-window.is-empty::before { display: none; }
.chat-empty { margin: 0; text-align: center; color: #8a94a6; padding: 40px 0; }
.chat-empty-title { font-weight: 600; margin-bottom: 14px; color: #55617a; }
.chat-suggestions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.chat-suggestion {
  border: 1px solid #d5ddeb; background: #f7f9fc; color: #3d4b66;
  padding: 8px 14px; border-radius: 999px; font-size: 13px; cursor: pointer;
  transition: all 0.15s;
}
.chat-suggestion:hover { background: #e8f5f3; border-color: #74d6c6; color: #0e7c6d; }
.chat-row { display: flex; gap: 10px; align-items: flex-start; }
.chat-row.user { flex-direction: row-reverse; }
.chat-avatar {
  width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700; color: #fff;
  background: linear-gradient(135deg, #74d6c6, #2aa79a);
}
.chat-row.user .chat-avatar { background: linear-gradient(135deg, #7a8db5, #55617a); }
.chat-bubble-wrap { max-width: 78%; display: flex; flex-direction: column; gap: 4px; }
.chat-row.user .chat-bubble-wrap { align-items: flex-end; }
.chat-meta { display: flex; align-items: center; gap: 8px; }
.chat-agent-tag {
  font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 999px;
  background: #eef3fa; color: #3d5a80;
}
.chat-agent-tag.agent-customer_service { background: #e3f2fd; color: #1976d2; }
.chat-agent-tag.agent-investment_advisor { background: #e8f5e9; color: #2e7d32; }
.chat-agent-tag.agent-risk_monitor { background: #ffebee; color: #c62828; }
.chat-agent-tag.agent-data_analyst { background: #f3e5f5; color: #6a1b9a; }
.chat-agent-tag.agent-business_operator { background: #fff3e0; color: #e65100; }
.chat-confidence { font-size: 12px; color: #8a94a6; }
.chat-bubble {
  background: #f4f7fb; border: 1px solid #e6ebf3; border-radius: 12px;
  border-top-left-radius: 2px; padding: 12px 16px; color: #33405c;
  line-height: 1.7; font-size: 14px;
}
.chat-row.user .chat-bubble { background: #e6f7f4; border-color: #c8efe9; border-top-left-radius: 12px; border-top-right-radius: 2px; }
.chat-bubble.chat-error { background: #fdf0f0; border-color: #f3caca; color: #a33; }
.chat-text { margin: 0; white-space: normal; }
.chat-confirm-bar {
  margin-top: 10px; padding-top: 10px; border-top: 1px dashed #cbd5e3;
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  font-size: 13px; color: #c7791a;
}
.chat-confirm-bar .chat-cancel-btn {
  background: #fff; color: #a33; border: 1px solid #f3caca;
}
.chat-confirm-bar .chat-cancel-btn:hover { background: #fdf0f0; }
.button-sm { padding: 4px 14px; font-size: 13px; }
.chat-evidence { display: flex; flex-direction: column; gap: 4px; }
.chat-evidence details {
  font-size: 12px; color: #6b7688; background: #fafbfd; border: 1px solid #eef1f6;
  border-radius: 8px; padding: 6px 10px;
}
.chat-evidence summary { cursor: pointer; font-weight: 500; }
.chat-evidence p { margin: 6px 0 2px; color: #55617a; line-height: 1.5; }
.chat-input-bar { display: flex; gap: 10px; }
.chat-input-bar input {
  flex: 1; padding: 12px 16px; border: 1px solid #d5ddeb; border-radius: 10px;
  font-size: 14px; outline: none; transition: border-color 0.15s;
}
.chat-input-bar input:focus { border-color: #74d6c6; }
.chat-input-bar input:disabled { background: #f4f6f9; }
.profile-extraction-consent { color: #71809a; font-size: 12px; display: flex; gap: 6px; align-items: center; }
.chat-clear-btn {
  align-self: flex-start; flex-shrink: 0;
  background: #fff; color: #71809a;
  border: 1px solid #d5ddeb; border-radius: 8px;
  padding: 6px 14px; font-size: 13px; cursor: pointer;
  transition: all 0.15s;
}
.chat-clear-btn:hover:not(:disabled) { color: #a33; border-color: #f3caca; background: #fdf7f7; }
.chat-clear-btn:disabled { opacity: 0.5; cursor: not-allowed; }
@media (max-width: 640px) {
  .chat-module { height: max(520px, calc(100dvh - 160px)); }
  .chat-window { min-height: 0; }
}
</style>
