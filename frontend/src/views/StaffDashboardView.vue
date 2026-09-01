<script setup lang="ts">
import { computed } from 'vue'
import { useAuth } from '../stores/auth'
import { roleLabel } from '../constants/roles'
const auth=useAuth();const role=computed(()=>auth.me.value?.roles?.[0]||'employee_pending')
const menus:Record<string,[string,string][]>={financial_advisor:[['客户管理','/staff/customers'],['客户画像','/staff/profiles'],['风险与产品匹配','/staff/suitability']],risk_specialist:[['风险测评','/staff/risk'],['适当性检查','/staff/suitability'],['风险预警','/staff/alerts']],customer_manager:[['客户管理','/staff/customers'],['订单管理','/staff/orders'],['业务操作','/staff/trades']],auditor:[['审计记录','/staff/audit'],['只读查询','/staff/readonly']]};const menu=computed(()=>menus[role.value]||[])
</script>
<template><section><div class="section-heading"><div><span class="eyebrow">STAFF WORKSPACE</span><h2>工作人员后台</h2><p>当前角色：<strong>{{roleLabel(role)}}</strong>。菜单根据后端权限显示。</p></div><span class="pill">{{roleLabel(role)}}</span></div><div v-if="role==='employee_pending'" class="notice-card warning-card"><h3>账号等待管理员分组</h3><p>当前账号已经注册成功，但尚未分配理财顾问、风控专员、客户经理或审计角色。</p></div><div v-else class="dashboard-grid"><RouterLink v-for="item in menu" :key="item[1]" :to="item[1]" class="dashboard-card"><span class="card-kicker">WORKSPACE</span><h3>{{item[0]}}</h3><p>进入{{item[0]}}工作台。</p><span class="card-link">进入 →</span></RouterLink></div></section></template>
