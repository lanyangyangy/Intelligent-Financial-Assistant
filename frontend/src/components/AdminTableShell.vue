<script setup lang="ts">
defineProps<{title:string; loading?:boolean; error?:string}>()
</script>
<template><section class="table-module"><div class="module-header"><div><span class="module-kicker">BUSINESS MODULE</span><h2>{{title}}</h2></div><slot name="tools"/></div><p v-if="error" class="error">{{error}}</p><div v-if="loading" class="empty">正在加载…</div><div v-else class="module-table-wrap"><slot/></div></section></template>
