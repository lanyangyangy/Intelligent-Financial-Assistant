import { http } from './http'

export interface GraphStats {
  enabled: boolean
  total_nodes: number
  total_relations: number
  nodes: { label: string; count: number }[]
  relations: { rel_type: string; count: number }[]
}

export interface GraphNode {
  id: string
  label: string
  name?: string
  risk_level?: string
  risk?: string
}

export interface GraphEdge {
  source: string
  target: string
  relation: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export const graphApi = {
  stats: () => http.get<{ data: GraphStats }>('/graph/stats'),
  customers: () => http.get<{ data: { name: string; customer_id: string | null }[] }>('/graph/customers'),
  visualization: (customerId: string) =>
    http.get<{ data: GraphData }>(`/graph/visualization/${encodeURIComponent(customerId)}`),
  productIndustry: (productName: string) =>
    http.get<{ data: { industry: string }[] }>(`/graph/products/${encodeURIComponent(productName)}/industry`),
}
