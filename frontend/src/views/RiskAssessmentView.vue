<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { riskApi, type RiskQuestion, type SuitabilityCheckResult } from '../api/risk'
import { profileApi } from '../api/profile'
import { useAuth } from '../stores/auth'

const router = useRouter()
const auth = useAuth()
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const message = ref('')
const questions = ref<RiskQuestion[]>([])
const answers = ref<Record<number, string>>({})
const result = ref<any>(null)
const checkLoading = ref(false)
const checks = ref<SuitabilityCheckResult[]>([])

const DIMENSION_LABELS: Record<string, string> = {
  income: '收入状况', experience: '投资经验', risk_tolerance: '风险承受力', goal: '投资目标', liquidity: '流动性',
}
const answeredCount = computed(() => questions.value.filter(q => answers.value[q.q]).length)
const allAnswered = computed(() => questions.value.length > 0 && answeredCount.value === questions.value.length)

async function load() {
  loading.value = true; error.value = ''
  try {
    const r = await riskApi.questionnaire()
    questions.value = r.data.data.items
    for (const q of questions.value) if (!answers.value[q.q]) answers.value[q.q] = 'A'
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
}

async function submit() {
  if (!auth.me.value?.id) { error.value = '未获取到客户信息，请重新登录'; return }
  if (!allAnswered.value) { error.value = `请完成全部 ${questions.value.length} 道题目`; return }
  submitting.value = true; error.value = ''; message.value = ''
  const payload = questions.value.map(q => ({ q: q.q, a: answers.value[q.q] }))
  try {
    const r = await riskApi.submitAssessment(auth.me.value.id, payload)
    result.value = r.data.data
    message.value = `风评提交成功：${r.data.data.risk_level} ${r.data.data.level_name}（得分 ${r.data.data.score} 分）`
    await runChecks()
  } catch (e: any) { error.value = e.response?.data?.error?.message || e.message } finally { submitting.value = false }
}

async function runChecks() {
  if (!auth.me.value?.id) return
  checkLoading.value = true
  try {
    const prods = (await profileApi.products()).data.data as any[]
    checks.value = []
    for (const p of prods) {
      try {
        const c = (await riskApi.suitabilityCheck(auth.me.value.id, p.id)).data.data
        checks.value.push(c)
      } catch { /* skip individual product failures */ }
    }
  } catch { /* ignore */ } finally { checkLoading.value = false }
}

function goProfile() {
  // 跳回客户中心并切换至"个人画像"标签页（缓存已失效，重新加载即为最新画像）
  sessionStorage.setItem('customer_center_tab', 'profile')
  router.push('/customer-center')
}

function backToCenter() {
  // 普通返回：清除画像定位标记，回到客户中心默认看板
  try { sessionStorage.removeItem('customer_center_tab') } catch { /* ignore */ }
  router.push('/customer-center')
}

onMounted(async () => {
  await auth.loadMe()
  await load()
})
</script>

<template>
  <section class="form-module">
    <div class="page-nav-bar">
      <button class="button button-nav" type="button" @click="backToCenter">
        <span class="nav-arrow">←</span> 返回客户中心
      </button>
      <button class="button button-nav button-nav-quiet" type="button" @click="router.push('/')">返回前台</button>
      <span class="nav-spacer"></span>
      <span class="nav-badge">风险测评</span>
    </div>
    <div class="section-heading">
      <div><span class="eyebrow">RISK ASSESSMENT</span><h2>风险测评</h2>
        <p>共 {{ questions.length }} 道题目，覆盖收入、投资经验、风险承受力、投资目标、流动性五个维度。</p></div>
      <span v-if="questions.length" class="pill">{{ answeredCount }}/{{ questions.length }} 已作答</span>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="message" class="success">{{ message }}</p>
    <div v-if="loading" class="empty">正在加载测评问卷…</div>

    <template v-else>
      <div v-if="result" class="profile-panel">
        <h3>测评结果：<strong :class="'level-' + result.risk_level">{{ result.risk_level }} {{ result.level_name }}</strong></h3>
        <p>得分：<strong>{{ result.score }}</strong> 分 · 已作答 {{ result.answered }} 题</p>
        <p class="hint">分级标准：C1 保守型 0-20 · C2 稳健型 21-40 · C3 平衡型 41-60 · C4 进取型 61-80 · C5 激进型 81-100</p>
        <p class="link-row">
          <a class="button button-small" href="/customer-center/risk-assessment#profile" @click.prevent="goProfile">→ 查看更新后的个人画像（风险等级已同步）</a>
        </p>
      </div>

      <form class="risk-questionnaire" @submit.prevent="submit">
        <div v-for="q in questions" :key="q.q" class="risk-question">
          <div class="risk-question-head">
            <span class="risk-qno">Q{{ q.q }}</span>
            <span class="pill pill-dim">{{ DIMENSION_LABELS[q.dimension] || q.dimension }}</span>
          </div>
          <p class="risk-question-text">{{ q.question }}</p>
          <div class="risk-options">
            <label v-for="opt in q.options" :key="opt.key" class="risk-option"
              :class="{ selected: answers[q.q] === opt.key }">
              <input type="radio" :name="'q' + q.q" :value="opt.key" v-model="answers[q.q]" />
              <span class="risk-option-key">{{ opt.key }}</span>
              <span class="risk-option-text">{{ opt.text }}</span>
            </label>
          </div>
        </div>
        <div class="risk-submit">
          <button class="button" type="submit" :disabled="submitting || !allAnswered">
            {{ submitting ? '提交中…' : (result ? '重新测评' : '提交测评') }}
          </button>
        </div>
      </form>

      <div v-if="checks.length" class="suitability-panel">
        <h3>适当性匹配检查（客户风险 vs 产品风险）</h3>
        <div v-if="checkLoading" class="empty">正在检查…</div>
        <div class="suitability-grid">
          <div v-for="c in checks" :key="c.product_id" class="suitability-item" :class="{ ok: c.matched, warn: !c.matched }">
            <div class="suitability-name">
              <strong>{{ c.product_name || c.product_id }}</strong>
              <span class="pill">产品 {{ c.product_risk_level }}</span>
            </div>
            <div class="suitability-status">
              <span v-if="c.matched" class="tag-ok">✓ 可购买</span>
              <span v-else class="tag-warn">✗ 不匹配</span>
              <small>最高可购 {{ c.max_allowed_product_risk }}</small>
            </div>
            <p v-if="c.warning" class="suitability-warning">{{ c.warning }}</p>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* 顶部导航栏：返回客户中心（醒目大按钮） */
.page-nav-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; padding: 14px 18px; background: #fff; border: 1px solid #e6ebf3; border-radius: 14px; box-shadow: 0 2px 10px #eef2f8; }
.button-nav { padding: 11px 22px; font-size: 15px; gap: 8px; display: inline-flex; align-items: center; }
.button-nav .nav-arrow { font-size: 18px; line-height: 1; }
.button-nav-quiet { background: #f2f5fa; color: #55617a; border: 1px solid #d5ddeb; }
.button-nav-quiet:hover { border-color: #74d6c6; color: #0e7c6d; }
.nav-spacer { flex: 1; }
.nav-badge { color: #8a94a6; font-size: 13px; font-weight: 600; }
@media (max-width: 600px) {
  .page-nav-bar { flex-wrap: wrap; }
  .nav-spacer { display: none; }
  .button-nav { flex: 1; justify-content: center; }
}
.risk-questionnaire { display: flex; flex-direction: column; gap: 20px; margin-top: 20px; }
.risk-question { border: 1px solid #e6ebf3; border-radius: 12px; padding: 16px 18px; background: #fff; }
.risk-question-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.risk-qno { font-weight: 700; color: #0e7c6d; background: #e6f7f4; padding: 2px 10px; border-radius: 999px; font-size: 13px; }
.pill-dim { background: #eef3fa; color: #3d5a80; }
.risk-question-text { margin: 0 0 12px; font-weight: 600; color: #172033; }
.risk-options { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.risk-option { display: flex; align-items: center; gap: 8px; border: 1px solid #d5ddeb; border-radius: 8px; padding: 8px 12px; cursor: pointer; transition: all 0.15s; font-size: 13px; }
.risk-option:hover { border-color: #74d6c6; }
.risk-option.selected { border-color: #74d6c6; background: #e6f7f4; }
.risk-option input { accent-color: #2aa79a; }
.risk-option-key { font-weight: 700; color: #55617a; background: #f0f3f8; border-radius: 6px; padding: 1px 7px; }
.risk-option.selected .risk-option-key { background: #74d6c6; color: #fff; }
.risk-submit { text-align: center; margin-top: 6px; }
.hint { color: #8a94a6; font-size: 12px; }
.level-C1 { color: #2e7d32; } .level-C2 { color: #7d9e2e; }
.level-C3 { color: #e65100; } .level-C4 { color: #c62828; } .level-C5 { color: #6a1b9a; }
.suitability-panel { margin-top: 28px; border-top: 2px solid #e6ebf3; padding-top: 20px; }
.suitability-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-top: 12px; }
.suitability-item { border: 1px solid #e6ebf3; border-radius: 12px; padding: 12px 14px; }
.suitability-item.ok { background: #f2fbf7; }
.suitability-item.warn { background: #fff8f0; border-color: #f0c78a; }
.suitability-name { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.suitability-status { display: flex; align-items: center; gap: 10px; }
.tag-ok { color: #2e7d32; font-weight: 700; }
.tag-warn { color: #c7791a; font-weight: 700; }
.suitability-status small { color: #8a94a6; }
.suitability-warning { margin: 8px 0 0; font-size: 12px; color: #c7791a; }
</style>
