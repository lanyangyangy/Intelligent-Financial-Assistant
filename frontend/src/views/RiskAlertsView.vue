<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { riskAlertApi, type RiskAlert, type RiskAlertDetail, type WorkOrder } from '../api/riskAlert'

const alerts = ref<RiskAlert[]>([])
const workOrders = ref<WorkOrder[]>([])
const loading = ref(false)
const error = ref('')
const tab = ref<'alerts' | 'orders' | 'monitor'>('alerts')

const LEVEL_LABELS: Record<string, string> = { low: '轻度', medium: '中度', high: '重度' }
const COLOR_LABELS: Record<string, string> = { blue: '蓝色', yellow: '黄色', red: '红色' }
const STATUS_LABELS: Record<string, string> = { pending: '待处理', confirmed: '已确认', resolved: '已解决' }

// ── F4.1 交易事件监测（POST /api/risk/monitor）────────────────────
const monCustomer = ref<number | string>('')
const monTxnId = ref('')
const monAmount = ref<number | null>(null)
const monType = ref('transfer')
const monResult = ref<RiskAlertDetail | null>(null)
const monLoading = ref(false)
const monError = ref('')

// 预警详情
const detail = ref<RiskAlertDetail | null>(null)
const detailLoading = ref(false)

// 处理预警弹窗（替代 window.prompt，避免浏览器沙箱静默返回 null）
const handleTarget = ref<RiskAlert | null>(null)
const handleNote = ref('')

async function submitMonitor() {
  monError.value = ''; monResult.value = null
  if (!monCustomer.value || !monAmount.value || monAmount.value <= 0) {
    monError.value = '请填写客户ID与交易金额'; return
  }
  monLoading.value = true
  try {
    const r = await riskAlertApi.monitor({
      customer_id: monCustomer.value,
      transaction_id: monTxnId.value || `TXN-${Date.now()}`,
      amount: monAmount.value,
      transaction_type: monType.value,
    })
    const d = r.data.data
    if (d.alert_id) {
      // 命中规则 → 拉取完整详情
      const det = await riskAlertApi.alertDetail(d.alert_id)
      monResult.value = { ...det.data.data, alert_id: d.alert_id } as RiskAlertDetail
    } else {
      monResult.value = {
        id: '',
        customer_id: String(d.customer_id),
        alert_level: d.alert_level || '',
        alert_color: d.alert_color || '',
        alert_type: 'none',
        trigger_rules: d.rule_hits,
        confidence: d.confidence || 0,
        status: 'clear',
        handle_note: '',
        created_at: null,
        trigger_detail: d.message || '交易未命中风控规则',
        transaction_ids: [d.transaction_id],
        handler_id: null,
        handled_at: null,
        workorder_no: d.workorder_no || null,
      }
    }
    await load()
  } catch (e: any) { monError.value = e.message } finally { monLoading.value = false }
}

async function showDetail(alert: RiskAlert) {
  detailLoading.value = true; detail.value = null
  try {
    const r = await riskAlertApi.alertDetail(alert.id)
    detail.value = r.data.data
  } catch (e: any) { error.value = e.message } finally { detailLoading.value = false }
}

async function load() {
  loading.value = true; error.value = ''
  try {
    const [a, w] = await Promise.all([riskAlertApi.alerts(50), riskAlertApi.workOrders(50)])
    alerts.value = a.data.data
    workOrders.value = w.data.data
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
}

async function handleAlert(alert: RiskAlert) {
  // 用页面内联弹窗替代 window.prompt——浏览器沙箱/自动环境下
  // prompt() 会静默返回 null 导致"点击无响应"。
  handleTarget.value = alert
  handleNote.value = '已人工复核确认'
}

async function confirmHandle() {
  const alert = handleTarget.value
  if (!alert) return
  try {
    await riskAlertApi.handleAlert(alert.id, handleNote.value || '已确认')
    alert.status = 'confirmed'
    alert.handle_note = handleNote.value
    handleTarget.value = null
  } catch (e: any) { error.value = e.message }
}

function colorOf(c: string) {
  return { blue: '#1976d2', yellow: '#f0ad4e', red: '#c62828' }[c] || '#55617a'
}

onMounted(load)
</script>

<template>
  <section class="table-module">
    <div class="module-header">
      <div><span class="module-kicker">RISK ALERT</span><h2>风控预警与工单</h2>
        <p>反洗钱规则命中 → 分级预警 → 工单流转</p></div>
      <button class="button" :disabled="loading" @click="load">刷新</button>
    </div>
    <div class="tabs">
      <button :class="{ active: tab === 'alerts' }" @click="tab = 'alerts'">预警列表 ({{ alerts.length }})</button>
      <button :class="{ active: tab === 'orders' }" @click="tab = 'orders'">工单列表 ({{ workOrders.length }})</button>
      <button :class="{ active: tab === 'monitor' }" @click="tab = 'monitor'">交易事件监测</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading" class="empty">正在加载…</div>

    <template v-if="tab === 'monitor'">
      <div class="monitor-panel">
        <h3>F4.1 交易事件接收</h3>
        <p class="dim">提交单笔交易，触发反洗钱规则引擎匹配（大额/时段/整数金额/异常波动），命中后生成分级预警 + 工单 + 事件广播。</p>
        <div class="monitor-grid">
          <label>客户ID <input v-model.number="monCustomer" type="number" placeholder="如 31" /></label>
          <label>交易ID <input v-model="monTxnId" placeholder="如 TXN-001" /></label>
          <label>金额 <input v-model.number="monAmount" type="number" placeholder="如 60000" /></label>
          <label>交易类型
            <select v-model="monType">
              <option value="transfer">转账</option>
              <option value="purchase">申购</option>
              <option value="redeem">赎回</option>
              <option value="deposit">入金</option>
            </select>
          </label>
        </div>
        <button class="button" :disabled="monLoading" @click="submitMonitor">{{ monLoading ? '检测中…' : '提交检测' }}</button>
        <p v-if="monError" class="error">{{ monError }}</p>
        <div v-if="monResult" class="monitor-result">
          <template v-if="monResult.alert_id">
            <span class="pill" :style="{ background: colorOf(monResult.alert_color!) + '22', color: colorOf(monResult.alert_color!) }">
              {{ COLOR_LABELS[monResult.alert_color!] }} {{ LEVEL_LABELS[monResult.alert_level!] }}
            </span>
            <p>命中规则：{{ monResult.trigger_rules.join('；') }}</p>
            <p>置信度：{{ ((monResult.confidence ?? 0) / 100).toFixed(2) }} · 工单：{{ monResult.workorder_no || '—' }}</p>
          </template>
          <template v-else>
            <span class="pill" style="background:#e8f5e9;color:#2e7d32">未命中</span>
            <p>{{ monResult.trigger_detail }}</p>
          </template>
        </div>
      </div>
    </template>

    <template v-else-if="tab === 'alerts'">
      <div v-if="!alerts.length" class="empty">暂无预警记录</div>
      <table v-else class="data-table">
        <thead><tr><th>级别</th><th>规则</th><th>置信度</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="a in alerts" :key="a.id">
            <td><span class="pill" :style="{ background: colorOf(a.alert_color) + '22', color: colorOf(a.alert_color) }">{{ COLOR_LABELS[a.alert_color] }} {{ LEVEL_LABELS[a.alert_level] }}</span></td>
            <td class="rule-cell">{{ a.trigger_rules.join('；') || '—' }}</td>
            <td>{{ (a.confidence / 100).toFixed(2) }}</td>
            <td>{{ STATUS_LABELS[a.status] || a.status }}</td>
            <td class="time-cell">{{ a.created_at?.slice(0, 19).replace('T', ' ') }}</td>
            <td>
              <button class="table-action" @click="showDetail(a)">详情</button>
              <button v-if="a.status === 'pending'" class="table-action" @click="handleAlert(a)">处理</button>
              <span v-else class="dim">{{ a.handle_note }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-else>
      <div v-if="!workOrders.length" class="empty">暂无工单</div>
      <table v-else class="data-table">
        <thead><tr><th>工单号</th><th>类型</th><th>优先级</th><th>标题</th><th>状态</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="w in workOrders" :key="w.id">
            <td>{{ w.workorder_no }}</td>
            <td>{{ w.workorder_type }}</td>
            <td><span class="pill" :class="w.priority === 'high' ? 'pill-warn' : ''">{{ w.priority }}</span></td>
            <td>{{ w.title }}</td>
            <td>{{ STATUS_LABELS[w.status] || w.status }}</td>
            <td class="time-cell">{{ w.created_at?.slice(0, 19).replace('T', ' ') }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 预警详情弹窗 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal-box">
        <h3>预警详情</h3>
        <p v-if="detailLoading" class="dim">加载中…</p>
        <template v-else>
          <p><strong>预警ID：</strong>{{ detail.id }}</p>
          <p><strong>客户ID：</strong>{{ detail.customer_id }}</p>
          <p><strong>级别：</strong>
            <span class="pill" :style="{ background: colorOf(detail.alert_color) + '22', color: colorOf(detail.alert_color) }">
              {{ COLOR_LABELS[detail.alert_color] }} {{ LEVEL_LABELS[detail.alert_level] }}
            </span>
          </p>
          <p><strong>触发规则：</strong>{{ detail.trigger_rules.join('；') }}</p>
          <p><strong>触发详情：</strong>{{ detail.trigger_detail }}</p>
          <p><strong>置信度：</strong>{{ (detail.confidence / 100).toFixed(2) }}</p>
          <p><strong>关联交易：</strong>{{ detail.transaction_ids.join('、') || '—' }}</p>
          <p><strong>工单号：</strong>{{ detail.workorder_no || '—' }}</p>
          <p><strong>状态：</strong>{{ STATUS_LABELS[detail.status] || detail.status }}</p>
          <p v-if="detail.handle_note"><strong>处理备注：</strong>{{ detail.handle_note }}</p>
          <p class="time-cell"><strong>时间：</strong>{{ detail.created_at?.slice(0, 19).replace('T', ' ') }}</p>
        </template>
        <button class="button" style="margin-top: 12px" @click="detail = null">关闭</button>
      </div>
    </div>

    <!-- 处理预警弹窗 -->
    <div v-if="handleTarget" class="modal-mask" @click.self="handleTarget = null">
      <div class="modal-box">
        <h3>处理预警</h3>
        <p class="dim">预警ID：{{ handleTarget.id }} · 级别：{{ COLOR_LABELS[handleTarget.alert_color] }} {{ LEVEL_LABELS[handleTarget.alert_level] }}</p>
        <p class="dim">触发规则：{{ handleTarget.trigger_rules.join('；') }}</p>
        <label class="handle-note-label">处理备注
          <textarea v-model="handleNote" rows="3" placeholder="填写处理结论…"></textarea>
        </label>
        <div class="handle-actions">
          <button class="button" @click="handleTarget = null">取消</button>
          <button class="button button-primary" @click="confirmHandle">确认处理</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tabs { display: flex; gap: 8px; margin: 12px 0; }
.tabs button { padding: 8px 18px; border: 1px solid #d5ddeb; border-radius: 8px; background: #fff; cursor: pointer; font-size: 13px; }
.tabs button.active { background: #e6f7f4; border-color: #74d6c6; color: #0e7c6d; font-weight: 600; }
.data-table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; }
.data-table th { background: #f4f7fb; text-align: left; padding: 10px 14px; font-size: 12px; color: #55617a; }
.data-table td { padding: 10px 14px; border-top: 1px solid #eef1f6; font-size: 13px; }
.rule-cell { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.time-cell { color: #8a94a6; font-size: 12px; }
.pill-warn { background: #fff3cd; color: #8a6d1a; }
.table-action { border: 1px solid #74d6c6; color: #0e7c6d; background: #fff; border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
.dim { color: #8a94a6; font-size: 12px; }
.monitor-panel { border: 1px solid #e3e8f0; border-radius: 12px; padding: 20px; background: #fafbfd; }
.monitor-panel h3 { margin: 0 0 6px; }
.monitor-panel .dim { margin: 0 0 14px; line-height: 1.6; }
.monitor-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 14px; }
.monitor-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #55617a; }
.monitor-grid input, .monitor-grid select { padding: 8px 10px; border: 1px solid #d5ddeb; border-radius: 8px; font-size: 13px; outline: none; }
.monitor-grid input:focus, .monitor-grid select:focus { border-color: #74d6c6; }
.monitor-result { margin-top: 14px; padding: 14px; border: 1px solid #e3e8f0; border-radius: 10px; background: #fff; }
.monitor-result p { margin: 6px 0; font-size: 13px; }
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal-box { background: #fff; border-radius: 14px; padding: 24px; max-width: 520px; width: 90%; max-height: 80vh; overflow-y: auto; }
.modal-box h3 { margin: 0 0 14px; }
.modal-box p { margin: 8px 0; font-size: 13px; line-height: 1.6; }
.handle-note-label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: #55617a; margin-top: 12px; }
.handle-note-label textarea { padding: 10px; border: 1px solid #d5ddeb; border-radius: 8px; font-size: 13px; outline: none; resize: vertical; }
.handle-note-label textarea:focus { border-color: #74d6c6; }
.handle-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
.button-primary { background: #0e7c6d; border-color: #0e7c6d; color: #fff; }
.button-primary:hover { background: #0b6a5c; }
</style>
