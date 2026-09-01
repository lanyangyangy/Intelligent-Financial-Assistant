<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { chatApi, type ChatResult } from '../api/chat'
import { profileApi } from '../api/profile'
import { useAuth } from '../stores/auth'

/* ------------------------------------------------------------------ */
/* 类型定义（与后端 app/agents/advisor_agent.py 返回 data 对齐）        */
/* ------------------------------------------------------------------ */
interface AdvisorProduct {
  product_id: string
  name: string
  product_type: string
  risk_level: string
  term_days: number
  minimum_amount: number
  liquidity: string
  description: string
  final_score: number
  score_breakdown: {
    return: number
    risk_match: number
    term_match: number
    min_amount: number
    graph_signal: number
  }
  industry?: string
}

interface AdvisorExcluded {
  product_id: string
  name: string
  risk_level: string
  reasons: string[]
}

interface AdvisorHolding {
  product: string
  product_id: string
  product_type: string
  risk_level: string
  liquidity: string
  term_days: number
  quantity: number
  market_value: number
  cost_amount: number
  profit_loss: number
  profit_loss_pct: number
  holding_days: number
  weight: number
}

interface AdvisorPortfolio {
  items: AdvisorHolding[]
  total_market_value: number
  total_cost: number
  total_profit_loss: number
  risk_distribution: Record<string, number>
  type_distribution: Record<string, number>
}

interface AdvisorProfile {
  user_id: string
  customer_type: string
  customer_tier: string
  risk_level: string
  investment_goal: string
  investment_horizon_years: number
  total_asset: number
  investable_asset: number
  holding_count: number
  profile_status: string
  suitability_confidence: number
  risk_alert?: {
    level?: string
    score?: number
    trigger_rules?: string[]
    updated_at?: string
    confidence?: number
  } | null
}

interface AdvisorData {
  found?: boolean
  profile_found?: boolean
  blocked_by_risk?: boolean
  // 产品推荐
  products?: AdvisorProduct[]
  reasons?: string[]
  excluded?: AdvisorExcluded[]
  excluded_count?: number
  guard?: string | null
  profile?: AdvisorProfile
  risk_alert?: AdvisorProfile['risk_alert']
  // 持仓分析
  analysis?: string
  portfolio?: AdvisorPortfolio
  top3_weight?: number
  total_pnl_pct?: number
  industry_distribution?: Record<string, number>
  // 资产配置
  allocation?: Record<string, number>
  risk_level?: string
  total_asset?: number
  investable_asset?: number
  current_allocation?: Record<string, number>
  portfolio_total?: number
  adjustments?: Array<{ category: string; current_ratio: number; target_ratio: number; deviation: number; action: string }>
  review_required?: boolean
  // 客户对比
  common?: string[]
  only_a?: string[]
  only_b?: string[]
  risk_difference?: { a?: string | null; b?: string | null; different?: boolean }
  concentration?: { a?: { top_industry?: string | null; top_ratio?: number; level?: string }; b?: { top_industry?: string | null; top_ratio?: number; level?: string } }
  advice?: { a?: string[]; b?: string[] }
}

interface CustomerOption {
  id: string
  username: string
  display_name: string
  risk_level: string | null
  customer_tier: string
  holding_count: number
  investable_asset: number
}

/* ------------------------------------------------------------------ */
/* 常量与工具                                                          */
/* ------------------------------------------------------------------ */
type Mode = 'product' | 'portfolio' | 'allocation' | 'comparison'

const PRESETS: Array<{ mode: Mode; label: string; desc: string; icon: string }> = [
  { mode: 'product', label: '产品推荐', desc: '适当性过滤 + 五因子评分排序', icon: '◈' },
  { mode: 'portfolio', label: '持仓分析', desc: '持仓明细、集中度、行业分布', icon: '▦' },
  { mode: 'allocation', label: '资产配置', desc: '按风险等级生成目标配置比例', icon: '◔' },
  { mode: 'comparison', label: '客户对比', desc: '两位客户共同与独有持仓', icon: '⇄' },
]

const MODE_TITLE: Record<Mode, string> = {
  product: '产品推荐',
  portfolio: '持仓分析',
  allocation: '资产配置建议',
  comparison: '客户对比',
}

const REASON_TEXT: Record<string, string> = {
  risk_level: '产品风险高于客户承受等级',
  minimum_amount: '可投资资产低于起投金额',
  customer_type: '客户类型不符合产品要求',
  suitability: '未通过适当性校验',
}

const RISK_ALERT_TEXT: Record<string, string> = {
  high: '红色预警：该客户存在高风险信号，投顾已暂停产品推荐，建议先核实风险并处理预警工单',
  medium: '中度风控预警：推荐已降低产品风险档位，建议人工复核后再与客户沟通',
  low: '轻度风控预警：推荐已降低产品风险档位，建议人工复核',
}

const ACTION_TEXT: Record<string, string> = {
  increase: '建议增配',
  reduce: '建议减配',
  hold: '保持',
}

const FACTOR_LABELS: Record<string, string> = {
  return: '收益',
  risk_match: '风险匹配',
  term_match: '期限匹配',
  min_amount: '起投金额',
  graph_signal: '图谱分散',
}

const RISK_COLOR: Record<string, string> = {
  R1: '#2f9e68', R2: '#3b82c4', R3: '#d29a2b', R4: '#d96c3f', R5: '#c23b52',
}

const TIER_LABEL: Record<string, string> = {
  ordinary: '普通', vip: 'VIP', private: '私行', enterprise: '企业',
}

// 产品类型中文映射（产品表存的英文代码）
const PRODUCT_TYPE_LABEL: Record<string, string> = {
  cash_management: '现金管理',
  fixed_income: '固收理财',
  equity_fund: '股票基金',
  balanced_fund: '平衡配置',
  private_strategy: '私募策略',
  qdii_fund: 'QDII基金',
  bond_fund: '债券基金',
  money_fund: '货币基金',
}
function productTypeLabel(value: string | undefined): string {
  if (!value) return '未分类'
  return PRODUCT_TYPE_LABEL[value] || value
}

// 收益率：后端 total_pnl_pct / profit_loss_pct 已是百分数值（如 7.10），直接加符号显示
function signedPctValue(value: unknown): string {
  const num = Number(value || 0)
  return `${num > 0 ? '+' : ''}${Math.round(num * 100) / 100}%`
}

function money(value: unknown): string {
  const num = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(num)
}
function moneyShort(value: unknown): string {
  const num = Number(value || 0)
  if (num >= 100000000) return `${(num / 100000000).toFixed(1)} 亿`
  if (num >= 10000) return `${(num / 10000).toFixed(0)} 万`
  return `${money(num)} 元`
}
function signedPct(value: unknown): string {
  const num = Number(value || 0)
  return `${num > 0 ? '+' : ''}${Math.round(num * 100)}%`
}
function profitClass(value: unknown): string {
  const num = Number(value || 0)
  if (num > 0) return 'is-profit'
  if (num < 0) return 'is-loss'
  return ''
}
function riskBadgeStyle(level: string) {
  return { background: `${RISK_COLOR[level] || '#8b98a8'}22`, color: RISK_COLOR[level] || '#8b98a8', borderColor: `${RISK_COLOR[level] || '#8b98a8'}55` }
}

/* ------------------------------------------------------------------ */
/* 状态                                                                */
/* ------------------------------------------------------------------ */
const auth = useAuth()
const mode = ref<Mode>('product')
const customers = ref<CustomerOption[]>([])
const customerId = ref('')
const customerId2 = ref('')
const loading = ref(false)
const loadingCustomers = ref(false)
const error = ref('')
const result = ref<ChatResult | null>(null)

const advisorData = computed<AdvisorData>(() => (result.value?.data || {}) as AdvisorData)
const agentLabel = computed(() => result.value?.agent || '')
const summary = computed(() => result.value?.summary || '')
const evidence = computed(() => result.value?.evidence || [])
const confidence = computed(() => result.value?.confidence || 0)

const selectedCustomer = computed(() => customers.value.find(c => c.id === customerId.value))
const selectedCustomer2 = computed(() => customers.value.find(c => c.id === customerId2.value))
const isComparison = computed(() => mode.value === 'comparison')
const canRun = computed(() => {
  if (loading.value) return false
  if (!customerId.value) return false
  if (isComparison.value && !customerId2.value) return false
  return true
})

const allocationItems = computed(() => {
  const alloc = advisorData.value.allocation
  if (!alloc) return []
  const total = Object.values(alloc).reduce((a, b) => a + b, 0) || 1
  return Object.entries(alloc).map(([category, ratio]) => ({
    category,
    ratio,
    pct: Math.round((ratio / total) * 100),
  }))
})

const riskAlertLevel = computed(() => {
  const alert = advisorData.value.risk_alert || advisorData.value.profile?.risk_alert
  return alert?.level || ''
})
const riskAlertConfidence = computed(() => {
  const alert = advisorData.value.risk_alert || advisorData.value.profile?.risk_alert
  return alert?.confidence || 0
})
const riskAlertRules = computed(() => {
  const alert = advisorData.value.risk_alert || advisorData.value.profile?.risk_alert
  return (alert?.trigger_rules || []).join('、')
})

const portfolioItems = computed(() => advisorData.value.portfolio?.items || [])
const riskDistribution = computed(() => advisorData.value.portfolio?.risk_distribution || {})
const typeDistribution = computed<Record<string, number>>(() => {
  const raw = advisorData.value.portfolio?.type_distribution || {}
  const out: Record<string, number> = {}
  for (const [key, value] of Object.entries(raw)) out[productTypeLabel(key)] = value
  return out
})
const industryDistribution = computed(() => advisorData.value.industry_distribution || {})

function reasonLabel(code: string): string {
  return REASON_TEXT[code] || code
}

async function loadCustomers() {
  loadingCustomers.value = true
  try {
    const resp = await profileApi.customers('', '', '', 100, 0)
    const items = (resp.data as any)?.data?.items || []
    customers.value = items.map((item: any) => ({
      id: item.id,
      username: item.username,
      display_name: item.display_name,
      risk_level: item.risk_level || null,
      customer_tier: item.customer_tier || 'ordinary',
      holding_count: item.holding_count || 0,
      investable_asset: Number(item.latest_asset?.investable_asset || 0),
    }))
    // 默认选中第一位有画像的客户
    if (!customerId.value && customers.value.length) {
      customerId.value = customers.value[0].id
    }
  } catch (e: any) {
    error.value = `加载客户列表失败：${e?.response?.data?.detail || e?.message || e}`
  } finally {
    loadingCustomers.value = false
  }
}

function selectMode(next: Mode) {
  mode.value = next
  error.value = ''
  if (next !== 'comparison') customerId2.value = ''
}

async function analyze() {
  if (!canRun.value) return
  loading.value = true
  error.value = ''
  result.value = null
  const customer = selectedCustomer.value
  if (!customer) {
    error.value = '请先选择客户'
    loading.value = false
    return
  }
  let message = ''
  let payload: Record<string, unknown> = {
    agent: 'investment_advisor',
    customer_id: customer.id,
    session_id: `advisor-web-${crypto.randomUUID().slice(0, 8)}`,
  }
  if (mode.value === 'product') {
    // 注意：后端意图路由按关键词匹配，消息中避免出现"持仓/配置/对比"等触发词
    message = `请结合客户${customer.display_name}的风险等级、资产规模与投资目标，推荐适合的产品，并说明推荐理由和主要风险。`
  } else if (mode.value === 'portfolio') {
    message = `请分析客户${customer.display_name}的当前持仓、收益和行业集中度，并提示需要关注的风险。`
  } else if (mode.value === 'allocation') {
    message = `请根据客户${customer.display_name}的风险等级和现有持仓，给出资产配置比例建议。`
  } else {
    const customerB = selectedCustomer2.value
    if (!customerB) {
      error.value = '客户对比需要选择两个客户'
      loading.value = false
      return
    }
    message = `请对比客户${customer.display_name}和客户${customerB.display_name}的风险等级、共同持仓和配置差异`
    payload.target_customer_id = customerB.id
  }
  payload.message = message
  try {
    const resp = await chatApi.send(payload as any)
    result.value = (resp.data as any)?.data as ChatResult
  } catch (e: any) {
    error.value = `投顾分析失败：${e?.response?.data?.detail || e?.message || e}`
  } finally {
    loading.value = false
  }
}

onMounted(loadCustomers)
</script>

<template>
  <section class="advisor-workspace">
    <header class="advisor-header">
      <div>
        <span class="module-kicker">ADVISOR ASSISTANT</span>
        <h2>投顾工作台</h2>
        <p>适当性核验 · 五因子评分排序 · 图谱分散度 · 合规护栏</p>
      </div>
      <span v-if="selectedCustomer" class="advisor-chip">
        {{ selectedCustomer.display_name }}
        <template v-if="selectedCustomer.risk_level"> · {{ selectedCustomer.risk_level }}</template>
        <template v-if="selectedCustomer.customer_tier"> · {{ TIER_LABEL[selectedCustomer.customer_tier] || selectedCustomer.customer_tier }}客户</template>
      </span>
    </header>

    <!-- 客户选择 -->
    <section class="advisor-panel advisor-customer-panel">
      <div class="advisor-preset-grid">
        <button
          v-for="preset in PRESETS"
          :key="preset.mode"
          type="button"
          class="advisor-preset-card"
          :class="{ active: mode === preset.mode }"
          :disabled="loading"
          @click="selectMode(preset.mode)"
        >
          <span class="advisor-preset-icon">{{ preset.icon }}</span>
          <div>
            <strong>{{ preset.label }}</strong>
            <small>{{ preset.desc }}</small>
          </div>
        </button>
      </div>

      <div class="advisor-customer-row">
        <label>
          <span>{{ isComparison ? '客户 A' : '目标客户' }}</span>
          <select v-model="customerId" :disabled="loading || loadingCustomers">
            <option v-if="loadingCustomers" value="">加载客户中…</option>
            <option v-if="!customers.length && !loadingCustomers" value="">暂无客户</option>
            <option v-for="c in customers" :key="c.id" :value="c.id">
              {{ c.display_name }}（{{ c.username }} · {{ c.risk_level || '未测评' }} · {{ moneyShort(c.investable_asset) }}）
            </option>
          </select>
        </label>
        <label v-if="isComparison">
          <span>客户 B</span>
          <select v-model="customerId2" :disabled="loading || loadingCustomers">
            <option value="">请选择第二个客户</option>
            <option v-for="c in customers" :key="c.id" :value="c.id" :disabled="c.id === customerId">
              {{ c.display_name }}（{{ c.username }} · {{ c.risk_level || '未测评' }}）
            </option>
          </select>
        </label>
        <button class="advisor-run-button" type="button" :disabled="!canRun" @click="analyze">
          {{ loading ? '分析中…' : '生成投顾分析' }}
        </button>
      </div>
      <p class="advisor-hint">
        <template v-if="isComparison">对比分析将检索两位客户的持仓交集与差异，并结合图谱验证。</template>
        <template v-else-if="mode === 'product'">推荐结果按 收益30% / 风险匹配25% / 期限15% / 起投15% / 图谱分散15% 排序，并自动过滤不适当产品。</template>
        <template v-else-if="mode === 'portfolio'">持仓分析包含持仓明细、集中度、风险分布与 LLM 深度解读。</template>
        <template v-else>配置建议按客户风险等级 C1-C5 模板生成，并写入画像标签。</template>
      </p>
    </section>

    <p v-if="error" class="advisor-error" role="alert">{{ error }}</p>

    <!-- 结果区 -->
    <template v-if="result">
      <section class="advisor-panel advisor-result">
        <div class="advisor-result-head">
          <div>
            <span class="module-kicker">STRUCTURED RESULT · {{ MODE_TITLE[mode] }}</span>
            <p class="advisor-summary">{{ summary }}</p>
          </div>
          <div class="advisor-result-meta">
            <span class="advisor-agent-tag">{{ agentLabel }}</span>
            <span class="advisor-confidence">置信度 {{ (confidence * 100).toFixed(0) }}%</span>
          </div>
        </div>

        <!-- 客户画像概览 -->
        <div v-if="advisorData.profile" class="advisor-profile-grid">
          <div class="advisor-profile-cell"><span>风险等级</span><strong :style="riskBadgeStyle(advisorData.profile.risk_level)">{{ advisorData.profile.risk_level }}</strong></div>
          <div class="advisor-profile-cell"><span>投资目标</span><strong>{{ advisorData.profile.investment_goal || '—' }}</strong></div>
          <div class="advisor-profile-cell"><span>可投资资产</span><strong>{{ moneyShort(advisorData.profile.investable_asset) }}</strong></div>
          <div class="advisor-profile-cell"><span>总资产</span><strong>{{ moneyShort(advisorData.profile.total_asset) }}</strong></div>
          <div class="advisor-profile-cell"><span>持仓数</span><strong>{{ advisorData.profile.holding_count }}</strong></div>
          <div class="advisor-profile-cell"><span>画像状态</span><strong>{{ advisorData.profile.profile_status || '—' }}</strong></div>
          <div class="advisor-profile-cell"><span>适当性置信度</span><strong>{{ ((advisorData.profile.suitability_confidence || 0) * 100).toFixed(0) }}%</strong></div>
          <div class="advisor-profile-cell"><span>客户类型</span><strong>{{ advisorData.profile.customer_type === 'enterprise' ? '企业客户' : '个人客户' }}</strong></div>
        </div>

        <!-- 合规护栏 -->
        <div v-if="advisorData.guard" class="advisor-guard">
          <span class="advisor-guard-icon">⚠</span>
          <div><strong>合规护栏</strong><p>{{ advisorData.guard }}</p></div>
        </div>

        <!-- 跨 Agent 风控联动横幅 -->
        <div v-if="riskAlertLevel" class="advisor-risk-banner" :data-level="riskAlertLevel">
          <span class="advisor-guard-icon">{{ riskAlertLevel === 'high' ? '🔴' : '🟡' }}</span>
          <div>
            <strong>风控联动</strong>
            <p>{{ RISK_ALERT_TEXT[riskAlertLevel] || '该客户存在风控预警信号' }}
              <template v-if="riskAlertRules">（触发规则：{{ riskAlertRules }}）</template>
              <template v-if="riskAlertConfidence"> · 置信度 {{ (riskAlertConfidence * 100).toFixed(0) }}%</template>
            </p>
          </div>
        </div>

        <!-- 产品推荐卡片 -->
        <div v-if="mode === 'product' && advisorData.products?.length" class="advisor-section">
          <div class="advisor-section-head">
            <h3>推荐产品</h3>
            <span class="advisor-count-pill">{{ advisorData.products.length }} 只通过适当性校验</span>
          </div>
          <div class="advisor-rec-grid">
            <article v-for="(p, index) in advisorData.products" :key="p.product_id" class="advisor-rec-card">
              <div class="advisor-rec-top">
                <span class="advisor-rank">#{{ index + 1 }}</span>
                <span class="advisor-risk-badge" :style="riskBadgeStyle(p.risk_level)">{{ p.risk_level }}</span>
                <span class="advisor-rec-score">综合 {{ (p.final_score * 100).toFixed(0) }} 分</span>
              </div>
              <h4 class="advisor-rec-name">{{ p.name }}</h4>
              <p class="advisor-rec-industry">{{ p.industry || '行业未归类' }} · {{ productTypeLabel(p.product_type) }} · {{ p.term_days ? p.term_days + ' 天' : '开放期限' }} · 起投 {{ money(p.minimum_amount) }} 元</p>
              <div class="advisor-factor-bars">
                <div v-for="(label, key) in FACTOR_LABELS" :key="key" class="advisor-factor-row">
                  <span>{{ label }}</span>
                  <div class="advisor-factor-track">
                    <div
                      class="advisor-factor-fill"
                      :style="{ width: `${Math.round(((p.score_breakdown as any)[key] || 0) / 0.3 * 100)}%`, background: key === 'return' ? '#315ff4' : key === 'risk_match' ? '#7c4dff' : '#2f9e68' }"
                    ></div>
                  </div>
                  <em>{{ ((p.score_breakdown as any)[key] || 0).toFixed(2) }}</em>
                </div>
              </div>
              <p v-if="advisorData.reasons?.[index]" class="advisor-rec-reason">{{ advisorData.reasons[index] }}</p>
              <p v-if="p.description" class="advisor-rec-desc">{{ p.description }}</p>
            </article>
          </div>

          <details v-if="advisorData.excluded?.length" class="advisor-excluded">
            <summary>
              被适当性规则排除的产品（{{ advisorData.excluded_count ?? advisorData.excluded.length }}）
            </summary>
            <ul>
              <li v-for="item in advisorData.excluded" :key="item.product_id">
                <strong>{{ item.name }}</strong>
                <span class="advisor-excluded-risk" :style="riskBadgeStyle(item.risk_level)">{{ item.risk_level }}</span>
                <span>{{ item.reasons.map(reasonLabel).join('；') }}</span>
              </li>
            </ul>
          </details>
        </div>
        <div v-else-if="mode === 'product'" class="advisor-section">
          <p class="advisor-empty">没有通过适当性门槛的产品，请检查客户画像与风险测评状态。</p>
        </div>

        <!-- 持仓分析 -->
        <div v-if="mode === 'portfolio' && advisorData.portfolio" class="advisor-section">
          <div class="advisor-section-head">
            <h3>持仓概览</h3>
            <span class="advisor-count-pill">{{ portfolioItems.length }} 项持仓</span>
          </div>
          <div class="advisor-metric-grid">
            <div class="advisor-metric"><span>持仓总市值</span><strong>{{ money(advisorData.portfolio.total_market_value) }} 元</strong></div>
            <div class="advisor-metric"><span>累计成本</span><strong>{{ money(advisorData.portfolio.total_cost) }} 元</strong></div>
            <div class="advisor-metric"><span>累计盈亏</span><strong :class="profitClass(advisorData.portfolio.total_profit_loss)">{{ advisorData.portfolio.total_profit_loss >= 0 ? '+' : '' }}{{ money(advisorData.portfolio.total_profit_loss) }} 元</strong></div>
            <div class="advisor-metric"><span>收益率</span><strong :class="profitClass(advisorData.total_pnl_pct)">{{ signedPctValue(advisorData.total_pnl_pct) }}</strong></div>
            <div class="advisor-metric"><span>前3大集中度</span><strong>{{ advisorData.top3_weight }}%</strong></div>
          </div>

          <div v-if="portfolioItems.length" class="advisor-table-wrap">
            <table class="advisor-table">
              <thead>
                <tr><th>产品</th><th>类型</th><th>风险</th><th>市值</th><th>成本</th><th>盈亏</th><th>占比</th><th>持有</th></tr>
              </thead>
              <tbody>
                <tr v-for="item in portfolioItems" :key="item.product_id">
                  <td class="advisor-td-strong">{{ item.product }}</td>
                  <td>{{ productTypeLabel(item.product_type) }}</td>
                  <td><span class="advisor-risk-badge" :style="riskBadgeStyle(item.risk_level)">{{ item.risk_level }}</span></td>
                  <td>{{ money(item.market_value) }}</td>
                  <td>{{ money(item.cost_amount) }}</td>
                  <td :class="profitClass(item.profit_loss)">{{ item.profit_loss >= 0 ? '+' : '' }}{{ money(item.profit_loss) }}（{{ signedPctValue(item.profit_loss_pct) }}）</td>
                  <td>{{ item.weight }}%</td>
                  <td>{{ item.holding_days }} 天</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="advisor-dist-grid">
            <div class="advisor-dist-card">
              <h4>风险分布</h4>
              <div class="advisor-dist-bars">
                <div v-for="(value, level) in riskDistribution" :key="level" class="advisor-dist-row">
                  <span>{{ level }}</span>
                  <div class="advisor-dist-track">
                    <div class="advisor-dist-fill" :style="{ width: `${value}%`, background: RISK_COLOR[level] || '#315ff4' }"></div>
                  </div>
                  <em>{{ value.toFixed(1) }}%</em>
                </div>
              </div>
            </div>
            <div class="advisor-dist-card">
              <h4>类型分布</h4>
              <div class="advisor-dist-bars">
                <div v-for="(value, type) in typeDistribution" :key="type" class="advisor-dist-row">
                  <span>{{ type }}</span>
                  <div class="advisor-dist-track">
                    <div class="advisor-dist-fill" :style="{ width: `${value}%`, background: '#7c4dff' }"></div>
                  </div>
                  <em>{{ value.toFixed(1) }}%</em>
                </div>
              </div>
            </div>
            <div class="advisor-dist-card">
              <h4>行业分布（图谱）</h4>
              <div class="advisor-dist-bars">
                <div v-for="(value, industry) in industryDistribution" :key="industry" class="advisor-dist-row">
                  <span>{{ industry }}</span>
                  <div class="advisor-dist-track">
                    <div class="advisor-dist-fill" :style="{ width: `${Math.min(value, 100)}%`, background: '#2f9e68' }"></div>
                  </div>
                  <em>{{ value.toFixed(1) }}%</em>
                </div>
              </div>
            </div>
          </div>

          <p v-if="advisorData.analysis" class="advisor-analysis">
            <strong>AI 解读</strong><br>
            {{ advisorData.analysis }}
          </p>
        </div>

        <!-- 资产配置 -->
        <div v-if="mode === 'allocation' && allocationItems.length" class="advisor-section">
          <div class="advisor-section-head">
            <h3>目标资产配置 · {{ advisorData.risk_level }} 风险等级</h3>
            <span class="advisor-count-pill">已写入画像标签</span>
          </div>
          <div class="advisor-alloc-grid">
            <div v-for="item in allocationItems" :key="item.category" class="advisor-alloc-card">
              <div class="advisor-alloc-head">
                <span>{{ item.category }}</span>
                <strong>{{ item.ratio }}%</strong>
              </div>
              <div class="advisor-alloc-track">
                <div class="advisor-alloc-fill" :style="{ width: `${item.pct}%` }"></div>
              </div>
            </div>
          </div>
          <div class="advisor-alloc-meta">
            <span>可投资资产：{{ moneyShort(advisorData.investable_asset) }} 元</span>
            <span>总资产：{{ moneyShort(advisorData.total_asset) }} 元</span>
            <span>配置模板：货币 / 债券 / 股票 / 现金</span>
          </div>

          <!-- 当前 vs 目标配置偏差诊断 -->
          <div v-if="advisorData.adjustments?.length" class="advisor-alloc-diag">
            <div class="advisor-section-head">
              <h3>配置偏差诊断</h3>
              <span v-if="advisorData.review_required" class="advisor-count-pill advisor-count-warn">偏差 ≥20% 需人工复核</span>
              <span v-else class="advisor-count-pill">偏差可控</span>
            </div>
            <div class="advisor-table-wrap">
              <table class="advisor-table">
                <thead>
                  <tr><th>配置类别</th><th>当前比例</th><th>目标比例</th><th>偏差</th><th>建议动作</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in advisorData.adjustments" :key="item.category">
                    <td class="advisor-td-strong">{{ item.category }}</td>
                    <td>{{ item.current_ratio }}%</td>
                    <td>{{ item.target_ratio }}%</td>
                    <td :class="Math.abs(item.deviation) >= 20 ? 'is-loss' : Math.abs(item.deviation) > 5 ? 'is-warn' : ''">{{ item.deviation > 0 ? '+' : '' }}{{ item.deviation }}%</td>
                    <td><span class="advisor-action-badge" :data-action="item.action">{{ ACTION_TEXT[item.action] || item.action }}</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="advisorData.portfolio_total" class="advisor-alloc-note">当前持仓合计 {{ money(advisorData.portfolio_total) }} 元（按产品类型映射至配置大类）。</p>
          </div>
        </div>

        <!-- 客户对比 -->
        <div v-if="mode === 'comparison'" class="advisor-section">
          <div class="advisor-section-head">
            <h3>客户对比</h3>
            <span class="advisor-count-pill">{{ selectedCustomer?.display_name }} ⇄ {{ selectedCustomer2?.display_name }}</span>
          </div>

          <!-- 风险差异 -->
          <div v-if="advisorData.risk_difference" class="advisor-risk-comp">
            <span class="advisor-risk-comp-label">风险等级</span>
            <span class="advisor-risk-badge" :style="riskBadgeStyle(advisorData.risk_difference.a || 'C1')">{{ advisorData.risk_difference.a || '未测评' }}</span>
            <span class="advisor-risk-comp-arrow">⇄</span>
            <span class="advisor-risk-badge" :style="riskBadgeStyle(advisorData.risk_difference.b || 'C1')">{{ advisorData.risk_difference.b || '未测评' }}</span>
            <span v-if="advisorData.risk_difference.different" class="advisor-risk-comp-tag">风险等级不同，需分别复核适当性</span>
            <span v-else class="advisor-risk-comp-tag ok">风险等级一致</span>
          </div>

          <!-- 行业集中度对比 -->
          <div v-if="advisorData.concentration" class="advisor-conc-comp">
            <div class="advisor-conc-item">
              <h4>{{ selectedCustomer?.display_name }} 集中度</h4>
              <p v-if="advisorData.concentration.a?.top_industry">最高行业：{{ advisorData.concentration.a.top_industry }}（{{ advisorData.concentration.a.top_ratio }}%）</p>
              <p v-else class="advisor-empty-sm">暂无图谱行业数据</p>
            </div>
            <div class="advisor-conc-item">
              <h4>{{ selectedCustomer2?.display_name }} 集中度</h4>
              <p v-if="advisorData.concentration.b?.top_industry">最高行业：{{ advisorData.concentration.b.top_industry }}（{{ advisorData.concentration.b.top_ratio }}%）</p>
              <p v-else class="advisor-empty-sm">暂无图谱行业数据</p>
            </div>
          </div>

          <!-- 分客户建议 -->
          <div v-if="advisorData.advice?.a?.length || advisorData.advice?.b?.length" class="advisor-advice-grid">
            <div v-if="advisorData.advice?.a?.length" class="advisor-comp-card">
              <h4>{{ selectedCustomer?.display_name }} 建议</h4>
              <ul><li v-for="text in advisorData.advice.a" :key="text">{{ text }}</li></ul>
            </div>
            <div v-if="advisorData.advice?.b?.length" class="advisor-comp-card">
              <h4>{{ selectedCustomer2?.display_name }} 建议</h4>
              <ul><li v-for="text in advisorData.advice.b" :key="text">{{ text }}</li></ul>
            </div>
          </div>

          <div class="advisor-comp-grid">
            <div class="advisor-comp-card">
              <h4>共同持仓（{{ advisorData.common?.length || 0 }}）</h4>
              <ul v-if="advisorData.common?.length">
                <li v-for="name in advisorData.common" :key="name">{{ name }}</li>
              </ul>
              <p v-else class="advisor-empty-sm">两位客户暂无共同持仓</p>
            </div>
            <div class="advisor-comp-card">
              <h4>{{ selectedCustomer?.display_name }} 独有（{{ advisorData.only_a?.length || 0 }}）</h4>
              <ul v-if="advisorData.only_a?.length">
                <li v-for="name in advisorData.only_a" :key="name">{{ name }}</li>
              </ul>
              <p v-else class="advisor-empty-sm">无独有持仓</p>
            </div>
            <div class="advisor-comp-card">
              <h4>{{ selectedCustomer2?.display_name }} 独有（{{ advisorData.only_b?.length || 0 }}）</h4>
              <ul v-if="advisorData.only_b?.length">
                <li v-for="name in advisorData.only_b" :key="name">{{ name }}</li>
              </ul>
              <p v-else class="advisor-empty-sm">无独有持仓</p>
            </div>
          </div>
        </div>

        <!-- 证据区 -->
        <div v-if="evidence.length" class="advisor-section">
          <div class="advisor-section-head">
            <h3>证据来源</h3>
            <span class="advisor-count-pill">{{ evidence.length }} 条</span>
          </div>
          <details v-for="(item, index) in evidence" :key="index" class="advisor-evidence-item">
            <summary>{{ item.source || '知识片段' }}<em v-if="item.score !== undefined"> · 相关度 {{ ((item.score || 0) * 100).toFixed(0) }}%</em></summary>
            <p>{{ item.content }}</p>
          </details>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.advisor-workspace { max-width: 1080px; }
.advisor-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
.advisor-header h2 { margin: 8px 0 0; color: #26364a; }
.advisor-header p { margin: 6px 0 0; color: #718198; font-size: 13px; }
.advisor-chip { display: inline-flex; align-items: center; gap: 4px; padding: 8px 14px; border: 1px solid #b8c9ff; border-radius: 999px; background: #f0f4ff; color: #315ff4; font-size: 13px; font-weight: 600; white-space: nowrap; }
.advisor-panel { padding: 22px; border: 1px solid #d7e0eb; border-radius: 14px; background: #fff; box-shadow: 0 8px 24px #3341550d; margin-bottom: 16px; }
.advisor-preset-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
.advisor-preset-card { display: flex; gap: 10px; align-items: center; padding: 14px 16px; border: 1px solid #d7e0eb; border-radius: 11px; background: #fafbfd; color: #52647a; text-align: left; cursor: pointer; transition: .18s; }
.advisor-preset-card:hover { border-color: #b8c9ff; background: #f0f4ff; }
.advisor-preset-card.active { border-color: #315ff4; background: #eef3ff; box-shadow: inset 0 0 0 1px #315ff4; }
.advisor-preset-card:disabled { opacity: .5; cursor: wait; }
.advisor-preset-icon { font-size: 20px; color: #315ff4; }
.advisor-preset-card strong { display: block; color: #26364a; font-size: 14px; }
.advisor-preset-card small { display: block; margin-top: 3px; color: #8b9aaa; font-size: 11px; line-height: 1.4; }
.advisor-customer-row { display: grid; grid-template-columns: 1fr 1fr auto; gap: 12px; align-items: end; }
.advisor-customer-row label { display: grid; gap: 7px; color: #52647a; font-size: 13px; }
.advisor-customer-row select { width: 100%; padding: 11px 12px; border: 1px solid #d7e0eb; border-radius: 9px; background: #fff; color: #26364a; font-size: 14px; }
.advisor-run-button { padding: 12px 22px; border: 0; border-radius: 9px; background: linear-gradient(135deg, #315ff4, #7c4dff); color: #fff; font-weight: 700; cursor: pointer; white-space: nowrap; box-shadow: 0 8px 16px #315ff433; }
.advisor-run-button:disabled { opacity: .5; cursor: not-allowed; filter: grayscale(.35); }
.advisor-hint { margin: 14px 0 0; color: #8b9aaa; font-size: 12px; line-height: 1.6; }
.advisor-error { padding: 12px 16px; border: 1px solid #f0b8c0; border-radius: 10px; background: #fff5f6; color: #c33b52; font-size: 13px; margin-bottom: 14px; }
.advisor-result-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; border-bottom: 1px solid #e7edf3; padding-bottom: 16px; margin-bottom: 16px; flex-wrap: wrap; }
.advisor-summary { margin: 10px 0 0; color: #52647a; font-size: 13.5px; line-height: 1.8; white-space: pre-line; }
.advisor-result-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.advisor-agent-tag { padding: 5px 10px; border-radius: 999px; background: #eef3ff; color: #315ff4; font-size: 12px; }
.advisor-confidence { padding: 5px 10px; border-radius: 999px; background: #e9f9f1; color: #2f9e68; font-size: 12px; }
.advisor-profile-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
.advisor-profile-cell { padding: 12px 14px; border: 1px solid #e7edf3; border-radius: 10px; background: #fafbfd; }
.advisor-profile-cell span { display: block; color: #8b9aaa; font-size: 11px; margin-bottom: 6px; }
.advisor-profile-cell strong { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 13px; color: #26364a; }
.advisor-guard { display: flex; gap: 12px; padding: 14px 16px; border: 1px solid #f5d9a8; border-radius: 11px; background: #fffaf1; margin-bottom: 18px; }
.advisor-guard-icon { font-size: 18px; }
.advisor-guard strong { color: #8a5a10; font-size: 13px; }
.advisor-guard p { margin: 4px 0 0; color: #a06f22; font-size: 12.5px; line-height: 1.6; }
.advisor-risk-banner { display: flex; gap: 12px; padding: 14px 16px; border-radius: 11px; margin-bottom: 18px; border: 1px solid; }
.advisor-risk-banner[data-level="high"] { border-color: #f0b8c0; background: #fff5f6; }
.advisor-risk-banner[data-level="medium"] { border-color: #f5d9a8; background: #fffaf1; }
.advisor-risk-banner[data-level="low"] { border-color: #e0e8f5; background: #f6f9ff; }
.advisor-risk-banner strong { color: #26364a; font-size: 13px; }
.advisor-risk-banner p { margin: 4px 0 0; color: #718198; font-size: 12.5px; line-height: 1.6; }
.is-warn { color: #d29a2b !important; }
.advisor-alloc-diag { margin-top: 18px; }
.advisor-count-warn { background: #fff5f6; color: #c33b52; }
.advisor-action-badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.advisor-action-badge[data-action="increase"] { background: #e9f9f1; color: #2f9e68; }
.advisor-action-badge[data-action="reduce"] { background: #fff5f6; color: #c33b52; }
.advisor-action-badge[data-action="hold"] { background: #eef3ff; color: #315ff4; }
.advisor-alloc-note { margin: 10px 0 0; color: #8b9aaa; font-size: 12px; }
.advisor-risk-comp { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border: 1px solid #e7edf3; border-radius: 10px; background: #fafbfd; margin-bottom: 12px; flex-wrap: wrap; }
.advisor-risk-comp-label { color: #8b9aaa; font-size: 12px; }
.advisor-risk-comp-arrow { color: #b0bcc9; font-size: 14px; }
.advisor-risk-comp-tag { padding: 3px 10px; border-radius: 999px; background: #fff3e0; color: #e65100; font-size: 11px; }
.advisor-risk-comp-tag.ok { background: #e9f9f1; color: #2f9e68; }
.advisor-conc-comp { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.advisor-conc-item { padding: 14px; border: 1px solid #e7edf3; border-radius: 10px; background: #fafbfd; }
.advisor-conc-item h4 { margin: 0 0 8px; color: #26364a; font-size: 13px; }
.advisor-conc-item p { margin: 0; color: #52647a; font-size: 13px; }
.advisor-advice-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.advisor-section { margin-top: 20px; }
.advisor-section-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.advisor-section-head h3 { margin: 0; color: #26364a; font-size: 17px; }
.advisor-count-pill { padding: 4px 10px; border-radius: 999px; background: #f0f4ff; color: #315ff4; font-size: 12px; }
.advisor-rec-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.advisor-rec-card { padding: 16px; border: 1px solid #d7e0eb; border-radius: 12px; background: #fafbfd; }
.advisor-rec-top { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.advisor-rank { font-size: 11px; color: #8b9aaa; }
.advisor-risk-badge { display: inline-block; padding: 1px 8px; border: 1px solid; border-radius: 999px; font-size: 11px; font-weight: 600; }
.advisor-rec-score { margin-left: auto; font-size: 12px; color: #315ff4; font-weight: 600; }
.advisor-rec-name { margin: 6px 0 4px; color: #26364a; font-size: 16px; }
.advisor-rec-industry { margin: 0 0 12px; color: #8b9aaa; font-size: 12px; }
.advisor-factor-bars { display: grid; gap: 6px; margin-bottom: 10px; }
.advisor-factor-row { display: grid; grid-template-columns: 64px 1fr 34px; gap: 8px; align-items: center; }
.advisor-factor-row span { font-size: 11px; color: #718198; }
.advisor-factor-row em { font-style: normal; font-size: 11px; color: #52647a; text-align: right; }
.advisor-factor-track { height: 6px; border-radius: 999px; background: #e7edf3; overflow: hidden; }
.advisor-factor-fill { height: 100%; border-radius: 999px; transition: width .4s; }
.advisor-rec-reason { margin: 8px 0 0; color: #52647a; font-size: 12.5px; line-height: 1.6; }
.advisor-rec-desc { margin: 8px 0 0; padding-top: 8px; border-top: 1px dashed #d7e0eb; color: #8b9aaa; font-size: 12px; line-height: 1.6; }
.advisor-excluded { margin-top: 14px; border: 1px solid #f0b8c0; border-radius: 10px; background: #fff9fa; }
.advisor-excluded summary { padding: 12px 16px; cursor: pointer; color: #c33b52; font-size: 13px; font-weight: 600; }
.advisor-excluded ul { margin: 0; padding: 0 16px 14px; list-style: none; }
.advisor-excluded li { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px dashed #f0d4d9; font-size: 13px; color: #52647a; }
.advisor-excluded-risk { flex: none; }
.advisor-metric-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 14px; }
.advisor-metric { padding: 14px; border: 1px solid #e7edf3; border-radius: 10px; background: #fafbfd; }
.advisor-metric span { display: block; color: #8b9aaa; font-size: 11px; margin-bottom: 6px; }
.advisor-metric strong { color: #26364a; font-size: 15px; }
.advisor-table-wrap { overflow-x: auto; border: 1px solid #e7edf3; border-radius: 10px; margin-bottom: 14px; }
.advisor-table { width: 100%; border-collapse: collapse; min-width: 720px; }
.advisor-table th, .advisor-table td { padding: 11px 13px; border-bottom: 1px solid #e7edf3; text-align: left; font-size: 12.5px; color: #52647a; white-space: nowrap; }
.advisor-table th { background: #f7f9fc; color: #63758b; font-weight: 600; }
.advisor-table tr:hover td { background: #fbfdff; }
.advisor-td-strong { color: #26364a; font-weight: 600; }
.is-profit { color: #2f9e68 !important; }
.is-loss { color: #c23b52 !important; }
.advisor-dist-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 14px; }
.advisor-dist-card { padding: 14px; border: 1px solid #e7edf3; border-radius: 10px; background: #fafbfd; }
.advisor-dist-card h4 { margin: 0 0 10px; color: #26364a; font-size: 13px; }
.advisor-dist-bars { display: grid; gap: 8px; }
.advisor-dist-row { display: grid; grid-template-columns: 64px 1fr 48px; gap: 8px; align-items: center; }
.advisor-dist-row span { font-size: 12px; color: #52647a; }
.advisor-dist-row em { font-style: normal; font-size: 11px; color: #8b9aaa; text-align: right; }
.advisor-dist-track { height: 8px; border-radius: 999px; background: #e7edf3; overflow: hidden; }
.advisor-dist-fill { height: 100%; border-radius: 999px; }
.advisor-analysis { margin: 0; padding: 14px 16px; border: 1px solid #dce4ef; border-radius: 10px; background: #f7f9fc; color: #52647a; font-size: 13px; line-height: 1.9; }
.advisor-analysis strong { color: #315ff4; font-size: 12px; }
.advisor-alloc-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.advisor-alloc-card { padding: 14px; border: 1px solid #e7edf3; border-radius: 10px; background: #fafbfd; }
.advisor-alloc-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.advisor-alloc-head span { color: #52647a; font-size: 13px; }
.advisor-alloc-head strong { color: #26364a; font-size: 18px; }
.advisor-alloc-track { height: 9px; border-radius: 999px; background: #e7edf3; overflow: hidden; }
.advisor-alloc-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #315ff4, #7c4dff); }
.advisor-alloc-meta { display: flex; gap: 16px; margin-top: 12px; color: #8b9aaa; font-size: 12px; flex-wrap: wrap; }
.advisor-comp-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.advisor-comp-card { padding: 14px; border: 1px solid #e7edf3; border-radius: 10px; background: #fafbfd; }
.advisor-comp-card h4 { margin: 0 0 10px; color: #26364a; font-size: 13px; }
.advisor-comp-card ul { margin: 0; padding: 0; list-style: none; }
.advisor-comp-card li { padding: 7px 0; border-top: 1px dashed #e7edf3; font-size: 13px; color: #52647a; }
.advisor-empty { color: #8b9aaa; font-size: 13px; }
.advisor-empty-sm { color: #b0bcc9; font-size: 12px; }
.advisor-evidence-item { border: 1px solid #e7edf3; border-radius: 9px; margin-bottom: 8px; background: #fafbfd; }
.advisor-evidence-item summary { padding: 11px 14px; cursor: pointer; color: #315ff4; font-size: 13px; }
.advisor-evidence-item summary em { color: #8b9aaa; font-size: 12px; font-style: normal; }
.advisor-evidence-item p { margin: 0; padding: 0 14px 12px; color: #718198; font-size: 12.5px; line-height: 1.7; }

@media (max-width: 900px) {
  .advisor-preset-grid { grid-template-columns: repeat(2, 1fr); }
  .advisor-customer-row { grid-template-columns: 1fr; }
  .advisor-rec-grid, .advisor-comp-grid, .advisor-dist-grid, .advisor-alloc-grid, .advisor-conc-comp, .advisor-advice-grid { grid-template-columns: 1fr; }
  .advisor-metric-grid { grid-template-columns: repeat(2, 1fr); }
  .advisor-profile-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
