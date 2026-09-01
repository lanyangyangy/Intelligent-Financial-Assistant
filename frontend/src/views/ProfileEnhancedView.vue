<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { profileApi } from '../api/profile'
import { useAuth } from '../stores/auth'
import { isCustomerRoles } from '../constants/roles'

// 可选 prop：员工端查看指定客户画像时传入客户 ID；客户本人留空
const props = defineProps<{ customerId?: string }>()
const auth = useAuth()
const isStaffView = computed(() => Boolean(props.customerId))
const canManageCustomers = computed(() => Boolean(auth.me.value) && !isCustomerRoles(auth.me.value?.roles) && auth.me.value?.permissions?.includes('customer:read'))
const canExtractConversation = computed(() => isStaffView.value && canManageCustomers.value)
const loading = ref(false)
const error = ref('')
const enhanced = ref<any>(null)
const tags = ref<any[]>([])
const conflicts = ref<any[]>([])
const convText = ref('')
const extracting = ref(false)
const extractMsg = ref('')
const conversationOpen = ref(false)
const conversationResult = ref<any>(null)
const calcMsg = ref('')
// 客户个人画像：额外加载风险测评与客户层级（均来自本项目 API/数据库）
const riskAssessment = ref<any>(null)
const tier = ref<any>(null)

const RISK_LEVEL_LABELS: Record<string, string> = {
  C1: '保守型', C2: '稳健型', C3: '平衡型', C4: '进取型', C5: '激进型',
}
const TIER_LABELS: Record<string, string> = {
  ordinary: '普通客户', gold: '黄金客户', platinum: '铂金客户',
  diamond: '钻石客户', private_bank: '私行客户', enterprise_standard: '企业客户',
}
const riskStatusLabel = (status: string) => ({ active: '已生效', provisional: '临时', superseded: '已过期' }[status] || status)

const STATUS_LABELS: Record<string, string> = {
  VALID: '有效', PROVISIONAL: '临时', NEEDS_REVIEW: '待复核', INCOMPLETE: '不完整', EXPIRED: '已过期',
}
const TAG_LABELS: Record<string, string> = {
  OCCUPATION: '职业', MONTHLY_INCOME: '月收入', TOTAL_ASSETS: '总资产',
  HOUSEHOLD_ANNUAL_INCOME: '家庭年收入', TOTAL_LIABILITIES: '总负债', EDUCATION_LEVEL: '学历',
  INVESTMENT_GOAL: '投资目标', LOSS_TOLERANCE: '亏损容忍度', MAXIMUM_LOSS_TOLERANCE_PCT: '最大亏损容忍',
  LIQUIDITY_NEED: '流动性需求', PREFERRED_PRODUCT_TYPES: '偏好产品', INVESTMENT_EXPERIENCE_YEARS: '投资经验',
  INVESTABLE_ASSETS: '可投资资产', ASSET_SCALE: '资产规模档',
}
const TAG_VALUE_LABELS: Record<string, string> = {
  CAPITAL_PRESERVATION: '资产保值', STEADY_GROWTH: '稳健增值', LONG_TERM_GROWTH: '长期增长', HIGH_RETURN: '高收益增长',
  HIGH: '高', MEDIUM: '中', LOW: '低', NONE: '无',
  BELOW_100K: '10 万以下', '100K_TO_500K': '10–50 万', '500K_TO_1M': '50–100 万', '1M_TO_5M': '100–500 万', ABOVE_5M: '500 万以上',
}
const DIMENSION_LABELS: Record<string, string> = {
  basic: '基础属性', experience: '投资经验', preference: '风险偏好', behavior: '行为稳定性',
}
const BREAKDOWN_LABELS: Record<string, string> = {
  age: '年龄', education: '学历', occupation: '职业', income: '收入', asset: '资产',
  experience_years: '投资年限', product_type: '产品偏好', trade_frequency: '交易频率',
  return: '收益表现', questionnaire: '问卷测评', loss_tolerance: '损失承受力',
}
const CONF_COLORS = ['#dc3545', '#e67e22', '#f0ad4e', '#74d6c6', '#2aa79a']

const dimensionItems = computed(() => {
  const dimensions = enhanced.value?.dimension_scores?.dimensions || {}
  return Object.entries(dimensions).map(([key, value]: [string, any]) => ({
    key,
    label: DIMENSION_LABELS[key] || key,
    score: Number(value?.score || 0),
    weight: Number(value?.weight || 0),
  }))
})
const breakdownItems = computed(() => Object.entries(enhanced.value?.dimension_scores?.breakdown || {}).map(([key, value]) => ({
  key,
  label: BREAKDOWN_LABELS[key] || key,
  value: Number(value || 0),
})))

function statusColor(status: string) {
  return { VALID: '#2e7d32', PROVISIONAL: '#e65100', NEEDS_REVIEW: '#c62828', INCOMPLETE: '#6a1b9a', EXPIRED: '#37474f' }[status] || '#55617a'
}
function confColor(confidence: unknown) {
  const value = Number(confidence || 0)
  return CONF_COLORS[Math.min(CONF_COLORS.length - 1, Math.max(0, Math.floor(value * 5)))]
}
function formatPercent(value: unknown) { return `${Math.round(Number(value || 0) * 100)}%` }
function dimensionPercent(item: { score: number; weight: number }) {
  const ceiling = item.weight > 0 ? item.weight * 100 : 25
  return Math.max(0, Math.min(100, Math.round((item.score / ceiling) * 100)))
}
function formatMoney(value: unknown) { return `¥${Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}` }
function formatTagValue(tag: any) {
  const value = tag?.value ?? tag?.tag_value
  if (Array.isArray(value)) return value.join('、')
  if (['TOTAL_ASSETS', 'INVESTABLE_ASSETS', 'MONTHLY_INCOME', 'HOUSEHOLD_ANNUAL_INCOME', 'TOTAL_LIABILITIES'].includes(tag?.tag_code)) return formatMoney(value)
  if (tag?.tag_code === 'INVESTMENT_EXPERIENCE_YEARS') return `${value} 年`
  if (tag?.tag_code === 'MAXIMUM_LOSS_TOLERANCE_PCT') return `${value}%`
  return TAG_VALUE_LABELS[String(value)] || String(value ?? '—')
}
function formatConflictValue(value: unknown) {
  if (Array.isArray(value)) return value.join('、')
  if (value && typeof value === 'object') return JSON.stringify(value, null, 0)
  return String(value ?? '—')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    // 员工端查看指定客户：走 staff 接口（风评/层级/历史/产品仅客户本人可见）；
    // 客户本人走 /me 接口
    const enhancedReq = isStaffView.value
      ? profileApi.staffEnhanced(props.customerId!)
      : profileApi.enhanced()
    const infoReq = isStaffView.value
      ? profileApi.staffInfo(props.customerId!)
      : profileApi.myInfo()
    const riskReq = isStaffView.value ? null : profileApi.riskAssessment().catch(() => null)
    const tierReq = isStaffView.value ? null : profileApi.tier().catch(() => null)
    const historyReq = isStaffView.value ? null : profileApi.history().catch(() => null)
    const productReq = isStaffView.value ? null : profileApi.productSuitability().catch(() => null)
    const conflictsReq = isStaffView.value
      ? profileApi.staffConflicts(props.customerId!).catch(() => null)
      : profileApi.myConflicts().catch(() => null)
    const [enhancedResp, riskResp, tierResp, infoResp, historyResp, productResp, conflictsResp] = await Promise.all([
      enhancedReq,
      riskReq,
      tierReq,
      infoReq.catch(() => null),
      historyReq,
      productReq,
      conflictsReq,
    ])
    enhanced.value = enhancedResp.data.data
    tags.value = enhancedResp.data.data.tags || []
    riskAssessment.value = riskResp?.data?.data ?? null
    tier.value = tierResp?.data?.data ?? null
    myInfo.value = infoResp?.data?.data ?? null
    history.value = historyResp?.data?.data ?? []
    products.value = productResp?.data?.data ?? null
    if (!suitabilityProducts.value.some((p: any) => p.product_id === suitabilityProductId.value)) {
      suitabilityProductId.value = suitabilityProducts.value[0]?.product_id || ''
    }
    conflicts.value = conflictsResp?.data?.data?.conflicts ?? []
    if (myInfo.value?.basic) {
      editForm.value = { ...myInfo.value.basic }
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

// ---- 我的信息 / KYC / 资产（功能 1、2、8）----
const myInfo = ref<any>(null)
const editForm = ref<any>({})
const editing = ref(false)
const saving = ref(false)
const infoMsg = ref('')
const OCCUPATION_OPTIONS = [
  { value: '', label: '请选择' }, { value: 'civil_servant', label: '公务员' },
  { value: 'public_institution', label: '事业单位' }, { value: 'state_owned_employee', label: '国企员工' },
  { value: 'listed_company_employee', label: '上市公司员工' }, { value: 'doctor', label: '医生' },
  { value: 'lawyer', label: '律师' }, { value: 'engineer', label: '工程师' },
  { value: 'sme_employee', label: '中小企业员工' }, { value: 'self_employed', label: '个体经营' },
  { value: 'retired', label: '退休' }, { value: 'unemployed', label: '无业' },
]
const EDUCATION_OPTIONS = [
  { value: '', label: '请选择' }, { value: 'HIGH_SCHOOL_OR_BELOW', label: '高中及以下' },
  { value: 'COLLEGE', label: '大专' }, { value: 'BACHELOR', label: '本科' },
  { value: 'MASTER_OR_ABOVE', label: '硕士及以上' },
]
const KYC_LABELS: Record<string, string> = {
  not_submitted: '未提交', submitted: '资料已提交', pending: '审核中', approved: '已认证',
}
function startEdit() {
  editForm.value = { ...(myInfo.value?.basic || {}) }
  editing.value = true
}
async function saveInfo() {
  saving.value = true
  infoMsg.value = ''
  try {
    const payload: any = {
      age: editForm.value.age ?? null,
      occupation: editForm.value.occupation || '',
      education_level: editForm.value.education_level || '',
      annual_income: editForm.value.annual_income ?? null,
      investment_experience_years: Number(editForm.value.investment_experience_years || 0),
      investment_goal: editForm.value.investment_goal || 'balanced',
      liquidity_preference: editForm.value.liquidity_preference || 'medium',
    }
    await profileApi.saveInfo(payload)
    editing.value = false
    infoMsg.value = '我的信息已保存，画像将基于最新资料重新计算。'
    await Promise.all([profileApi.calculate().catch(() => null), load()])
  } catch (e: any) {
    infoMsg.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

// ---- 画像版本历史（功能 6）----
const history = ref<any[]>([])
const VERSION_REASON_LABELS: Record<string, string> = {
  calculate: '重新计算', MANUAL_TAG_UPDATE: '标签更新', TAG_CONFLICT_RESOLVED: '冲突解析',
}

// ---- 产品可购买/拒绝判断（功能 9、10）----
const products = ref<any>(null)
const productStatusClass = (p: any) => p?.purchase_allowed ? 'can-buy' : p?.needs_review ? 'needs-review' : 'cannot-buy'
const productStatusText = (p: any) => p?.purchase_allowed ? '可购买' : p?.needs_review ? '待人工复核' : '拒绝购买'

// ---- 单产品适当性检查（客户个人画像面板）----
const suitabilityBusinessType = ref('PURCHASE')
const suitabilityProductId = ref('')
const suitabilityResult = ref<any>(null)
const suitabilityChecking = ref(false)
const suitabilityError = ref('')
const BUSINESS_TYPE_OPTIONS = [
  { value: 'PURCHASE', label: '购买' },
  { value: 'ADDITIONAL_PURCHASE', label: '追加购买' },
  { value: 'NEW_RECURRING_INVESTMENT', label: '新增定投' },
]
const DECISION_LABELS: Record<string, string> = {
  PASS: '通过', REJECT: '拒绝', REVIEW_REQUIRED: '待人工复核',
  PROFILE_REFRESH_REQUIRED: '需更新画像',
}
const RESTRICTION_LABELS: Record<string, string> = {
  UNDER_AGE: '未达到可投资年龄', AGE_OVER_80_R2_LIMIT: '年龄超过 80 岁，最高适配 R2',
  AGE_OVER_80_R3_APPROVAL_REQUIRED: '年龄超过 80 岁，R3 需审批',
  AGE_OVER_80_R4_R5_REJECTED: '年龄超过 80 岁，不适配 R4/R5',
  NO_INCOME_LOW_ASSETS_R2_LIMIT: '收入与资产不足，最高适配 R2',
  ASSESSMENT_MISSING: '缺少正式风险测评', ASSESSMENT_EXPIRED: '风险测评已过期',
  PROFILE_INCOMPLETE: '个人画像信息不完整', LOW_CONFIDENCE: '画像置信度不足',
  EVIDENCE_CONFLICT: '画像存在待确认冲突', SUITABILITY_MISMATCH: '产品风险等级超出客户适配范围',
}
const suitabilityProducts = computed(() => products.value?.items || [])
const selectedSuitabilityProduct = computed(() => suitabilityProducts.value.find((p: any) => p.product_id === suitabilityProductId.value))
function productRiskLabel(level: string) {
  return ({ R1: '低风险', R2: '中低风险', R3: '中风险', R4: '中高风险', R5: '高风险' } as Record<string, string>)[level] || level
}
function suitabilityDecisionLabel(decision: string) { return DECISION_LABELS[decision] || decision || '—' }
function suitabilityDecisionClass(decision: string) {
  return decision === 'PASS' ? 'suitability-pass' : decision === 'REJECT' ? 'suitability-reject' : 'suitability-review'
}
function suitabilityRestrictionText(result: any) {
  const codes = result?.restriction_codes || []
  return codes.length ? codes.map((code: string) => RESTRICTION_LABELS[code] || code).join('、') : '无'
}

async function recalc() {
  calcMsg.value = ''
  try {
    // 员工端查看指定客户：重算目标客户画像；客户本人：重算自己
    const response = isStaffView.value
      ? await profileApi.staffCalculate(props.customerId!)
      : await profileApi.calculate()
    enhanced.value = response.data.data.snapshot
    tags.value = response.data.data.tags || []
    calcMsg.value = `画像已重新计算：状态 ${STATUS_LABELS[enhanced.value.profile_status] || enhanced.value.profile_status}，风险评分 ${enhanced.value.model_risk_score}`
    await load()
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function extract() {
  if (convText.value.trim().length < 10) {
    extractMsg.value = '请至少输入 10 个字，便于模型提取有效信息。'
    return
  }
  extracting.value = true
  extractMsg.value = ''
  try {
    const response = await profileApi.extractConversation({
      conversation_text: convText.value,
      customer_id: props.customerId,
    })
    conversationResult.value = response.data.data
    extractMsg.value = `抽取完成（${response.data.data.extraction_mode}）：${response.data.data.summary}`
    await load()
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    extracting.value = false
  }
}

async function checkSuitability() {
  if (!suitabilityProductId.value) {
    suitabilityError.value = '请先选择测试产品。'
    suitabilityResult.value = null
    return
  }
  suitabilityChecking.value = true
  suitabilityError.value = ''
  try {
    const response = await profileApi.suitabilityCheck({
      product_id: suitabilityProductId.value,
      business_type: suitabilityBusinessType.value,
    })
    suitabilityResult.value = response.data.data
    await load()
  } catch (e: any) {
    suitabilityError.value = e.response?.data?.detail || e.message
  } finally {
    suitabilityChecking.value = false
  }
}

function openConversation() {
  conversationOpen.value = true
  extractMsg.value = ''
  conversationResult.value = null
}

function closeConversation() {
  if (!extracting.value) conversationOpen.value = false
}

onMounted(load)
</script>

<template>
  <section class="profile-insight-section">
    <div class="profile-insight-header">
      <div>
        <span class="module-kicker">PROFILE INSIGHTS</span>
        <h2>画像洞察</h2>
        <p>基于基础资料、风险测评、资产和持仓生成；资料更新后可重新计算。</p>
      </div>
      <button class="button" :disabled="loading" @click="recalc">重新计算画像</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="calcMsg" class="notice">{{ calcMsg }}</p>
    <div v-if="loading && !enhanced" class="profile-empty">正在读取完整画像…</div>

    <template v-else-if="enhanced">
      <div class="profile-stat-grid">
        <article><span>画像状态</span><strong :style="{ color: statusColor(enhanced.profile_status) }">{{ STATUS_LABELS[enhanced.profile_status] || enhanced.profile_status }}</strong></article>
        <article><span>风险等级</span><strong>{{ riskAssessment?.risk_level || enhanced.model_risk_level || '—' }}</strong><small>{{ RISK_LEVEL_LABELS[riskAssessment?.risk_level || enhanced.model_risk_level || ''] || '未测评' }}</small></article>
        <article><span>客户层级</span><strong>{{ tier?.customer_tier || myInfo?.basic?.customer_tier ? (TIER_LABELS[tier?.customer_tier || myInfo?.basic?.customer_tier] || tier?.customer_tier || myInfo?.basic?.customer_tier) : '—' }}</strong><small>{{ (tier?.customer_type || myInfo?.basic?.customer_type) === 'enterprise' ? '企业客户' : '个人客户' }}</small></article>
        <article><span>风险评分</span><strong>{{ enhanced.model_risk_score }}</strong><small>满分 100</small></article>
        <article><span>适当性置信度</span><strong :style="{ color: confColor(enhanced.suitability_confidence) }">{{ formatPercent(enhanced.suitability_confidence) }}</strong></article>
        <article><span>推荐置信度</span><strong :style="{ color: confColor(enhanced.recommendation_confidence) }">{{ formatPercent(enhanced.recommendation_confidence) }}</strong></article>
        <article><span>最高可购风险</span><strong>{{ enhanced.max_allowed_product_risk || '—' }}</strong></article>
        <article><span>画像版本</span><strong>v{{ enhanced.profile_version }}</strong></article>
      </div>

      <div v-if="enhanced.restriction_codes?.length" class="restriction-bar">
        <strong>限制项：</strong>
        <span v-for="code in enhanced.restriction_codes" :key="code" class="pill pill-warn">{{ code }}</span>
      </div>

      <!-- 我的信息 + KYC/资料（功能 1、2） -->
      <article class="insight-panel myinfo-panel">
        <div class="insight-panel-head">
          <div><span class="section-label">MY INFORMATION</span><h3>我的信息</h3></div>
          <div class="heading-actions">
            <span class="pill" :class="myInfo?.kyc?.status === 'approved' ? 'pill-ok' : myInfo?.kyc?.status === 'not_submitted' ? 'pill-warn' : 'pill-ok'">{{ KYC_LABELS[myInfo?.kyc?.status || 'not_submitted'] || '未提交' }}</span>
            <template v-if="!isStaffView">
              <button v-if="!editing" class="button button-small" @click="startEdit">编辑资料</button>
              <button v-else class="button button-small" :disabled="saving" @click="saveInfo">{{ saving ? '保存中…' : '保存并重算' }}</button>
            </template>
          </div>
        </div>
        <template v-if="!editing">
          <div class="myinfo-grid">
            <div><span>姓名</span><strong>{{ myInfo?.basic?.display_name || auth.me.value?.display_name || '—' }}</strong></div>
            <div><span>年龄</span><strong>{{ myInfo?.basic?.age ?? '—' }}</strong></div>
            <div><span>学历</span><strong>{{ EDUCATION_OPTIONS.find(o => o.value === myInfo?.basic?.education_level)?.label || '—' }}</strong></div>
            <div><span>职业</span><strong>{{ OCCUPATION_OPTIONS.find(o => o.value === myInfo?.basic?.occupation)?.label || myInfo?.basic?.occupation || '—' }}</strong></div>
            <div><span>年家庭收入</span><strong>{{ myInfo?.basic?.annual_income != null ? formatMoney(myInfo.basic.annual_income) : '—' }}</strong></div>
            <div><span>投资经验</span><strong>{{ myInfo?.basic?.investment_experience_years != null ? `${myInfo.basic.investment_experience_years} 年` : '—' }}</strong></div>
            <div><span>投资目标</span><strong>{{ myInfo?.basic?.investment_goal || '—' }}</strong></div>
            <div><span>所在地区</span><strong>{{ myInfo?.basic?.region || '—' }}</strong></div>
          </div>
          <div class="asset-strip" v-if="myInfo?.asset">
            <div><span>总资产</span><strong>{{ formatMoney(myInfo.asset.total_asset) }}</strong></div>
            <div><span>可投资资产</span><strong>{{ formatMoney(myInfo.asset.investable_asset) }}</strong></div>
            <div><span>现金余额</span><strong>{{ formatMoney(myInfo.asset.cash_balance) }}</strong></div>
            <div><span>净资产</span><strong>{{ formatMoney(myInfo.asset.net_asset) }}</strong></div>
          </div>
          <p v-if="infoMsg" class="notice">{{ infoMsg }}</p>
        </template>
        <form v-else class="myinfo-form" @submit.prevent="saveInfo">
          <label>学历
            <select v-model="editForm.education_level" required>
              <option v-for="o in EDUCATION_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </label>
          <label>职业
            <select v-model="editForm.occupation" required>
              <option v-for="o in OCCUPATION_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </label>
          <label>年龄<input v-model.number="editForm.age" type="number" min="0" max="150"></label>
          <label>年家庭收入（元）<input v-model.number="editForm.annual_income" type="number" min="0"></label>
          <label>投资经验（年）<input v-model.number="editForm.investment_experience_years" type="number" min="0" max="80"></label>
          <label>投资目标
            <select v-model="editForm.investment_goal">
              <option value="capital_preservation">保值</option>
              <option value="balanced">稳健增值</option>
              <option value="growth">增长</option>
              <option value="aggressive">激进</option>
            </select>
          </label>
          <label>流动性偏好
            <select v-model="editForm.liquidity_preference">
              <option value="high">高</option><option value="medium">中</option><option value="low">低</option>
            </select>
          </label>
          <div class="myinfo-form-footer">
            <button type="button" class="button button-quiet" @click="editing = false">取消</button>
            <button type="submit" class="button" :disabled="saving">{{ saving ? '保存中…' : '保存并重算画像' }}</button>
          </div>
          <p v-if="infoMsg" class="notice">{{ infoMsg }}</p>
        </form>
      </article>

      <!-- 画像版本历史（功能 6） -->
      <article v-if="!isStaffView" class="insight-panel history-panel">
        <div class="insight-panel-head"><div><span class="section-label">VERSION HISTORY</span><h3>画像版本历史</h3></div><span>{{ history.length }} 个版本</span></div>
        <div class="history-timeline">
          <div v-for="v in history" :key="v.profile_version" class="history-item">
            <span class="history-dot" :style="{ background: statusColor(v.profile_status) }"></span>
            <div class="history-body">
              <header>
                <strong>v{{ v.profile_version }}</strong>
                <span class="pill" :class="v.profile_status === 'VALID' ? 'pill-ok' : 'pill-warn'">{{ STATUS_LABELS[v.profile_status] || v.profile_status }}</span>
                <time>{{ v.created_at ? new Date(v.created_at).toLocaleString('zh-CN') : '' }}</time>
              </header>
              <p>风险评分 {{ v.model_risk_score }} · 等级 {{ v.model_risk_level }} · 最高可购 {{ v.max_allowed_product_risk }} · 置信度 {{ formatPercent(v.suitability_confidence) }} · 触发：{{ VERSION_REASON_LABELS[v.reason] || v.reason }}</p>
            </div>
          </div>
          <p v-if="!history.length" class="panel-empty">暂无版本记录，点击「重新计算画像」生成 v1。</p>
        </div>
      </article>

      <div class="profile-detail-grid">
        <article class="insight-panel dimension-panel">
          <div class="insight-panel-head"><div><span class="section-label">FOUR DIMENSIONS</span><h3>四维风险评分</h3></div><span>贡献分 / 权重</span></div>
          <div class="dimension-list">
            <div v-for="item in dimensionItems" :key="item.key" class="dimension-row">
              <div class="dimension-name"><strong>{{ item.label }}</strong><span>{{ item.score.toFixed(1) }} 分 · 权重 {{ Math.round(item.weight * 100) }}%</span></div>
              <div class="dimension-track"><i :style="{ width: `${dimensionPercent(item)}%` }"></i></div>
              <b>{{ dimensionPercent(item) }}%</b>
            </div>
            <p v-if="!dimensionItems.length" class="panel-empty">暂无四维评分，请重新计算画像。</p>
          </div>
        </article>

        <article class="insight-panel breakdown-panel">
          <div class="insight-panel-head"><div><span class="section-label">SCORING EVIDENCE</span><h3>评分依据</h3></div><span>{{ breakdownItems.length }} 项</span></div>
          <div class="breakdown-grid">
            <div v-for="item in breakdownItems" :key="item.key"><span>{{ item.label }}</span><strong>{{ item.value }} 分</strong></div>
            <p v-if="!breakdownItems.length" class="panel-empty">暂无评分明细。</p>
          </div>
        </article>
      </div>

      <div v-if="canExtractConversation && isStaffView" class="extract-panel">
        <h3>从客服对话补充画像</h3>
        <p>系统会提取对话中的偏好、经验和资产线索，并保留证据来源。</p>
        <textarea v-model="convText" rows="3" placeholder="粘贴一段客户对话，例如：我是国企员工，月收入3万元，总资产200万，投资经验8年，偏好基金和债券…" :disabled="extracting"></textarea>
        <button class="button" :disabled="extracting || !convText.trim()" @click="extract">{{ extracting ? '抽取中…' : '抽取画像标签' }}</button>
        <p v-if="extractMsg" class="notice">{{ extractMsg }}</p>
      </div>

      <article v-if="riskAssessment" class="insight-panel risk-panel">
        <div class="insight-panel-head">
          <div><span class="section-label">RISK ASSESSMENT</span><h3>风险测评</h3></div>
          <div class="heading-actions">
            <span class="pill" :class="riskAssessment.status === 'active' ? 'pill-ok' : 'pill-warn'">{{ riskStatusLabel(riskAssessment.status) }}</span>
            <a class="button button-small" href="/customer-center/risk-assessment">去测评</a>
          </div>
        </div>
        <div class="risk-grid">
          <div><span>测评等级</span><strong>{{ riskAssessment.risk_level }} · {{ RISK_LEVEL_LABELS[riskAssessment.risk_level] || '—' }}</strong></div>
          <div><span>测评得分</span><strong>{{ riskAssessment.score }} 分</strong></div>
          <div><span>测评来源</span><strong>{{ riskAssessment.source_type === 'questionnaire' ? '16题问卷' : riskAssessment.source_type }}</strong></div>
          <div><span>测评时间</span><strong>{{ riskAssessment.assessed_at ? new Date(riskAssessment.assessed_at).toLocaleDateString('zh-CN') : '—' }}</strong></div>
          <div><span>有效期至</span><strong :class="{ 'text-danger': riskAssessment.expires_at && new Date(riskAssessment.expires_at) < new Date() }">{{ riskAssessment.expires_at ? new Date(riskAssessment.expires_at).toLocaleDateString('zh-CN') : '长期有效（临时）' }}</strong></div>
        </div>
        <p v-if="riskAssessment.expires_at && new Date(riskAssessment.expires_at) < new Date()" class="notice warn-text">风评已过期，请点击「去测评」重新评估以解锁适当产品。</p>
      </article>

      <article class="insight-panel tag-panel">
        <div class="insight-panel-head"><div><span class="section-label">PROFILE TAGS</span><h3>画像标签</h3></div><span>{{ tags.length }} 项</span></div>
        <div v-if="tags.length" class="tag-grid">
          <article v-for="tag in tags" :key="tag.tag_code" class="tag-card" :class="String(tag.status || '').toLowerCase()">
            <header><strong>{{ TAG_LABELS[tag.tag_code] || tag.tag_code }}</strong>
              <span class="pill" :style="{ background: confColor(tag.confidence) + '22', color: confColor(tag.confidence) }">{{ formatPercent(tag.confidence) }}</span>
            </header>
            <p class="tag-value">{{ formatTagValue(tag) }}</p>
            <footer><span>{{ tag.source_type }} / {{ tag.extraction_method }}</span><span :class="{ 'tag-review': tag.status === 'NEEDS_REVIEW' }">{{ tag.status === 'NEEDS_REVIEW' ? '待复核' : '已生效' }}</span></footer>
            <blockquote v-if="tag.evidence_quote">“{{ tag.evidence_quote }}”</blockquote>
          </article>
        </div>
        <div v-else class="profile-empty compact">暂无标签。请重新计算画像以同步基础资料和资产信息。</div>
      </article>

      <article v-if="conflicts.length && isStaffView" class="insight-panel conflict-panel">
        <div class="insight-panel-head">
          <div><span class="section-label">PROFILE CONFLICTS</span><h3>画像标签冲突</h3></div>
          <span>{{ conflicts.length }} 条记录</span>
        </div>
        <div class="conflict-list">
          <div v-for="conflict in conflicts" :key="conflict.conflict_id" class="conflict-item">
            <header>
              <strong>{{ TAG_LABELS[conflict.tag_code] || conflict.tag_code }}</strong>
              <span class="pill" :class="conflict.status === 'OPEN' ? 'pill-warn' : 'pill-ok'">{{ conflict.status === 'OPEN' ? '待确认' : '已记录' }}</span>
            </header>
            <div class="conflict-values">
              <div><span>原画像</span><strong>{{ formatConflictValue(conflict.left_value) }}</strong><small>{{ conflict.left_source }} / {{ conflict.left_method }} · {{ formatPercent(conflict.left_confidence) }}</small></div>
              <div><span>对话提取</span><strong>{{ formatConflictValue(conflict.right_value) }}</strong><small>{{ conflict.right_source }} / {{ conflict.right_method }} · {{ formatPercent(conflict.right_confidence) }}</small></div>
            </div>
            <p v-if="conflict.resolution" class="conflict-resolution">处理：{{ conflict.resolution }}</p>
          </div>
        </div>
      </article>

      <!-- 单产品适当性检查 -->
      <article v-if="!isStaffView" class="insight-panel suitability-gate-panel">
        <div class="insight-panel-head">
          <div><span class="section-label">SUITABILITY GATE</span><h3>产品适当性检查</h3></div>
        </div>
        <div class="suitability-controls">
          <label>业务类型
            <select v-model="suitabilityBusinessType" :disabled="suitabilityChecking">
              <option v-for="option in BUSINESS_TYPE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <label>测试产品
            <select v-model="suitabilityProductId" :disabled="suitabilityChecking || !suitabilityProducts.length">
              <option value="" disabled>请选择产品</option>
              <option v-for="product in suitabilityProducts" :key="product.product_id" :value="product.product_id">
                {{ product.name }} · {{ product.risk_level }} · {{ productRiskLabel(product.risk_level) }}
              </option>
            </select>
          </label>
          <button class="button suitability-check-button" :disabled="suitabilityChecking || !suitabilityProductId" @click="checkSuitability">
            {{ suitabilityChecking ? '检查中…' : '执行适当性检查' }}
          </button>
        </div>
        <p v-if="suitabilityError" class="error suitability-error">{{ suitabilityError }}</p>
        <p v-if="!suitabilityProducts.length" class="panel-empty">暂无可检查的在售产品。</p>
        <div v-if="suitabilityResult" class="suitability-result" :class="suitabilityDecisionClass(suitabilityResult.decision)">
          <div class="suitability-result-cell suitability-decision-cell">
            <span>决策</span>
            <strong>{{ suitabilityDecisionLabel(suitabilityResult.decision) }}</strong>
          </div>
          <div class="suitability-result-cell">
            <span>客户 / 产品等级</span>
            <strong>{{ suitabilityResult.model_risk_level }} / {{ suitabilityResult.product_risk_level }}</strong>
          </div>
          <div class="suitability-result-cell">
            <span>限制原因</span>
            <strong>{{ suitabilityRestrictionText(suitabilityResult) }}</strong>
            <small v-if="selectedSuitabilityProduct">{{ selectedSuitabilityProduct.name }} · 规则 {{ suitabilityResult.decision_rule_version }}</small>
          </div>
        </div>
      </article>

      <!-- 产品购买判断（功能 9、10） -->
      <article v-if="!isStaffView" class="insight-panel products-panel">
        <div class="insight-panel-head">
          <div><span class="section-label">PRODUCT SUITABILITY</span><h3>产品购买判断</h3></div>
          <span>客户 {{ products?.customer_risk_level || 'C1' }} · 可购上限 {{ products?.max_allowed_product_risk || 'R1' }}</span>
        </div>
        <div v-if="products?.purchase_blocked" class="blocked-bar"><strong>⚠ 购买已阻断：</strong>{{ products.purchase_blocked_reason }}</div>
        <div class="product-grid">
          <article v-for="p in products?.items || []" :key="p.product_id" class="product-card" :class="productStatusClass(p)">
            <header>
              <strong>{{ p.name }}</strong>
              <span class="pill" :class="productStatusClass(p) === 'can-buy' ? 'pill-ok' : productStatusClass(p) === 'needs-review' ? 'pill-warn' : 'pill-reject'">{{ productStatusText(p) }}</span>
            </header>
            <p class="product-risk">风险 {{ p.risk_level }}<span v-if="p.term_days"> · {{ p.term_days }} 天</span><span v-if="p.minimum_amount"> · 起投 {{ formatMoney(p.minimum_amount) }}</span></p>
            <p class="product-desc">{{ p.description || p.product_type }}</p>
            <ul class="product-reasons">
              <li v-for="(r, i) in p.reasons" :key="i">{{ r }}</li>
            </ul>
          </article>
        </div>
        <p v-if="products && !products.items?.length" class="panel-empty">暂无在售产品。</p>
      </article>
    </template>

    <div v-if="conversationOpen" class="profile-dialog-backdrop" @click.self="closeConversation">
      <section class="profile-dialog" role="dialog" aria-modal="true" aria-labelledby="profile-dialog-title">
        <header class="profile-dialog-header">
          <div>
            <span class="section-label">AI PROFILE ASSISTANT</span>
            <h3 id="profile-dialog-title">和 AI 对话完善个人画像</h3>
          </div>
          <button class="dialog-close" :disabled="extracting" aria-label="关闭" @click="closeConversation">×</button>
        </header>
        <div class="profile-dialog-messages">
          <div class="dialog-bubble assistant-bubble">你好，请告诉我你的职业、收入、资产、投资经验、风险承受能力或产品偏好。我只会提取明确表达的信息。</div>
          <div v-if="conversationResult" class="dialog-bubble user-bubble">{{ convText }}</div>
          <div v-if="conversationResult" class="dialog-bubble assistant-bubble">
            {{ conversationResult.summary }}
            <span v-if="conversationResult.conflict_ids?.length" class="dialog-conflict">发现 {{ conversationResult.conflict_ids.length }} 项标签冲突，已保留待确认。</span>
          </div>
        </div>
        <textarea
          v-model="convText"
          class="profile-dialog-input"
          rows="4"
          placeholder="例如：我是工程师，月收入2万元，有5年投资经验，能接受10%的波动，偏好基金和债券。"
          :disabled="extracting"
        ></textarea>
        <p class="profile-dialog-hint">标签初始置信度：风评问卷 90% · LLM 提取 60% · 用户自述 40%</p>
        <div v-if="conversationResult?.tags?.length" class="dialog-tags">
          <span v-for="tag in conversationResult.tags" :key="tag.tag_code" class="dialog-tag">
            {{ TAG_LABELS[tag.tag_code] || tag.tag_code }}：{{ formatTagValue(tag) }} · {{ formatPercent(tag.confidence) }}
          </span>
        </div>
        <p v-if="extractMsg" class="notice">{{ extractMsg }}</p>
        <footer class="profile-dialog-actions">
          <button class="button button-quiet" :disabled="extracting" @click="closeConversation">关闭</button>
          <button class="button" :disabled="extracting || convText.trim().length < 10" @click="extract">{{ extracting ? '提取并更新中…' : '提取并更新画像' }}</button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.profile-insight-section { margin-top: 26px; padding-top: 24px; border-top: 1px solid #e8edf5; }
.profile-insight-header, .insight-panel-head { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }
.profile-insight-header h2, .insight-panel-head h3 { margin: 4px 0 6px; color: #263b59; }
.profile-insight-header p { margin: 0; color: #71809a; font-size: 13px; }
.profile-stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 12px; margin: 18px 0; }
.profile-stat-grid article { min-height: 86px; background: linear-gradient(135deg, #fbfcff, #f3f7fc); border: 1px solid #e2eaf4; border-radius: 12px; padding: 14px 16px; }
.profile-stat-grid span, .profile-stat-grid small { display: block; color: #8a94a6; font-size: 12px; }
.profile-stat-grid strong { display: block; margin: 7px 0 2px; font-size: 20px; color: #263b59; }
.restriction-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; background: #fff8e6; border: 1px solid #f5e3b8; border-radius: 10px; padding: 10px 14px; margin-bottom: 16px; color: #8a6d1a; }
.pill-warn { background: #fff3cd; color: #8a6d1a; }
.profile-detail-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr); gap: 14px; margin-bottom: 14px; }
.insight-panel { border: 1px solid #e2eaf4; border-radius: 14px; background: #fff; padding: 18px; }
.insight-panel-head { margin-bottom: 16px; }
.insight-panel-head > span { color: #8a94a6; font-size: 12px; white-space: nowrap; }
.section-label { color: #4e7cf0; font-size: 11px; font-weight: 700; letter-spacing: .12em; }
.dimension-list { display: grid; gap: 14px; }
.dimension-row { display: grid; grid-template-columns: minmax(126px, .9fr) minmax(90px, 1fr) 40px; align-items: center; gap: 10px; }
.dimension-name strong, .dimension-name span { display: block; }
.dimension-name strong { color: #33405c; font-size: 14px; }
.dimension-name span { margin-top: 3px; color: #8a94a6; font-size: 12px; }
.dimension-track { height: 7px; overflow: hidden; border-radius: 99px; background: #eaf0f8; }
.dimension-track i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #4c78ee, #63c8bd); }
.dimension-row b { color: #526581; font-size: 12px; text-align: right; }
.breakdown-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.breakdown-grid div { padding: 10px; border-radius: 9px; background: #f7f9fc; }
.breakdown-grid span, .breakdown-grid strong { display: block; }
.breakdown-grid span { color: #8a94a6; font-size: 12px; }
.breakdown-grid strong { margin-top: 4px; color: #33405c; font-size: 14px; }
.extract-panel { margin: 14px 0; background: #f5f8fe; border: 1px solid #dbe6f7; border-radius: 12px; padding: 18px; }
.extract-panel h3 { margin: 0 0 5px; color: #33405c; }
.extract-panel p { margin: 0 0 10px; color: #71809a; font-size: 13px; }
.extract-panel textarea { box-sizing: border-box; width: 100%; border: 1px solid #d5ddeb; border-radius: 8px; padding: 10px; font-size: 13px; resize: vertical; margin-bottom: 10px; }
.button-ai { background: linear-gradient(135deg, #4e7cf0, #6b4ff5); color: #fff; border-color: transparent; }
.profile-dialog-backdrop { position: fixed; z-index: 30; inset: 0; display: grid; place-items: center; padding: 20px; background: rgba(24, 40, 64, .42); }
.profile-dialog { width: min(620px, 100%); max-height: min(720px, calc(100vh - 40px)); overflow: auto; border: 1px solid #dbe6f7; border-radius: 16px; background: #fff; box-shadow: 0 24px 70px rgba(28, 53, 91, .26); padding: 20px; }
.profile-dialog-header, .profile-dialog-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.profile-dialog-header h3 { margin: 5px 0 0; color: #263b59; }
.dialog-close { border: 0; background: transparent; color: #71809a; font-size: 26px; cursor: pointer; }
.profile-dialog-messages { display: grid; gap: 10px; margin: 18px 0 12px; }
.dialog-bubble { max-width: 88%; border-radius: 12px; padding: 10px 12px; font-size: 13px; line-height: 1.6; }
.assistant-bubble { justify-self: start; background: #f1f5fb; color: #526581; }
.user-bubble { justify-self: end; background: #e6f7f4; color: #245d59; }
.dialog-conflict { display: block; margin-top: 6px; color: #c7791a; font-weight: 600; }
.profile-dialog-input { box-sizing: border-box; width: 100%; border: 1px solid #d5ddeb; border-radius: 9px; padding: 11px; color: #263b59; font: inherit; resize: vertical; }
.profile-dialog-hint { margin: 8px 0 10px; color: #8a94a6; font-size: 12px; }
.dialog-tags { display: flex; flex-wrap: wrap; gap: 7px; margin: 8px 0 12px; }
.dialog-tag { border: 1px solid #dbe6f7; border-radius: 99px; background: #f5f8fe; color: #526581; padding: 5px 9px; font-size: 12px; }
.profile-dialog-actions { justify-content: flex-end; margin-top: 14px; }
.tag-panel { margin-top: 14px; }
.conflict-panel { margin-top: 14px; }
.conflict-list { display: grid; gap: 10px; }
.conflict-item { border: 1px solid #f0d9a6; border-radius: 10px; background: #fffaf0; padding: 12px 14px; }
.conflict-item header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.conflict-item header strong { color: #6b4f1d; }
.conflict-values { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 10px; }
.conflict-values div { padding: 9px 10px; border-radius: 8px; background: rgba(255, 255, 255, .72); }
.conflict-values span, .conflict-values strong, .conflict-values small { display: block; }
.conflict-values span, .conflict-values small { color: #8a6d1a; font-size: 11px; }
.conflict-values strong { margin: 4px 0; color: #4d3b1d; font-size: 14px; }
.conflict-resolution { margin: 8px 0 0; color: #8a6d1a; font-size: 12px; }
.tag-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.tag-card { border: 1px solid #e6ebf3; border-radius: 11px; padding: 12px 14px; background: #fbfcfe; }
.tag-card.needs_review { border-color: #f0ad4e; background: #fffaf0; }
.tag-card header, .tag-card footer { display: flex; justify-content: space-between; gap: 8px; }
.tag-card header { align-items: center; }
.tag-card header strong { color: #33405c; }
.tag-value { margin: 9px 0; font-size: 15px; font-weight: 650; color: #172033; }
.tag-card footer { color: #8a94a6; font-size: 11px; }
.tag-review { color: #c7791a; font-weight: 600; }
.tag-card blockquote { margin: 9px 0 0; padding: 7px 10px; background: #f2f6fb; border-left: 3px solid #74d6c6; font-size: 12px; color: #55617a; }
.profile-empty, .panel-empty { color: #71809a; font-size: 13px; }
.profile-empty { border: 1px dashed #d7e1ee; border-radius: 12px; padding: 22px; background: #fbfcfe; text-align: center; }
.profile-empty.compact { padding: 16px; }
.panel-empty { margin: 0; }
.notice { color: #2aa79a; }
.risk-panel { margin: 14px 0; }
.risk-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.risk-grid div { padding: 10px 12px; border-radius: 9px; background: #f7f9fc; }
.risk-grid span, .risk-grid strong { display: block; }
.risk-grid span { color: #8a94a6; font-size: 12px; }
.risk-grid strong { margin-top: 4px; color: #33405c; font-size: 14px; }
.risk-grid .text-danger { color: #c62828; }
.pill-ok { background: #e6f6ec; color: #2e7d32; }
.pill-warn { background: #fff4e0; color: #c7791a; }
.pill-reject { background: #fdeaea; color: #c62828; }
.pill { display: inline-block; padding: 3px 10px; border-radius: 99px; font-size: 12px; font-weight: 600; }
.warn-text { color: #c7791a; font-weight: 600; }
/* 我的信息 */
.myinfo-panel, .history-panel, .products-panel { margin: 14px 0; }
.heading-actions { display: flex; align-items: center; gap: 10px; }
.button-small { padding: 5px 12px; font-size: 12px; }
.myinfo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
.myinfo-grid div, .asset-strip div { padding: 10px 12px; border-radius: 9px; background: #f7f9fc; }
.myinfo-grid span, .myinfo-grid strong, .asset-strip span, .asset-strip strong { display: block; }
.myinfo-grid span, .asset-strip span { color: #8a94a6; font-size: 12px; }
.myinfo-grid strong, .asset-strip strong { margin-top: 4px; color: #33405c; font-size: 14px; }
.asset-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 10px; }
.myinfo-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.myinfo-form label { display: grid; gap: 5px; color: #55617a; font-size: 13px; }
.myinfo-form select, .myinfo-form input { box-sizing: border-box; width: 100%; border: 1px solid #d5ddeb; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
.myinfo-form-footer { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 10px; }
.button-quiet { background: #f2f5fa; color: #55617a; border: 1px solid #d5ddeb; }
/* 版本历史 */
.history-timeline { display: grid; gap: 0; }
.history-item { position: relative; display: grid; grid-template-columns: 14px 1fr; gap: 12px; padding-bottom: 14px; }
.history-item:not(:last-child)::before { content: ''; position: absolute; left: 6px; top: 16px; bottom: 0; width: 2px; background: #e6ebf3; }
.history-dot { width: 14px; height: 14px; border-radius: 50%; border: 3px solid #fff; box-shadow: 0 0 0 1px #dde4ef; margin-top: 3px; }
.history-body header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.history-body header strong { color: #33405c; }
.history-body header time { color: #8a94a6; font-size: 12px; margin-left: auto; }
.history-body p { margin: 6px 0 0; color: #71809a; font-size: 13px; }
/* 单产品适当性检查 */
.suitability-gate-panel { margin: 14px 0; }
.suitability-controls { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(260px, 1.35fr) auto; gap: 14px; align-items: end; }
.suitability-controls label { display: grid; gap: 7px; color: #526581; font-size: 12px; }
.suitability-controls select { box-sizing: border-box; width: 100%; min-height: 42px; padding: 10px 12px; border: 1px solid #cbd8e8; border-radius: 7px; background: #fff; color: #263b59; font-size: 13px; }
.suitability-controls select:focus { outline: 2px solid #b8c9ff; outline-offset: 1px; border-color: #4e7cf0; }
.suitability-check-button { min-height: 42px; white-space: nowrap; border-radius: 6px; background: #18794e; box-shadow: none; }
.suitability-check-button:hover:not(:disabled) { background: #12603e; }
.suitability-error { margin: 12px 0 0; }
.suitability-result { display: grid; grid-template-columns: minmax(150px, .8fr) minmax(210px, 1.15fr) minmax(260px, 1.7fr); margin-top: 18px; border: 1px solid #d5e0e8; background: #fff; }
.suitability-result.suitability-pass { border-left: 4px solid #18794e; }
.suitability-result.suitability-reject { border-left: 4px solid #c33b52; }
.suitability-result.suitability-review { border-left: 4px solid #c7791a; }
.suitability-result-cell { min-height: 80px; padding: 15px 18px; border-right: 1px solid #d5e0e8; }
.suitability-result-cell:last-child { border-right: 0; }
.suitability-result-cell span, .suitability-result-cell strong, .suitability-result-cell small { display: block; }
.suitability-result-cell span { color: #71809a; font-size: 12px; }
.suitability-result-cell strong { margin-top: 9px; color: #263b59; font-size: 16px; }
.suitability-decision-cell strong { color: #18794e; }
.suitability-reject .suitability-decision-cell strong { color: #c33b52; }
.suitability-review .suitability-decision-cell strong { color: #c7791a; }
.suitability-result-cell small { margin-top: 7px; color: #8a94a6; font-size: 11px; }
/* 产品购买判断 */
.blocked-bar { background: #fdeaea; border: 1px solid #f5c6c6; border-radius: 10px; padding: 10px 14px; margin-bottom: 12px; color: #c62828; font-size: 13px; }
.product-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.product-card { border: 1px solid #e6ebf3; border-radius: 12px; padding: 14px; background: #fbfcfe; }
.product-card.can-buy { border-left: 4px solid #2e9e5b; }
.product-card.needs-review { border-left: 4px solid #c7791a; }
.product-card.cannot-buy { border-left: 4px solid #c62828; }
.product-card header { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.product-card header strong { color: #33405c; font-size: 14px; }
.product-risk { margin: 8px 0 4px; color: #526581; font-size: 13px; font-weight: 600; }
.product-desc { margin: 0 0 8px; color: #8a94a6; font-size: 12px; }
.product-reasons { margin: 0; padding-left: 18px; color: #71809a; font-size: 12px; }
.product-reasons li { margin: 3px 0; }
@media (max-width: 880px) { .profile-detail-grid { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .profile-insight-header, .insight-panel-head { flex-direction: column; } .dimension-row { grid-template-columns: 1fr; gap: 6px; } .dimension-row b { text-align: left; } .breakdown-grid { grid-template-columns: 1fr; } .myinfo-form { grid-template-columns: 1fr; } .product-grid { grid-template-columns: 1fr; } .suitability-controls, .suitability-result { grid-template-columns: 1fr; } .suitability-check-button { width: 100%; } .suitability-result-cell { border-right: 0; border-bottom: 1px solid #d5e0e8; } .suitability-result-cell:last-child { border-bottom: 0; } }
</style>
