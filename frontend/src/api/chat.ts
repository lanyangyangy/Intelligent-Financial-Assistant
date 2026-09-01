import { http } from './http'

export interface ChatEvidence {
  source?: string
  content?: string
  score?: number
  [key: string]: unknown
}

export interface ChatResult {
  agent: string
  summary: string
  status: string
  data: Record<string, unknown>
  evidence: ChatEvidence[]
  confidence: number
  requires_confirmation: boolean
  next_action: string | null
  session_id?: string | null
  history_turns?: number
}

export interface ChatPayload {
  message: string
  agent?: string
  confirmed?: boolean
  request_id?: string
  decision?: string
  confirmation_id?: string
  selected_customer_id?: string
  session_id?: string
  archive?: boolean
  extract_profile?: boolean
}

export interface ChatHistoryMessage {
  role: 'user' | 'assistant'
  content: string
  ts?: string
}

export const AGENT_LABELS: Record<string, string> = {
  customer_service: '智能客服',
  investment_advisor: '投顾助手',
  risk_monitor: '风控监测',
  data_analyst: '数据分析',
  business_operator: '业务操作',
}

export const chatApi = {
  send: (payload: ChatPayload) => http.post<{ data: ChatResult }>('/chat', payload),
  history: (sessionId: string) => http.get<{ data: ChatHistoryMessage[] }>(`/chat/history/${encodeURIComponent(sessionId)}`),
}

// SSE 流式对话（Phase 5 F5.3）：逐字返回打字机效果
export interface StreamMeta {
  agent?: string
  requires_confirmation?: boolean
  confirmation_id?: string
  status?: string
  data?: unknown
}

export async function chatStream(
  payload: ChatPayload,
  onDelta: (delta: string) => void,
  onMeta: (meta: StreamMeta) => void,
): Promise<string> {
  const token = sessionStorage.getItem('access_token')
  const resp = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(payload),
  })
  if (!resp.ok || !resp.body) throw new Error('stream failed')
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let full = ''
  let buffer = ''
  const consumeLine = (line: string) => {
    const trimmed = line.trim()
    if (!trimmed.startsWith('data: ')) return
    const data = trimmed.slice(6)
    if (data === '[DONE]') return
    try {
      const parsed = JSON.parse(data)
      // meta 事件：agent / 二次确认凭据 / 状态
      if (parsed.agent !== undefined || parsed.requires_confirmation !== undefined) {
        onMeta(parsed as StreamMeta)
      }
      if (parsed.delta) {
        full += parsed.delta
        onDelta(parsed.delta)
      }
    } catch { /* ignore an incomplete SSE data line */ }
  }
  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      buffer += decoder.decode()
      if (buffer) consumeLine(buffer)
      break
    }
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    lines.forEach(consumeLine)
  }
  return full
}
