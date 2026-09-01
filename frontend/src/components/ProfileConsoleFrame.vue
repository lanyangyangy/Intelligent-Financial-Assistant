<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { http } from '../api/http'

type ConsoleSession = {
  console_url: string
  api_base: string
  session: {
    customer_id: string
    login_name: string
    display_name: string
    user_role: string
    auth_mode: string
  }
}

const frame = ref<HTMLIFrameElement | null>(null)
const loading = ref(true)
const error = ref('')
const config = ref<ConsoleSession | null>(null)
const consoleOrigin = computed(() => {
  if (!config.value) return ''
  return new URL(config.value.console_url).origin
})

function sendSession() {
  if (!frame.value?.contentWindow || !config.value || !consoleOrigin.value) return
  const accessToken = sessionStorage.getItem('access_token')
  if (!accessToken) return
  const { session, api_base: apiBase } = config.value
  frame.value.contentWindow.postMessage(
    {
      type: 'profile-console:session',
      session: {
        customer_id: session.customer_id,
        login_name: session.login_name,
        display_name: session.display_name,
        user_role: session.user_role,
        auth_mode: session.auth_mode,
      },
      apiBase,
      accessToken,
      parentOrigin: window.location.origin,
    },
    consoleOrigin.value,
  )
}

function receiveMessage(event: MessageEvent) {
  if (event.origin !== consoleOrigin.value) return
  if (event.data?.type === 'profile-console:ready') sendSession()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    config.value = (await http.get('/profile-console/session')).data.data
  } catch (e: any) {
    error.value = e.message || '新版画像验收台暂不可用'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  window.addEventListener('message', receiveMessage)
  load()
})
onBeforeUnmount(() => window.removeEventListener('message', receiveMessage))
</script>

<template>
  <section class="embedded-profile-console" aria-label="个人画像验收台">
    <div v-if="loading" class="profile-console-state">正在加载新版个人画像验收台…</div>
    <div v-else-if="error" class="profile-console-state error-state">
      <strong>个人画像暂不可用</strong>
      <span>{{ error }}</span>
      <button class="button" @click="load">重新连接</button>
    </div>
    <iframe
      v-else-if="config"
      ref="frame"
      class="profile-console-frame"
      :src="config.console_url"
      title="客户画像验收台"
      @load="sendSession"
    />
  </section>
</template>

<style scoped>
.embedded-profile-console { min-height: 780px; }
.profile-console-frame { width: 100%; min-height: 1500px; border: 0; display: block; background: #f4f6f2; }
.profile-console-state { min-height: 320px; display: grid; place-content: center; gap: 12px; text-align: center; color: #6e7d92; background: #fff; border: 1px solid #dfe6f0; border-radius: 16px; }
.error-state strong { color: #b94a48; }
.profile-console-state .button { justify-self: center; }
@media (max-width: 900px) { .profile-console-frame { min-height: 1800px; } }
</style>
