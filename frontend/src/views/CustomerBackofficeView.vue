<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { roleLabel } from '../constants/roles'
import { tradingApi } from '../api/trading'
import { profileApi } from '../api/profile'
import OrdersView from './OrdersView.vue'
import ProfileEnhancedView from './ProfileEnhancedView.vue'
import CustomerProfileConversationPanel from '../components/CustomerProfileConversationPanel.vue'

const auth = useAuth()
const router = useRouter()
// 支持从风评问卷页跳回时自动切换标签（sessionStorage 标记由 RiskAssessmentView 写入）
const active = ref(sessionStorage.getItem('customer_center_tab') || 'dashboard')
const account = ref<any>(null)
const orders = ref<any[]>([])
const asset = ref<any>(null)
const holdings = ref<any[]>([])
const productNames = ref<Record<string, string>>({})
const profileRefreshKey = ref(0)
const error = ref('')
const loading = ref(true)
const items = [
  { key: 'dashboard', label: '我的看板', icon: '▦' },
  { key: 'profile', label: '个人画像', icon: '◉' },
  { key: 'risk', label: '风险测评', icon: '◷' },
  { key: 'orders', label: '我的订单', icon: '▤' },
  { key: 'assets', label: '资产与持仓', icon: '◇' },
]
const currentTitle = computed(() => items.find(item => item.key === active.value)?.label || '我的看板')
const totalProfitLoss = computed(() => holdings.value.reduce((total, holding) => total + Number(holding.profit_loss || 0), 0))
const formatMoney = (value: unknown) => Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const formatNumber = (value: unknown) => Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
const formatDate = (value: unknown) => value ? new Date(String(value)).toLocaleDateString('zh-CN') : '-'
const productName = (productId: string) => productNames.value[productId] || productId
const profitClass = (value: unknown) => Number(value || 0) >= 0 ? 'positive' : 'negative'
async function logout() { await auth.logout(); await router.push('/login') }
function switchTab(key: string) {
  active.value = key
  try { sessionStorage.removeItem('customer_center_tab') } catch { /* ignore */ }
}
function refreshProfile() { profileRefreshKey.value += 1 }
async function loadCustomerData() {
  try {
    const [accountResponse, ordersResponse, summaryResponse] = await Promise.all([
      tradingApi.account(),
      tradingApi.orders(),
      profileApi.summary(),
    ])
    account.value = accountResponse.data.data
    orders.value = ordersResponse.data.data
    const summary = summaryResponse.data.data
    asset.value = summary.latest_asset
    holdings.value = summary.holdings || []

    try {
      const products = (await profileApi.products()).data.data || []
      productNames.value = Object.fromEntries(products.map((product: any) => [product.id, product.name]))
    } catch {
      // 产品名称加载失败时仍显示 product_id，避免影响资产与持仓数据展示。
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}
onMounted(loadCustomerData)
</script>

<template>
  <div class="portal-shell customer-portal">
    <aside class="portal-sidebar">
      <div class="portal-brand"><div class="portal-logo">W</div><div><strong>财富管理平台</strong><small>客户服务端</small></div></div>
      <div class="portal-user"><div class="portal-avatar">{{(auth.me.value?.display_name || '客').slice(0,1)}}</div><div><strong>{{auth.me.value?.display_name}}</strong><small>{{roleLabel(auth.me.value?.roles?.[0])}}</small></div></div>
      <nav class="portal-nav">
        <button class="portal-return" @click="router.push('/')"><span>⌂</span> 返回前台</button>
        <div class="portal-divider"></div>
        <button v-for="item in items" :key="item.key" :class="{ active: active === item.key }" @click="switchTab(item.key)"><span class="portal-icon">{{item.icon}}</span>{{item.label}}</button>
      </nav>
      <button class="portal-logout" @click="logout"><span>↪</span> 退出登录</button>
    </aside>
    <main class="portal-main">
      <header class="portal-content-header"><div><p>当前位置 / {{currentTitle}}</p><h1>{{currentTitle}}</h1></div><div class="portal-header-badge customer">客户中心</div></header>
      <section class="portal-content-body">
        <p v-if="error" class="error">{{error}}</p><div v-if="loading" class="empty">正在读取客户数据…</div>
        <template v-else>
          <div v-if="active === 'dashboard'" class="portal-dashboard"><div class="portal-stat-grid"><article><span>可用余额</span><strong>¥{{account?.available_balance ?? 0}}</strong></article><article><span>冻结金额</span><strong>¥{{account?.frozen_balance ?? 0}}</strong></article><article><span>订单数量</span><strong>{{orders.length}}</strong></article></div></div>
           <section v-else-if="active === 'profile'" class="profile-workspace">
             <CustomerProfileConversationPanel @updated="refreshProfile" />
             <ProfileEnhancedView :key="profileRefreshKey" />
           </section>
           <div v-else-if="active === 'risk'" class="risk-entry-panel">
             <div class="section-heading"><div><span class="eyebrow">RISK ASSESSMENT</span><h2>风险测评</h2><p>完成 16 题问卷，系统将判定您的风险等级（C1-C5）并匹配可购买产品。</p></div></div>
             <div class="risk-entry-actions">
               <button class="button" @click="router.push('/customer-center/risk-assessment')">开始风险测评</button>
               <button class="button button-quiet" @click="router.push('/customer-center')">返回客户中心</button>
             </div>
           </div>
           <OrdersView v-else-if="active === 'orders'" />
           <div v-else-if="active === 'assets'" class="asset-workspace">
             <div class="asset-summary-grid">
               <article class="asset-total-card"><span class="asset-kicker">NET WORTH</span><span>净资产</span><strong>¥{{formatMoney(asset?.net_asset)}}</strong><small>数据更新于 {{formatDate(asset?.snapshot_time)}}</small></article>
               <article class="asset-summary-card"><span>总资产</span><strong>¥{{formatMoney(asset?.total_asset)}}</strong><small>现金与持仓市值合计</small></article>
               <article class="asset-summary-card"><span>现金余额</span><strong>¥{{formatMoney(asset?.cash_balance)}}</strong><small>账户可用与冻结资金</small></article>
               <article class="asset-summary-card"><span>可投资资产</span><strong>¥{{formatMoney(asset?.investable_asset)}}</strong><small>当前可配置资金</small></article>
               <article class="asset-summary-card"><span>负债</span><strong>¥{{formatMoney(asset?.liability)}}</strong><small>已计入净资产计算</small></article>
               <article class="asset-summary-card"><span>持仓盈亏</span><strong :class="profitClass(totalProfitLoss)">{{totalProfitLoss >= 0 ? '+' : ''}}¥{{formatMoney(Math.abs(totalProfitLoss))}}</strong><small>{{holdings.length}} 项在持仓</small></article>
             </div>
             <div class="portal-panel asset-holdings-panel">
               <div class="asset-panel-heading"><div><span class="module-kicker">PORTFOLIO HOLDINGS</span><h2>持仓明细</h2></div><span class="table-summary">{{holdings.length}} 项持仓</span></div>
               <div class="wide-table"><table class="admin-table asset-holdings-table"><thead><tr><th>产品</th><th>持有数量</th><th>成本金额</th><th>市值</th><th>盈亏</th><th>持有天数</th></tr></thead><tbody><tr v-for="holding in holdings" :key="holding.id"><td><strong>{{productName(holding.product_id)}}</strong><small class="asset-product-id">{{holding.product_id}}</small></td><td>{{formatNumber(holding.quantity)}}</td><td>¥{{formatMoney(holding.cost_amount)}}</td><td>¥{{formatMoney(holding.market_value)}}</td><td :class="profitClass(holding.profit_loss)">{{Number(holding.profit_loss || 0) >= 0 ? '+' : ''}}¥{{formatMoney(Math.abs(Number(holding.profit_loss || 0)))}}</td><td>{{holding.holding_days}} 天</td></tr><tr v-if="!holdings.length"><td colspan="6" class="table-empty">暂无持仓数据</td></tr></tbody></table></div>
             </div>
           </div>
           <div v-else class="portal-panel"><h2>{{currentTitle}}</h2><p>该模块将在后续业务阶段接入。</p></div>
        </template>
      </section>
    </main>
  </div>
</template>
