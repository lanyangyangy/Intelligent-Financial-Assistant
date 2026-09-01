<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { tradingApi } from '../api/trading'
import PaginationBar from '../components/PaginationBar.vue'
const orders=ref<any[]>([]);const error=ref('');const loading=ref(true);const page=ref(1);const pageSize=ref(10);const total=ref(0);const pages=computed(()=>Math.max(1,Math.ceil(total.value/pageSize.value)))
async function load(){loading.value=true;try{const r=(await tradingApi.pending(pageSize.value,(page.value-1)*pageSize.value)).data;orders.value=r.data.items;total.value=r.data.total}catch(e:any){error.value=e.response?.data?.detail||e.message}finally{loading.value=false}}
function changeSize(n:number){pageSize.value=n;page.value=1;load()}function go(n:number){page.value=Math.min(Math.max(n,1),pages.value);load()}
async function approve(id:string){await tradingApi.approve(id,'客户经理审核通过，Mock 执行');await load()}async function reject(id:string){await tradingApi.reject(id,'客户经理审核拒绝');await load()}
onMounted(load)
</script>
<template><section class="table-module"><div class="module-header"><div><span class="module-kicker">ORDER REVIEW</span><h2>订单审核</h2></div><span class="table-summary">待审核</span></div><p v-if="error" class="error">{{error}}</p><div v-if="loading" class="empty">正在读取待审核订单…</div><div v-else class="module-table-wrap"><table class="admin-table"><thead><tr><th>订单号</th><th>客户</th><th>产品</th><th>金额</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="order in orders" :key="order.id"><td>{{order.order_no}}</td><td>{{order.user_id}}</td><td>{{order.product_name||order.product_id}}</td><td>¥{{order.amount}}</td><td>{{order.created_at}}</td><td><button class="table-action" @click="approve(order.id)">通过并执行</button><button class="table-action danger" @click="reject(order.id)">拒绝</button></td></tr><tr v-if="!orders.length"><td colspan="6" class="table-empty">暂无待审核订单</td></tr></tbody></table></div><PaginationBar :page="page" :page-size="pageSize" :total="total" @change="go" @size-change="changeSize"/></section></template>
