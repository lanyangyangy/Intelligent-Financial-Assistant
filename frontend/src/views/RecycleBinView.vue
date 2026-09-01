<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { adminApi } from '../api/admin'
import { profileApi } from '../api/profile'
const module=ref('user');const items=ref<any[]>([]);const total=ref(0);const loading=ref(false);const error=ref('')
async function load(){loading.value=true;error.value='';try{const r=(await adminApi.recycleBin(module.value,100,0)).data.data;items.value=r.items;total.value=r.total}catch(e:any){error.value=e.response?.data?.detail||e.message}finally{loading.value=false}}
async function restore(item:any){if(!window.confirm('确认恢复该数据？'))return;try{if(module.value==='user')await adminApi.restoreUser(item.id);else await profileApi.restoreProduct(item.id);await load()}catch(e:any){error.value=e.response?.data?.detail||e.message}}
onMounted(load)
</script>
<template><section class="table-module"><div class="module-header"><div><span class="module-kicker">SUPER ADMIN ONLY</span><h2>数据回收站</h2><p>仅超级管理员可以恢复已软删除的数据。</p></div><span class="table-summary">{{total}} 条</span></div><div class="module-tools"><label>恢复模块<select v-model="module" @change="load"><option value="user">用户数据</option><option value="product">产品数据</option></select></label><button class="table-action" @click="load">刷新</button></div><p v-if="error" class="error">{{error}}</p><div v-if="loading" class="empty">正在读取回收站…</div><div v-else class="module-table-wrap"><table class="admin-table"><thead><tr><th>名称</th><th>类型/角色</th><th>状态</th><th>创建时间</th><th>修改时间</th><th>删除时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td>{{item.name}}</td><td>{{module==='user' ? item.roles?.join('、') : item.key}}</td><td>{{item.status}}</td><td>{{item.created_at}}</td><td>{{item.updated_at}}</td><td>{{item.deleted_at}}</td><td><button class="table-action" @click="restore(item)">恢复数据</button></td></tr><tr v-if="!items.length"><td colspan="7" class="table-empty">当前模块回收站为空</td></tr></tbody></table></div></section></template>

