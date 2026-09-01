import { http } from './http'
export const healthApi = { check: () => http.get('/health') }
