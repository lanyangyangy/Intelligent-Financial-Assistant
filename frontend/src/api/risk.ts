import axios from 'axios'

// F2.2 风险评估模块挂在 /api/risk/*（无 /v1 前缀）。
// http 实例 baseURL 为 /api/v1，直接复用会把 /api/risk/... 拼成 /api/v1/api/risk/...，
// 因此这里创建独立的实例：baseURL=/api + /risk/... = /api/risk/...
export const riskHttp = axios.create({ baseURL: '/api', timeout: 120000, headers: { 'Content-Type': 'application/json' } })
riskHttp.interceptors.request.use((config) => {
  config.headers['X-Trace-Id'] = crypto.randomUUID()
  const token = sessionStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
riskHttp.interceptors.response.use((response) => response, async (error) => {
  const original = error.config
  if (error.response?.status === 401 && original && !original._retry && sessionStorage.getItem('refresh_token')) {
    original._retry = true
    try {
      const { authApi } = await import('./auth')
      const r = await authApi.refresh(sessionStorage.getItem('refresh_token')!)
      sessionStorage.setItem('access_token', r.data.data.access_token)
      sessionStorage.setItem('refresh_token', r.data.data.refresh_token)
      window.dispatchEvent(new CustomEvent('auth:changed'))
      original.headers.Authorization = `Bearer ${r.data.data.access_token}`
      return riskHttp(original)
    } catch { /* fall through to error */ }
  }
  const detail = error.response?.data?.error?.message || error.response?.data?.detail || error.message || '请求失败'
  return Promise.reject(new Error(detail))
})

export interface RiskOption { key: string; text: string; score: number }
export interface RiskQuestion { q: number; dimension: string; question: string; options: RiskOption[] }
export interface RiskQuestionnaire { questionnaire_id: string; version: string; total_questions: number; dimensions: string[]; items: RiskQuestion[] }
export interface RiskAssessmentResult { customer_id: string; score: number; risk_level: string; level_name: string; answered: number; expired_at: string | null }
export interface SuitabilityCheckResult { customer_id: string; product_id: string; product_name: string | null; customer_risk_level: string; product_risk_level: string; matched: boolean; warning: string | null; max_allowed_product_risk: string | null }

export const riskApi = {
  questionnaire: () => riskHttp.get<{ data: RiskQuestionnaire }>('/risk/questionnaire'),
  submitAssessment: (customer_id: string, answers: { q: number; a: string }[]) =>
    riskHttp.post<{ data: RiskAssessmentResult }>('/risk/assessment', { customer_id, answers }),
  suitabilityCheck: (customer_id: string, product_id: string) =>
    riskHttp.post<{ data: SuitabilityCheckResult }>('/risk/suitability-check', { customer_id, product_id }),
}
