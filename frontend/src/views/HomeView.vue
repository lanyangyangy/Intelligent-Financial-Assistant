<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { healthApi } from '../api/health'
const status=ref('检查中');const checks=ref<Record<string,any>>({})
onMounted(async()=>{try{const r=await healthApi.check();status.value=r.data.data.status;checks.value=r.data.data.checks}catch{status.value='不可用'}})
</script>
<template><section class="hero"><div><span class="eyebrow">XX科技 · 财富服务</span><h2>专业、稳健、长期的综合金融服务。</h2><p>围绕个人财富管理、企业金融与高净值客户服务，提供产品配置、资产管理和专业顾问支持。</p><div class="hero-actions"><RouterLink class="button" to="/products">查看产品</RouterLink></div></div><div class="status-card"><span>系统状态</span><strong :class="status==='ok'?'ok':'warn'">{{status}}</strong><small v-for="(value,key) in checks" :key="key">{{key}}：{{value.status}}</small></div></section></template>
