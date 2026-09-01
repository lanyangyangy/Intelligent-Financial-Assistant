import { computed, ref } from 'vue'
import { authApi, type MeResponse } from '../api/auth'

const accessToken = ref<string | null>(sessionStorage.getItem('access_token'))
const refreshToken = ref<string | null>(sessionStorage.getItem('refresh_token'))
const me = ref<MeResponse | null>(null)
const loading = ref(false)
const initialized = ref(false)

function setTokens(access: string, refresh: string) {
  accessToken.value = access; refreshToken.value = refresh
  sessionStorage.setItem('access_token', access); sessionStorage.setItem('refresh_token', refresh)
  window.dispatchEvent(new CustomEvent('auth:changed'))
}
function clear() {
  accessToken.value = null; refreshToken.value = null; me.value = null
  sessionStorage.removeItem('access_token'); sessionStorage.removeItem('refresh_token')
  window.dispatchEvent(new CustomEvent('auth:changed'))
}
async function loadMe() {
  if (!accessToken.value) { initialized.value = true; return null }
  loading.value = true
  try { me.value = (await authApi.me()).data.data; return me.value }
  catch { clear(); return null }
  finally { loading.value = false; initialized.value = true }
}
async function login(username: string, password: string) {
  const result = await authApi.login(username, password)
  setTokens(result.data.data.access_token, result.data.data.refresh_token)
  await loadMe(); return me.value
}
async function logout() { if (refreshToken.value) { try { await authApi.logout(refreshToken.value) } catch {} }; clear() }
export function useAuth() { return { accessToken, refreshToken, me, loading, initialized, loggedIn: computed(() => Boolean(accessToken.value)), setTokens, clear, loadMe, login, logout } }
