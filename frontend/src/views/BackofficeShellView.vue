<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import StaffCustomersView from './StaffCustomersView.vue'
import StaffOrdersView from './StaffOrdersView.vue'
import AdminWorkspaceView from './AdminWorkspaceView.vue'
import StaffProductsView from './StaffProductsView.vue'
import AuditLogsView from './AuditLogsView.vue'
import RecycleBinView from './RecycleBinView.vue'
import AgentChatView from './AgentChatView.vue'
import AdvisorWorkspaceView from './AdvisorWorkspaceView.vue'
import OperatorWorkspaceView from './OperatorWorkspaceView.vue'
import KnowledgeGraphView from './KnowledgeGraphView.vue'
import RiskAlertsView from './RiskAlertsView.vue'
import KnowledgeManageView from './KnowledgeManageView.vue'
import EvaluationReportView from './EvaluationReportView.vue'
import { roleLabel } from '../constants/roles'

const auth = useAuth()
const router = useRouter()
const active = ref('dashboard')
const isAdmin = computed(() => auth.me.value?.is_super_admin === true || auth.me.value?.roles?.includes('super_admin') === true)
const permissions = computed(() => auth.me.value?.permissions || [])
// 投顾工作台仅对“理财顾问”角色开放，其他角色（风控/客户经理/审计等）均不可见
const isAdvisor = computed(() => auth.me.value?.roles?.includes('financial_advisor') === true)
// 业务操作工作台：仅客户经理（+系统管理员）可见（需求文档 2.2：业务操作→客户经理）
const isOperator = computed(() => auth.me.value?.roles?.includes('customer_manager') === true || isAdmin.value)
// risk:read 用于客户风险信息/适当性查询，不等于风控预警操作权限。
// 预警与工单属于风控专员工作台，系统管理员通过 isAdmin 进入。
const canOperateRiskAlerts = computed(() => isAdmin.value || permissions.value.includes('risk:write'))

const items = computed(() => {
  const out = [{ key: 'chat', label: '智能助手', icon: '✦' }, { key: 'dashboard', label: '数据看板', icon: '▦' }, { key: 'profile', label: '个人信息', icon: '◉' }]
  if (isAdvisor.value) out.push({ key: 'advisor', label: '投顾工作台', icon: '◈' })
  if (isOperator.value) out.push({ key: 'operator', label: '操作工作台', icon: '⌘' })
  if (permissions.value.includes('customer:read')) out.push({ key: 'customers', label: '客户管理', icon: '♙' })
  if (permissions.value.includes('order:write')) out.push({ key: 'orders', label: '订单审核', icon: '✓' })
  if (permissions.value.includes('product:write')) out.push({ key: 'products', label: '产品管理', icon: '◇' })
  if (permissions.value.includes('risk:write')) out.push({ key: 'risk', label: '风险管理', icon: '△' })
  if (canOperateRiskAlerts.value) out.push({ key: 'alerts', label: '风控预警', icon: '⚠' })
  if (permissions.value.includes('audit:read')) out.push({ key: 'audit', label: '审计查询', icon: '▤' })
  if (permissions.value.includes('product:read')) out.push({ key: 'graph', label: '知识图谱', icon: '🕸' })
  if (permissions.value.includes('product:write')) out.push({ key: 'kb', label: '知识库管理', icon: '📚' })
  if (isAdmin.value) { out.push({ key: 'admin', label: '用户与权限', icon: '⚙' }); out.push({ key: 'recycle', label: '数据回收站', icon: '♻' }) }
  out.push({ key: 'eval', label: '评测报告', icon: '◈' })
  return out
})
const currentTitle = computed(() => items.value.find(item => item.key === active.value)?.label || '数据看板')
async function logout() { await auth.logout(); await router.push('/login') }

// 非理财顾问角色即使 active 残留为 advisor 也强制回到数据看板，
// 避免内容区落入“该模块将在后续业务阶段接入”占位或直接渲染投顾工作台。
watch(isAdvisor, (advisor) => {
  if (!advisor && active.value === 'advisor') active.value = 'dashboard'
}, { immediate: true })
</script>

<template>
  <div class="portal-shell">
    <aside class="portal-sidebar">
      <div class="portal-brand"><div class="portal-logo">W</div><div><strong>财富管理平台</strong><small>业务管理端</small></div></div>
      <div class="portal-user"><div class="portal-avatar">{{(auth.me.value?.display_name || '员').slice(0,1)}}</div><div><strong>{{auth.me.value?.display_name}}</strong><small>{{auth.me.value?.roles?.map(roleLabel).join('、')}}</small></div></div>
      <nav class="portal-nav">
        <button class="portal-return" @click="router.push('/')"><span>⌂</span> 返回前台</button>
        <div class="portal-divider"></div>
        <button v-for="item in items" :key="item.key" :class="{ active: active === item.key }" @click="active = item.key"><span class="portal-icon">{{item.icon}}</span>{{item.label}}</button>
      </nav>
      <button class="portal-logout" @click="logout"><span>↪</span> 退出登录</button>
    </aside>
    <main class="portal-main">
      <header class="portal-content-header"><div><p>当前位置 / {{currentTitle}}</p><h1>{{currentTitle}}</h1></div><div class="portal-header-badge">{{isAdmin ? '系统管理员' : roleLabel(auth.me.value?.roles?.[0])}}</div></header>
      <section class="portal-content-body">
        <AgentChatView v-if="active === 'chat'" />
        <AdvisorWorkspaceView v-else-if="active === 'advisor'" />
        <OperatorWorkspaceView v-else-if="active === 'operator'" />
        <KnowledgeGraphView v-else-if="active === 'graph'" />
        <RiskAlertsView v-else-if="active === 'alerts'" />
        <KnowledgeManageView v-else-if="active === 'kb'" />
        <div v-else-if="active === 'dashboard'" class="portal-dashboard"><div class="portal-stat-grid"><article><span>当前角色</span><strong>{{roleLabel(auth.me.value?.roles?.[0])}}</strong></article><article><span>授权模块</span><strong>{{items.length - 3}}</strong></article><article><span>账号状态</span><strong>正常</strong></article></div></div>
        <div v-else-if="active === 'profile'" class="portal-panel"><h2>个人信息</h2><dl class="portal-profile"><dt>用户名</dt><dd>{{auth.me.value?.username}}</dd><dt>姓名</dt><dd>{{auth.me.value?.display_name}}</dd><dt>角色</dt><dd>{{auth.me.value?.roles?.map(roleLabel).join('、')}}</dd></dl></div>
        <StaffCustomersView v-else-if="active === 'customers'" />
        <StaffOrdersView v-else-if="active === 'orders'" />
        <StaffProductsView v-else-if="active === 'products'" />
        <AuditLogsView v-else-if="active === 'audit'" />
        <RecycleBinView v-else-if="active === 'recycle'" />
        <AdminWorkspaceView v-else-if="active === 'admin'" />
        <EvaluationReportView v-else-if="active === 'eval'" />
        <div v-else class="portal-panel"><h2>{{currentTitle}}</h2><p>该模块将在后续业务阶段接入。</p></div>
      </section>
    </main>
  </div>
</template>






