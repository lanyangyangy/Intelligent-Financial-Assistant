import { http } from './http'
export const tradingApi = {
  account: () => http.get('/trading/account/me'),
  orders: () => http.get('/trading/orders/me'),
  createOrder: (payload: { product_id: string; amount: number; idempotency_key?: string }) => http.post('/trading/orders', payload),
  order: (id: string) => http.get(`/trading/orders/${id}`),
  trades: () => http.get('/trading/trades/me'),
  confirm: (id: string) => http.post(`/trading/orders/${id}/confirm`),
  cancel: (id: string) => http.post(`/trading/orders/${id}/cancel`),
  pending: (limit=20,offset=0) => http.get('/trading/orders/pending',{params:{limit,offset}}),
  customerOrders: (id: string) => http.get(`/trading/orders/customer/${id}`),
  approve: (id: string, note = '') => http.post(`/trading/orders/${id}/review`, { note }),
  reject: (id: string, note = '') => http.post(`/trading/orders/${id}/reject`, { note }),
}
