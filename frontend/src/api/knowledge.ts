import { http } from './http'
export interface KnowledgeHit { id:string; content:string; title_path:string; document_id:string; version_id:string; chunk_index:number; score:number; metadata:Record<string, unknown> }
export interface SearchResponse { query:string; hits:KnowledgeHit[]; retrieval_mode:string; embedding_dimension:number }
export interface KnowledgeDocument { id:string; knowledge_base_id:string; document_key:string; file_name:string; source_path:string; file_type:string; file_size:number; content_hash:string; category:string; permission_level:string; status:string; created_at:string; updated_at:string }
export const knowledgeApi = {
  search: (query:string, top_k=5) => http.post<{data:SearchResponse}>('/knowledge/search',{query,top_k}),
  defaultBase: () => http.get('/knowledge/default'),
  list: () => http.get<{data:KnowledgeDocument[]}>('/knowledge/documents'),
  create: (payload: Partial<KnowledgeDocument>) => http.post<{data:KnowledgeDocument}>('/knowledge/documents', payload),
  delete: (id:string) => http.delete<{data:{document_id:string;status:string}}>(`/knowledge/documents/${id}`),
  ingest: (id:string) => http.post<{data:{document_id:string;status:string}}>(`/knowledge/documents/${id}/ingest`),
}
