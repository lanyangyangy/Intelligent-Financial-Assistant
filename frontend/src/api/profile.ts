import { http } from './http'
export const profileApi = {
  summary: () => http.get('/profile/me'),
  updateProfile: (payload: any) => http.put('/profile/me', payload),
  update: (payload: any) => http.put('/profile/me', payload),
  assets: (payload: any) => http.post('/profile/me/assets', payload),
  products: () => http.get('/profile/products'),
  staffProducts: (q = '', status = '', limit = 20, offset = 0) => http.get('/profile/staff/products', { params: { q, status, limit, offset } }),
  createProduct: (payload: any) => http.post('/profile/staff/products', payload),
  updateProduct: (id: string, payload: any) => http.put(`/profile/staff/products/${id}`, payload),
  deleteProduct: (id: string) => http.delete(`/profile/staff/products/${id}`),
  restoreProduct: (id: string) => http.put(`/profile/staff/products/${id}/restore`),
  recommendations: () => http.get('/profile/me/recommendations'),
  customers: (q = '', tier = '', customer_type = '', limit = 20, offset = 0) => http.get('/profile/staff/customers', { params: { q, tier, customer_type, limit, offset } }),
  customer: (id: string) => http.get(`/profile/staff/customer/${id}`),
  enterpriseVerification: () => http.get('/profile/me/enterprise-verification'),
  riskAssessment: () => http.get('/profile/me/risk-assessment'),
  submitRiskAssessment: (payload: any) => http.post('/profile/me/risk-assessment', payload),
  submitEnterpriseVerification: (payload: any) => http.post('/profile/me/enterprise-verification', payload),
  // ---- 画像增强（移植自外部画像数据分析后端）----
  enhanced: () => http.get('/profile/me/enhanced'),
  calculate: () => http.post('/profile/me/calculate', {}),
  staffCalculate: (id: string) => http.post(`/profile/staff/customer/${id}/calculate`, {}),
  extractConversation: (payload: { conversation_text: string; customer_id?: string }) => http.post('/profile/me/conversation-profile', payload),
  staffEnhanced: (id: string) => http.get(`/profile/staff/customer/${id}/enhanced`),
  // ---- 个人画像完整功能（对应外部验收台）----
  myInfo: () => http.get('/profile/me/info'),
  history: () => http.get('/profile/me/history'),
  productSuitability: () => http.get('/profile/me/products'),
  suitabilityCheck: (payload: { product_id: string; business_type: string }) =>
    http.post('/profile/me/suitability-check', payload),
  myConflicts: () => http.get('/profile/me/conflicts'),
  staffConflicts: (customerId: string) => http.get(`/profile/staff/customer/${customerId}/conflicts`),
  tier: () => http.get('/profile/me/tier'),
  saveInfo: (payload: any) => http.put('/profile/me', payload),
  submitAsset: (payload: any) => http.post('/profile/me/assets', payload),
  // ---- 员工端：指定客户画像（staff 接口）----
  staffInfo: (id: string) => http.get(`/profile/staff/customer/${id}/info`),
}
