<script setup lang="ts">
import { computed } from 'vue'
import { useAuth } from '../stores/auth'
import { roleLabel } from '../constants/roles'
const auth=useAuth();const permissions=computed(()=>auth.me.value?.permissions||[])
const links=computed(()=>{const result:[string,string,string][]=[];if(permissions.value.includes('customer:read'))result.push(['客户管理','/employee-workspace/customers','查询客户资料、资产、持仓和订单']);if(permissions.value.includes('order:write'))result.push(['订单审核','/employee-workspace/orders','审核客户交易申请并执行 Mock 交易']);if(permissions.value.includes('product:write'))result.push(['产品管理','/employee-workspace/products','维护产品目录和产品状态']);if(permissions.value.includes('risk:write'))result.push(['风险管理','/employee-workspace/risk','处理风险和适当性任务']);if(permissions.value.includes('audit:read'))result.push(['审计查询','/employee-workspace/audit','查看系统审计记录']);return result})
</script>
<template><section class="workspace-page"><div class="section-heading"><div><span class="eyebrow">EMPLOYEE WORKSPACE</span><h2>工作后台</h2><p>后台业务与公开前台完全分离，当前权限决定可用工作台。</p></div><span class="pill">{{roleLabel(auth.me.value?.roles?.[0]||'employee_pending')}}</span></div><div v-if="!links.length" class="notice-card warning-card"><h3>等待管理员分组</h3><p>当前员工账号尚未分配理财顾问、风控专员、客户经理或审计角色。</p></div><div v-else class="dashboard-grid"><RouterLink v-for="item in links" :key="item[1]" :to="item[1]" class="dashboard-card"><span class="card-kicker">WORKSPACE</span><h3>{{item[0]}}</h3><p>{{item[2]}}</p><span class="card-link">进入 →</span></RouterLink></div></section></template>
