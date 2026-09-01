<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { graphApi, type GraphData, type GraphStats } from '../api/graph'

const loading = ref(false)
const error = ref('')
const stats = ref<GraphStats | null>(null)
const graph = ref<GraphData>({ nodes: [], edges: [] })
const selected = ref<{ id: string; label: string; name?: string; risk?: string; risk_level?: string } | null>(null)

// 客户查询状态
const customerQuery = ref('')
const customerOptions = ref<{ name: string; customer_id: string | null }[]>([])
const searched = ref(false)

// 力导向简易布局：环形分布 + 客户居中
const NODE_COLORS: Record<string, string> = {
  客户: '#74d6c6', 产品: '#7a8db5', 风险等级: '#f0ad4e', 行业: '#b39ddb', 基金经理: '#e67e22',
}

// 边按关系类型着色（视觉分层：主关系实色、属性关系半透明）
const EDGE_COLORS: Record<string, string> = {
  HOLDS: '#2e7d6b',
  SUITABLE_FOR: '#f0ad4e',
  BELONGS_TO: '#b39ddb',
  MANAGED_BY: '#e67e22',
  LISTED_IN: '#90a4ae',
}

const positioned = computed(() => {
  const nodes = graph.value.nodes
  if (!nodes.length) return []
  const cx = 300, cy = 220
  const cust = nodes.find((n) => n.label === '客户')
  const products = nodes.filter((n) => n.label === '产品')
  const attrs = nodes.filter((n) => n.label !== '客户' && n.label !== '产品')

  const pos: Record<string, { x: number; y: number }> = {}
  if (cust) pos[cust.id] = { x: cx, y: cy }

  // 产品环：以客户为中心均分角度（从 -90° 起始，避免对称重叠）
  const prodRadius = 118
  products.forEach((p, i) => {
    const angle = (i / Math.max(1, products.length)) * 2 * Math.PI - Math.PI / 2
    pos[p.id] = {
      x: cx + prodRadius * Math.cos(angle),
      y: cy + prodRadius * Math.sin(angle) * 0.82,
    }
  })

  // 属性节点：贴各自所属产品放在外圈（形成放射簇，避免同类节点挤一起）
  // 先按边建立 产品 → 属性子节点 的映射
  const attrByProduct: Record<string, typeof attrs> = {}
  for (const e of graph.value.edges) {
    if (!attrByProduct[e.source]) attrByProduct[e.source] = []
    const target = nodes.find((n) => n.id === e.target)
    if (target && target.label !== '客户' && attrByProduct[e.source].indexOf(target) < 0) {
      attrByProduct[e.source].push(target)
    }
  }
  const attrRadius = 208
  const placedAttr = new Set<string>()
  for (const p of products) {
    const children = attrByProduct[p.id] || []
    const pPos = pos[p.id]
    const baseAngle = Math.atan2(pPos.y - cy, pPos.x - cx)
    children.forEach((a, j) => {
      if (placedAttr.has(a.id)) return // 共享属性（如同一行业）只放一次
      placedAttr.add(a.id)
      // 每个属性在小角度扇形内错开
      const spread = (children.length > 1 ? (j - (children.length - 1) / 2) * 0.26 : 0)
      const angle = baseAngle + spread
      pos[a.id] = {
        x: cx + attrRadius * Math.cos(angle),
        y: cy + attrRadius * Math.sin(angle) * 0.82,
      }
    })
  }
  // 兜底：孤立属性节点（无产品边）均匀放外圈
  attrs.filter((a) => !placedAttr.has(a.id)).forEach((a, i) => {
    const angle = (i / Math.max(1, attrs.length)) * 2 * Math.PI - Math.PI / 2
    pos[a.id] = {
      x: cx + attrRadius * Math.cos(angle),
      y: cy + attrRadius * Math.sin(angle) * 0.82,
    }
  })

  return nodes.map((n) => ({
    ...n,
    x: pos[n.id]?.x ?? cx,
    y: pos[n.id]?.y ?? cy,
    color: NODE_COLORS[n.label] || '#90a4ae',
  }))
})

const edges = computed(() => graph.value.edges)

function edgeColor(relation: string) {
  return EDGE_COLORS[relation] || '#b0bec5'
}

function edgeDash(relation: string) {
  // 客户→产品主关系实线；属性关系虚线
  return relation === 'HOLDS' ? '' : '4 3'
}
function errorMessage(e: any) { return e.response?.data?.detail || e.message || '图谱请求失败' }

async function loadStats() {
  try {
    const r = await graphApi.stats()
    stats.value = r.data.data
  } catch (e: any) { error.value = errorMessage(e) }
}

async function loadCustomers() {
  try {
    const r = await graphApi.customers()
    customerOptions.value = r.data.data || []
  } catch (e: any) {
    // 客户名单加载失败不阻断页面：仍可手动输入客户姓名查询
  }
}

async function queryCustomer(name?: string) {
  const rawKeyword = name ?? customerQuery.value
  const keyword = typeof rawKeyword === 'string' ? rawKeyword.trim() : ''
  if (!keyword) return
  loading.value = true
  error.value = ''
  searched.value = true
  graph.value = { nodes: [], edges: [] }
  selected.value = null
  try {
    const r = await graphApi.visualization(keyword)
    graph.value = r.data.data || { nodes: [], edges: [] }
  } catch (e: any) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

function onNodeClick(n: any) {
  selected.value = n
}

function nodeLabel(n: any) {
  if (n.label === '客户') return n.name || n.id
  return n.name || n.id
}

function nodeById(id: string) {
  return positioned.value.find((n: any) => n.id === id)
}

onMounted(async () => {
  await Promise.all([loadStats(), loadCustomers()])
})
</script>

<template>
  <section class="table-module">
    <div class="module-header">
      <div><span class="module-kicker">KNOWLEDGE GRAPH</span><h2>知识图谱（Neo4j）</h2>
        <p>客户-产品-行业-风险等级 多跳关联可视化</p></div>
      <span class="graph-access-hint">仅内部员工可见</span>
    </div>
    <p v-if="error" class="error">{{ error }}</p>

    <div v-if="stats" class="profile-stat-grid">
      <article><span>图谱状态</span><strong :style="{ color: stats.enabled ? '#2e7d32' : '#c62828' }">{{ stats.enabled ? '已连接' : '不可用' }}</strong></article>
      <article><span>节点总数</span><strong>{{ stats.total_nodes }}</strong></article>
      <article><span>关系总数</span><strong>{{ stats.total_relations }}</strong></article>
      <article v-for="n in stats.nodes.slice(0, 3)" :key="n.label"><span>{{ n.label }}</span><strong>{{ n.count }}</strong></article>
    </div>

    <!-- 客户图谱查询区 -->
    <div class="graph-query">
      <select v-model="customerQuery" class="graph-input" @change="queryCustomer()">
        <option value="">选择客户（图谱中的客户姓名）</option>
        <option v-for="c in customerOptions" :key="c.name" :value="c.customer_id ?? c.name">{{ c.name }}</option>
      </select>
      <input v-model="customerQuery" class="graph-input" placeholder="或输入客户姓名/ID 查询，如：冯雪" @keyup.enter="queryCustomer()" />
      <button class="graph-btn" :disabled="loading" @click="queryCustomer()">{{ loading ? '查询中…' : '查询客户图谱' }}</button>
    </div>

    <div v-if="loading" class="empty">正在加载图谱…</div>
    <div v-else-if="positioned.length" class="graph-wrap">
      <svg :viewBox="'0 0 600 440'" class="graph-svg">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#b0bec5" />
          </marker>
        </defs>
        <!-- 边 -->
        <line v-for="(e, i) in edges" :key="'e' + i"
          :x1="nodeById(e.source)?.x || 300" :y1="nodeById(e.source)?.y || 220"
          :x2="nodeById(e.target)?.x || 300" :y2="nodeById(e.target)?.y || 220"
          :stroke="edgeColor(e.relation)"
          :stroke-dasharray="edgeDash(e.relation)"
          :stroke-opacity="e.relation === 'HOLDS' ? 0.9 : 0.55"
          stroke-width="1.5" marker-end="url(#arrow)" />
        <!-- 边标签：仅主关系显示，属性边标签太密则省略 -->
        <text v-for="(e, i) in edges" :key="'et' + i"
          v-show="['HOLDS','SUITABLE_FOR','BELONGS_TO','MANAGED_BY'].includes(e.relation)"
          :x="((nodeById(e.source)?.x || 300) + (nodeById(e.target)?.x || 300)) / 2"
          :y="((nodeById(e.source)?.y || 220) + (nodeById(e.target)?.y || 220)) / 2 - 4"
          font-size="8" :fill="edgeColor(e.relation)" text-anchor="middle"
          :style="{ paintOrder: 'stroke', stroke: '#fff', strokeWidth: '2px' }">{{ e.relation }}</text>
        <!-- 节点 -->
        <g v-for="n in positioned" :key="n.id" @click="onNodeClick(n)" style="cursor: pointer">
          <circle :cx="n.x" :cy="n.y" :r="n.label === '客户' ? 26 : 20" :fill="n.color" fill-opacity="0.85" stroke="#fff" stroke-width="2" />
          <text :x="n.x" :y="n.y + 4" font-size="10" fill="#fff" font-weight="700" text-anchor="middle">{{ nodeLabel(n) }}</text>
          <text :x="n.x" :y="n.y - (n.label === '客户' ? 32 : 26)" font-size="10" fill="#546e7a" text-anchor="middle">{{ n.label }}</text>
        </g>
      </svg>
      <div v-if="selected" class="graph-detail">
        <strong>节点详情：{{ selected.label }}</strong>
        <p v-if="selected.name">名称：{{ selected.name }}</p>
        <p v-if="selected.risk || selected.risk_level">风险等级：{{ selected.risk || selected.risk_level }}</p>
        <p>ID：{{ selected.id }}</p>
      </div>
    </div>
    <div v-else-if="searched" class="empty">未查询到该客户的图谱数据，请确认客户姓名是否正确。</div>
    <div v-else class="empty">选择或输入客户姓名，查看其在图谱中的产品、行业与风险等级关联。</div>
  </section>
</template>

<style scoped>
.profile-stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 16px 0; }
.profile-stat-grid article { background: #f7f9fc; border: 1px solid #e6ebf3; border-radius: 10px; padding: 12px; }
.profile-stat-grid span { display: block; color: #8a94a6; font-size: 12px; margin-bottom: 6px; }
.profile-stat-grid strong { font-size: 18px; }
.graph-query { display: flex; gap: 10px; align-items: center; margin: 14px 0; flex-wrap: wrap; }
.graph-input { padding: 8px 12px; border: 1px solid #d5dcea; border-radius: 8px; font-size: 13px; background: #fff; color: #37405a; }
.graph-input:focus { outline: none; border-color: #4a90d9; }
.graph-btn { padding: 8px 18px; border: none; border-radius: 8px; background: #2e7d6b; color: #fff; font-size: 13px; cursor: pointer; }
.graph-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.graph-btn:hover:not(:disabled) { background: #256b5c; }
.graph-wrap { background: #fafbfd; border: 1px solid #e6ebf3; border-radius: 12px; padding: 16px; }
.graph-svg { width: 100%; height: auto; background: #fff; border-radius: 10px; }
.graph-detail { margin-top: 12px; padding: 10px 14px; background: #eef7f5; border-radius: 8px; font-size: 13px; }
.graph-detail p { margin: 4px 0; color: #55617a; }
.graph-access-hint { color: #718198; font-size: 13px; }
</style>
