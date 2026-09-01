<script setup lang="ts">
import { useRoute } from 'vue-router'
const route=useRoute()
</script>
<template><section class="placeholder-page"><span class="eyebrow">MODULE PLACEHOLDER</span><h2>{{route.meta.title || '功能模块'}}</h2><p>页面结构已预留，下一阶段接入真实 API、权限和构造业务数据。</p><RouterLink class="button" to="/">返回总览</RouterLink></section></template>
