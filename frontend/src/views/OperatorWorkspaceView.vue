<script setup lang="ts">
/**
 * 业务操作工作台（移植自 Financial System-业务操作agent 前端 OperatorWorkspace）
 *
 * 功能对齐目标项目 React 版：
 *  - ConversationWorkspace  对话区（含二次确认/取消按钮）
 *  - OperationRail          8 操作快捷栏
 *  - RecommendedQuestions   推荐问题
 *  - ExecutionPath          解析→权限→确认→执行→审计 执行路径可视化
 *  - ResultDetails          操作结果详情
 *  - WorkOrderPanel         审计工单面板
 *  - 健康状态徽标
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { chatApi, type ChatPayload, type ChatResult } from '../api/chat'
import { profileApi } from '../api/profile'
import { riskHttp } from '../api/risk'
import { useAuth } from '../stores/auth'

interface AuditOrder {
  id: string
  workorder_no: string
  workorder_type: string
  customer_id: string | null
  customer_name?: string | null
  submitter_id: string | null
  status: string
  created_at: string | null
}

interface Entry {
  id: number
  role: 'user' | 'agent'
  text: string
}

type Phase = 'idle' | 'submitting' | 'confirmation' | 'completed' | 'failed'

interface OperationDef {
  action: string
  label: string
  short: string
  desc: string
  example: string
  risk: 'high' | 'medium' | 'low'
  roles: string[]
}

const OPERATIONS: OperationDef[] = [
  { action: 'purchase', label: '产品申购', short: '申购', desc: '为指定客户申购适当性匹配的产品', example: '帮零售投资者申购10000元的国债逆回购优选', risk: 'high', roles: ['financial_advisor'] },
  { action: 'redeem', label: '产品赎回', short: '赎回', desc: '赎回客户持有产品的全部或指定份额', example: '赎回零售投资者持有的国债逆回购优选全部份额', risk: 'high', roles: ['financial_advisor'] },
  { action: 'transfer', label: '客户转账', short: '转账', desc: '在两名客户账户之间资金划转', example: '把零售投资者的50000元转到高净值客户账户', risk: 'high', roles: ['customer_manager'] },
  { action: 'risk_reassess', label: '风评重做', short: '风评', desc: '冻结新增申购并要求客户重新风险评估', example: '零售投资者需要重新风险评估', risk: 'medium', roles: ['financial_advisor'] },
  { action: 'info_update', label: '信息更新', short: '更新', desc: '更新客户手机号（格式校验）', example: '把零售投资者的手机号改成13812345678', risk: 'medium', roles: ['customer_manager'] },
  { action: 'product_query', label: '产品查询', short: '查询', desc: '查询产品净值/风险/起投信息', example: '查询国债逆回购优选最新净值', risk: 'low', roles: ['financial_advisor', 'customer_manager', 'risk_specialist', 'auditor'] },
  { action: 'suspicious_report', label: '可疑上报', short: '上报', desc: '人工上报可疑交易并生成风控预警', example: '上报零售投资者的可疑交易', risk: 'high', roles: ['risk_specialist'] },
  { action: 'workorder_create', label: '工单创建', short: '工单', desc: '创建客户服务工单', example: '为零售投资者创建工单内容是投诉', risk: 'medium', roles: ['customer_manager'] },
]

const RECOMMENDED_ALL = [
  '帮零售投资者申购3000元国债逆回购优选',
  '赎回李伟持有的国债逆回购优选全部份额',
  '把李伟的20000元转到张明账户',
  '查询安鑫短期理财最新净值',
  '为李伟创建工单内容是投诉',
  '上报李伟的可疑交易',
]

// 推荐指令按角色过滤（6.2 意图权限）
const RECOMMENDED = computed(() => {
  const roles = auth.me.value?.roles || []
  return RECOMMENDED_ALL.filter((q, i) => {
    const op = OPERATIONS[i % OPERATIONS.length]
    return roles.some((r: string) => op.roles.includes(r))
  })
})

interface Candidate {
  id: string
  username: string
  display_name: string
}

interface CustomerOption {
  id: string
  username: string
  display_name: string
}

interface AmbiguousState {
  message: string
  candidates: Candidate[]
}

const auth = useAuth()
const entries = ref<Entry[]>([])
const input = ref('')
const phase = ref<Phase>('idle')
const pending = ref<ChatResult | null>(null)
const latest = ref<ChatResult | null>(null)
const error = ref('')
const health = ref<'checking' | 'online' | 'offline'>('checking')
const sessionId = ref(`op-${Date.now().toString(36)}`)
const workOrders = ref<AuditOrder[]>([])
const showOrders = ref(true)
const ambiguous = ref<AmbiguousState | null>(null)
const customers = ref<CustomerOption[]>([])
const selectedCustomer = ref<CustomerOption | null>(null)
const scrollRef = ref<HTMLElement | null>(null)
let seq = 0

const isAdvisor = computed(() => auth.me.value?.roles?.includes('financial_advisor') === true)
const isRisk = computed(() => auth.me.value?.roles?.includes('risk_specialist') === true)
const isManager = computed(() => auth.me.value?.roles?.includes('customer_manager') === true)

function add(role: 'user' | 'agent', text: string) {
  entries.value.push({ id: ++seq, role, text })
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

async function send(text: string, opts: { decision?: 'confirm' | 'cancel' | 'select_customer'; confirmationId?: string; selectedCustomerId?: string } = {}) {
  const message = text.trim()
  if (!message || phase.value === 'submitting') return
  error.value = ''
  phase.value = opts.decision === 'confirm' ? 'submitting' : opts.decision === 'cancel' ? 'idle' : 'submitting'
  add('user', message)
  input.value = ''
  try {
    const payload: ChatPayload = {
      message,
      agent: 'business_operator',
      session_id: sessionId.value,
      request_id: `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
    }
    if (opts.decision && opts.confirmationId) {
      payload.decision = opts.decision
      payload.confirmation_id = opts.confirmationId
    }
    if (opts.decision === 'select_customer' && opts.selectedCustomerId) {
      payload.decision = 'select_customer'
      payload.selected_customer_id = opts.selectedCustomerId
    }
    const res = await chatApi.send(payload)
    const result: ChatResult = res.data.data
    add('agent', result.summary)
    // 重名歧义：展示候选列表，待操作员选择
    const data = result.data as Record<string, unknown>
    if (data?.ambiguous === true && Array.isArray(data.candidates)) {
      ambiguous.value = {
        message,
        candidates: data.candidates as Candidate[],
      }
      phase.value = 'idle'
      return
    }
    ambiguous.value = null
    if (result.requires_confirmation) {
      pending.value = result
      phase.value = 'confirmation'
    } else if (result.status === 'success') {
      pending.value = null
      latest.value = result
      phase.value = 'completed'
      loadWorkOrders()
    } else {
      pending.value = null
      latest.value = result
      phase.value = result.status === 'success' ? 'completed' : 'failed'
      if (result.status !== 'success') error.value = result.summary
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '请求失败'
    phase.value = 'failed'
    add('agent', `[操作失败] ${error.value}`)
  }
}

// 重名消歧：选中目标客户后，回传选中 UUID 重发原指令
async function selectCustomer(candidate: Candidate) {
  if (!ambiguous.value) return
  const origin = ambiguous.value.message
  ambiguous.value = null
  await send(origin, { decision: 'select_customer', selectedCustomerId: candidate.id })
}

// 客户选择器：加载客户列表（含 id/username/display_name）
async function loadCustomers() {
  try {
    const resp = await profileApi.customers('', '', '', 100, 0)
    const items = ((resp.data as any)?.data?.items) || []
    customers.value = items.map((item: any) => ({
      id: item.id,
      username: item.username,
      display_name: item.display_name,
    }))
    if (customers.value.length) selectedCustomer.value = customers.value[0]
  } catch { /* 客户列表加载失败不阻断 */ }
}

// 客户 ID 前缀（解析器识别 → customer_id 优先提取，用数据库 users.id 整数主键）
function customerIdTag(c: CustomerOption): string {
  return `客户ID ${c.id}`
}

// 展示用短 ID（整数 ID 取前 8 位）
function shortId(id: string | number): string {
  return id !== undefined && id !== null && id !== '' ? String(id).slice(0, 8) : ''
}

// 操作示例：把客户标识替换为选中客户 ID
function fillExample(op: OperationDef): void {
  const c = selectedCustomer.value
  if (!c) {
    input.value = op.example
    return
  }
  // 转账：转出=选中客户，转入=另一个客户（避免同客户）
  if (op.action === 'transfer') {
    const other = customers.value.find((x) => x.id !== c.id)
    // 先替换转入（"高净值客户账户"含"账户"更长，避免被转出替换误伤），再替换转出
    input.value = op.example
      .replace(/高净值客户账户/, other ? `${customerIdTag(other)}账户` : '客户账户')
      .replace(/零售投资者|李伟|高净值客户/gi, customerIdTag(c))
    return
  }
  // 其它操作：替换客户名为「客户ID xxx」
  input.value = op.example.replace(/零售投资者|李伟|高净值客户/gi, customerIdTag(c))
}

// 推荐指令：替换客户名为选中客户 ID 后发送
function sendRecommended(q: string): void {
  const c = selectedCustomer.value
  if (!c) {
    send(q)
    return
  }
  // 转账类推荐：转出=选中客户，转入=另一个客户
  if (q.includes('转到')) {
    const other = customers.value.find((x) => x.id !== c.id)
    const replaced = q
      .replace(/张明|王芳账户/, other ? `${customerIdTag(other)}` : '王芳')
      .replace(/零售投资者|李伟|高净值客户/gi, customerIdTag(c))
    send(replaced)
    return
  }
  send(q.replace(/零售投资者|李伟|高净值客户/gi, customerIdTag(c)))
}

async function loadWorkOrders() {
  try {
    const res = await riskHttp.get<{ data: AuditOrder[] }>('/operation/audit-orders', { params: { limit: 20 } })
    workOrders.value = res.data.data || []
  } catch { /* 工单面板加载失败不阻断 */ }
}

onMounted(async () => {
  try {
    await chatApi.send({ message: '你好', agent: 'business_operator', session_id: sessionId.value })
    health.value = 'online'
  } catch { health.value = 'offline' }
  loadWorkOrders()
  loadCustomers()
})

function currentStep(): number {
  // 执行路径：1 解析 → 2 权限 → 3 确认 → 4 执行 → 5 审计
  if (phase.value === 'submitting') return 1
  if (phase.value === 'confirmation') return 3
  if (phase.value === 'completed') return 5
  if (phase.value === 'failed') return 4
  return 1
}

function orderId(): string {
  const d = latest.value?.data?.data as Record<string, unknown> | undefined
  return (d?.order_no as string) || (latest.value?.data?.workorder_no as string) || ''
}
</script>

<template>
  <div class="op-workspace">
    <header class="op-header">
      <div>
        <h2>业务操作工作台</h2>
        <p>自然语言下达 8 种业务操作指令，系统执行「解析 → 权限 → 确认 → 执行 → 审计」全链路</p>
      </div>
      <span class="op-health" :class="health">{{ health === 'online' ? '● 服务在线' : health === 'offline' ? '○ 服务离线' : '◌ 检测中' }}</span>
    </header>

    <!-- 执行路径可视化（ExecutionPath） -->
    <div class="op-path">
      <div v-for="(step, i) in ['指令解析', '权限校验', '二次确认', '业务执行', '审计留痕']" :key="step"
        class="op-path-step" :class="{ active: currentStep() >= i + 1, now: currentStep() === i + 1 }">
        <span class="op-path-dot">{{ i + 1 }}</span><span>{{ step }}</span>
      </div>
    </div>

    <div class="op-grid">
      <section class="op-card op-chat">
        <!-- 对话区（ConversationWorkspace） -->
        <div ref="scrollRef" class="op-messages">
          <div v-for="m in entries" :key="m.id" class="op-msg" :class="m.role">
            <div class="op-msg-bubble">{{ m.text }}</div>
          </div>
          <div v-if="!entries.length" class="op-empty">输入指令或点击右侧操作开始。示例：「帮零售投资者申购10000元的国债逆回购优选」</div>
        </div>
        <!-- 重名消歧候选选择 -->
        <div v-if="ambiguous" class="op-confirm op-disambig">
          <div class="op-confirm-text">
            <strong>⚠ 客户名称不唯一</strong>
            <span>「{{ ambiguous.message }}」命中多位客户，请选择目标客户：</span>
          </div>
          <div class="op-disambig-list">
            <button v-for="c in ambiguous.candidates" :key="c.id" class="op-btn ghost" @click="selectCustomer(c)">
              {{ c.display_name }}（{{ c.username }}）
            </button>
          </div>
        </div>
        <!-- 二次确认横幅 -->
        <div v-if="phase === 'confirmation' && pending" class="op-confirm">
          <div class="op-confirm-text">
            <strong>⚠ 高风险操作待确认</strong>
            <span>{{ pending.summary }}</span>
          </div>
          <div class="op-confirm-actions">
            <button class="op-btn danger" @click="send('确认执行', { decision: 'confirm', confirmationId: (pending.data as any)?.confirmation_id })">确认执行</button>
            <button class="op-btn ghost" @click="send('取消', { decision: 'cancel', confirmationId: (pending.data as any)?.confirmation_id })">取消</button>
          </div>
        </div>
        <div v-if="error" class="op-error">{{ error }}</div>
        <form class="op-input" @submit.prevent="send(input)">
          <input v-model="input" placeholder="输入业务操作指令，例如：帮客户申购10万元稳健增值计划" :disabled="phase === 'submitting'" />
          <button type="submit" class="op-btn primary" :disabled="phase === 'submitting'">{{ phase === 'submitting' ? '处理中…' : '发送' }}</button>
        </form>
      </section>

      <aside class="op-side">
        <!-- 8 操作快捷栏（OperationRail） -->
        <section class="op-card">
          <h3>业务操作</h3>
          <div class="op-customer-pick">
            <label>目标客户（数据库客户ID）</label>
            <select v-model="selectedCustomer">
              <option v-for="c in customers" :key="c.id" :value="c">{{ c.display_name }}（{{ c.username }} · {{ shortId(c.id) }}…）</option>
            </select>
          </div>
          <div class="op-rail">
            <button v-for="op in OPERATIONS.filter(o => auth.me.value?.roles?.some((r: string) => o.roles.includes(r)) || auth.me.value?.is_super_admin)" :key="op.action" class="op-rail-item" :class="op.risk" :title="op.desc"
              @click="fillExample(op)">
              <strong>{{ op.short }}</strong><span>{{ op.label }}</span>
            </button>
          </div>
        </section>
        <!-- 推荐问题（RecommendedQuestions） -->
        <section class="op-card">
          <h3>推荐指令</h3>
          <button v-for="q in RECOMMENDED" :key="q" class="op-reco" @click="sendRecommended(q)">{{ q }}</button>
        </section>
        <!-- 结果详情（ResultDetails） -->
        <section v-if="latest && phase === 'completed'" class="op-card">
          <h3>操作结果</h3>
          <dl class="op-result">
            <dt>订单号</dt><dd>{{ orderId() || '—' }}</dd>
            <dt>意图</dt><dd>{{ (latest.data?.intent as string) || latest.agent }}</dd>
            <dt>状态</dt><dd class="ok">{{ latest.status }}</dd>
          </dl>
          <pre class="op-json">{{ JSON.stringify(latest.data, null, 2) }}</pre>
        </section>
      </aside>
    </div>

    <!-- 审计工单面板（WorkOrderPanel） -->
    <section v-if="showOrders" class="op-card op-orders">
      <h3>审计工单 <small>业务操作自动留痕</small></h3>
      <table>
        <thead><tr><th>工单号</th><th>类型</th><th>客户</th><th>状态</th><th>创建时间</th></tr></thead>
        <tbody>
          <tr v-for="w in workOrders" :key="w.id">
            <td>{{ w.workorder_no }}</td>
            <td>{{ w.workorder_type }}</td>
            <td>{{ w.customer_name || w.customer_id || '—' }}</td>
            <td><span class="op-status" :class="w.status">{{ w.status }}</span></td>
            <td>{{ w.created_at?.slice(0, 19)?.replace('T', ' ') }}</td>
          </tr>
          <tr v-if="!workOrders.length"><td colspan="5" class="op-empty">暂无审计工单</td></tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.op-workspace { color: #1f2937; display: flex; flex-direction: column; gap: 14px; }
.op-header { display: flex; align-items: flex-start; justify-content: space-between; }
.op-header h2 { margin: 0; font-size: 20px; }
.op-header p { margin: 4px 0 0; font-size: 12px; color: #6b7280; }
.op-health { font-size: 12px; padding: 4px 10px; border-radius: 999px; background: #f3f4f6; }
.op-health.online { color: #059669; background: #ecfdf5; }
.op-health.offline { color: #dc2626; background: #fef2f2; }
.op-path { display: flex; gap: 4px; background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; }
.op-path-step { flex: 1; display: flex; align-items: center; gap: 6px; font-size: 12px; color: #9ca3af; }
.op-path-step .op-path-dot { width: 20px; height: 20px; border-radius: 50%; background: #f3f4f6; display: inline-flex; align-items: center; justify-content: center; font-size: 11px; }
.op-path-step.active { color: #374151; }
.op-path-step.active .op-path-dot { background: #dbeafe; color: #1d4ed8; }
.op-path-step.now .op-path-dot { background: #1d4ed8; color: #fff; }
.op-grid { display: grid; grid-template-columns: 1fr 320px; gap: 14px; }
.op-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; }
.op-card h3 { margin: 0 0 10px; font-size: 14px; }
.op-card h3 small { color: #9ca3af; font-weight: 400; }
.op-chat { display: flex; flex-direction: column; min-height: 420px; }
.op-messages { flex: 1; overflow-y: auto; max-height: 320px; display: flex; flex-direction: column; gap: 10px; padding: 4px; }
.op-msg { display: flex; }
.op-msg.user { justify-content: flex-end; }
.op-msg-bubble { max-width: 85%; padding: 8px 12px; border-radius: 12px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.op-msg.user .op-msg-bubble { background: #1d4ed8; color: #fff; border-bottom-right-radius: 2px; }
.op-msg.agent .op-msg-bubble { background: #f3f4f6; color: #1f2937; border-bottom-left-radius: 2px; }
.op-empty { color: #9ca3af; font-size: 12px; text-align: center; padding: 40px 0; }
.op-confirm { border: 1px solid #fbbf24; background: #fffbeb; border-radius: 10px; padding: 10px 12px; display: flex; align-items: center; gap: 12px; }
.op-confirm-text { flex: 1; display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: #92400e; }
.op-confirm-actions { display: flex; gap: 8px; }
.op-disambig-list { display: flex; flex-wrap: wrap; gap: 8px; }
.op-disambig .op-btn { border: 1px solid #d1d5db; background: #fff; }
.op-disambig .op-btn:hover { border-color: #1d4ed8; color: #1d4ed8; }
.op-error { color: #dc2626; font-size: 12px; padding: 6px 2px; }
.op-input { display: flex; gap: 8px; margin-top: 10px; }
.op-input input { flex: 1; border: 1px solid #d1d5db; border-radius: 8px; padding: 8px 12px; font-size: 13px; }
.op-input input:focus { outline: none; border-color: #1d4ed8; }
.op-side { display: flex; flex-direction: column; gap: 14px; }
.op-customer-pick { margin-bottom: 10px; }
.op-customer-pick label { display: block; font-size: 12px; color: #6b7280; margin-bottom: 4px; }
.op-customer-pick select { width: 100%; border: 1px solid #d1d5db; border-radius: 8px; padding: 6px 8px; font-size: 12px; background: #fff; }
.op-customer-pick select:focus { outline: none; border-color: #1d4ed8; }
.op-rail { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.op-rail-item { display: flex; flex-direction: column; gap: 2px; padding: 8px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; cursor: pointer; text-align: left; font-size: 12px; color: #374151; }
.op-rail-item:hover { border-color: #1d4ed8; }
.op-rail-item.high { border-left: 3px solid #dc2626; }
.op-rail-item.medium { border-left: 3px solid #f59e0b; }
.op-rail-item.low { border-left: 3px solid #10b981; }
.op-reco { display: block; width: 100%; text-align: left; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 7px 10px; margin-bottom: 6px; font-size: 12px; cursor: pointer; color: #374151; }
.op-reco:hover { border-color: #1d4ed8; color: #1d4ed8; }
.op-result { display: grid; grid-template-columns: 70px 1fr; gap: 6px 10px; font-size: 12px; margin: 0 0 8px; }
.op-result dt { color: #9ca3af; }
.op-result dd { margin: 0; word-break: break-all; }
.op-result dd.ok { color: #059669; }
.op-json { background: #0f172a; color: #a5f3fc; border-radius: 8px; padding: 10px; font-size: 11px; overflow-x: auto; margin: 0; }
.op-btn { border: none; border-radius: 8px; padding: 8px 14px; font-size: 13px; cursor: pointer; }
.op-btn.primary { background: #1d4ed8; color: #fff; }
.op-btn.danger { background: #dc2626; color: #fff; }
.op-btn.ghost { background: #f3f4f6; color: #374151; }
.op-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.op-orders table { width: 100%; border-collapse: collapse; font-size: 12px; }
.op-orders th, .op-orders td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #f3f4f6; }
.op-orders th { color: #6b7280; font-weight: 500; }
.op-status { padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #f3f4f6; color: #374151; }
.op-status.completed { background: #ecfdf5; color: #059669; }
.op-status.pending { background: #fffbeb; color: #b45309; }
@media (max-width: 900px) { .op-grid { grid-template-columns: 1fr; } }
</style>
