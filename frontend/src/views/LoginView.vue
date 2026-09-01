<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
const router=useRouter();const auth=useAuth();const username=ref('');const password=ref('');const error=ref('');const loading=ref(false)
async function login(){loading.value=true;error.value='';try{await auth.login(username.value,password.value);await router.push('/')}catch(e:any){error.value=e.message}finally{loading.value=false}}
</script>
<template><section class="auth-page"><div class="auth-card"><span class="eyebrow">SECURE ACCESS</span><h2>登录智能财富管家</h2><p>使用开发环境测试账号或你的系统账号登录。</p><form @submit.prevent="login"><label>账号<input v-model="username" autocomplete="username" required /></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required /></label><p v-if="error" class="error">{{error}}</p><button class="button" :disabled="loading">{{loading?'登录中…':'登录'}}</button></form><button class="text-button" @click="router.push('/register')">还没有账号？去注册</button><small>开发测试账号请查看本地 <code>docs/DEMO_ACCOUNTS.txt</code>。</small></div></section></template>
