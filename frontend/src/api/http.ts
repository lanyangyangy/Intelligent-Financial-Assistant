import axios from 'axios'

export const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1', timeout: 120000, headers: { 'Content-Type': 'application/json' } })
let refreshing: Promise<string | null> | null = null
http.interceptors.request.use((config) => { config.headers['X-Trace-Id'] = crypto.randomUUID(); const token=sessionStorage.getItem('access_token'); if(token) config.headers.Authorization=`Bearer ${token}`; return config })
http.interceptors.response.use((response) => response, async (error) => {
  const original=error.config
  if(error.response?.status===401 && original && !original._retry && sessionStorage.getItem('refresh_token')) {
    original._retry=true
    if(!refreshing) refreshing=import('./auth').then(({authApi})=>authApi.refresh(sessionStorage.getItem('refresh_token')!).then(r=>{sessionStorage.setItem('access_token',r.data.data.access_token); sessionStorage.setItem('refresh_token',r.data.data.refresh_token); window.dispatchEvent(new CustomEvent('auth:changed')); return r.data.data.access_token}).catch(()=>null)).finally(()=>{refreshing=null})
    const token=await refreshing
    if(token){ original.headers.Authorization=`Bearer ${token}`; return http(original) }
  }
  const detail = error.response?.data?.detail || error.message || '请求失败'; return Promise.reject(new Error(detail))
})

