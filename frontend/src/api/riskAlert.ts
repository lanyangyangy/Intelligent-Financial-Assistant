import { riskHttp } from './risk'

export interface RiskAlert {
  id: string
  customer_id: string
  alert_level: string
  alert_color: string
  alert_type: string
  trigger_rules: string[]
  confidence: number
  status: string
  handle_note: string
  created_at: string | null
}

export interface RiskAlertDetail extends RiskAlert {
  alert_id?: string
  trigger_detail: string
  transaction_ids: string[]
  handler_id: string | null
  handled_at: string | null
  workorder_no: string | null
}

export interface WorkOrder {
  id: string
  workorder_no: string
  customer_id: string | null
  workorder_type: string
  priority: string
  status: string
  title: string
  description: string
  source_type: string
  created_at: string | null
}

export interface RiskMonitorPayload {
  customer_id: string | number
  transaction_id: string
  amount: number
  transaction_type: string
  timestamp?: string
}

export interface RiskMonitorResult {
  alert_id?: string
  customer_id: number | string
  transaction_id: string
  rule_hits: string[]
  alert_level: string | null
  alert_color?: string
  confidence?: number
  workorder_no?: string | null
  message?: string
}

export const riskAlertApi = {
  alerts: (limit = 50) => riskHttp.get<{ data: RiskAlert[] }>(`/risk/alerts?limit=${limit}`),
  alertDetail: (id: string) => riskHttp.get<{ data: RiskAlertDetail }>(`/risk/alert/${id}`),
  handleAlert: (id: string, note = '') =>
    riskHttp.put<{ data: { id: string; status: string } }>(`/risk/alerts/${id}/handle`, { note }),
  workOrders: (limit = 50) => riskHttp.get<{ data: WorkOrder[] }>(`/risk/work-orders?limit=${limit}`),
  monitor: (payload: RiskMonitorPayload) =>
    riskHttp.post<{ data: RiskMonitorResult }>('/risk/monitor', payload),
}
