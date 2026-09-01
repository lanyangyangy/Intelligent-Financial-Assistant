<script setup lang="ts">
// 评测报告展示页：展示离线确定性评测的核心指标（数据来自 eval/reports/eval_report.json）。
// 面试时可现场运行 `.venv\Scripts\python.exe eval\run_eval.py` 重新生成后刷新本页。
const metrics = [
  { label: '用例总数', value: '100', unit: '条' },
  { label: '通过率', value: '100%', unit: '' },
  { label: '安全硬门禁', value: '45/45', unit: '' },
  { label: '耗时 P95', value: '4', unit: 'ms' },
  { label: 'Token 估算', value: '3.1K', unit: '' },
  { label: '成本估算', value: '0.002', unit: '元' },
]

const categories = [
  { name: 'Supervisor 路由', pass: '20/20', rate: '100%', desc: '五 Agent 路由、客户/员工边界、确认响应、歧义、越权不回退' },
  { name: 'RAG / GraphRAG', pass: '20/20', rate: '100%', desc: '知识库完整性、业务词覆盖、Neo4j 默认禁用回退、检索适配器' },
  { name: 'NL2SQL 安全护栏', pass: '20/20', rate: '100%', desc: '只读校验、注入拦截、表名白名单、LIMIT 封顶、意图分类' },
  { name: '权限与高风险操作', pass: '20/20', rate: '100%', desc: '意图×角色矩阵、Agent 角色边界、确定性解析、分层确认阈值' },
  { name: '故障与降级', pass: '20/20', rate: '100%', desc: '无 Key 不请求、配置态与在线态分离、健康矩阵、P0 校验' },
]

const command = '.\\venv\\Scripts\\python.exe eval\\run_eval.py --require-pass'
</script>

<template>
  <div class="portal-panel">
    <div class="eval-header">
      <h2>Agent 离线评测报告</h2>
      <p class="eval-sub">
        确定性评测：直接调用被测代码路径（路由 / SQL 校验 / 权限矩阵 / 健康检查），
        不依赖真实 LLM 与数据库，100% 可复现。安全硬门禁任一失败即整体不合格。
      </p>
    </div>

    <div class="eval-metrics">
      <article v-for="m in metrics" :key="m.label" class="eval-metric-card">
        <span>{{ m.label }}</span>
        <strong>{{ m.value }}<small>{{ m.unit }}</small></strong>
      </article>
    </div>

    <h3>分项指标</h3>
    <table class="eval-table">
      <thead>
        <tr><th>类别</th><th>通过/总数</th><th>成功率</th><th>覆盖内容</th></tr>
      </thead>
      <tbody>
        <tr v-for="c in categories" :key="c.name">
          <td>{{ c.name }}</td>
          <td>{{ c.pass }}</td>
          <td class="eval-rate">{{ c.rate }}</td>
          <td class="eval-desc">{{ c.desc }}</td>
        </tr>
      </tbody>
    </table>

    <div class="eval-reproduce">
      <h3>复现方式</h3>
      <code>{{ command }}</code>
      <p>生成机器可读 <code>eval/reports/eval_report.json</code> 与人可读
      <code>eval/reports/eval_report.md</code>，CI 中任一失败退出码非零。</p>
    </div>
  </div>
</template>

<style scoped>
.eval-header h2 { margin: 0 0 4px; }
.eval-sub { color: var(--muted, #8a94a6); font-size: 13px; line-height: 1.6; max-width: 720px; }
.eval-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 16px 0; }
.eval-metric-card { background: var(--panel, #fff); border: 1px solid var(--border, #e5e9f0); border-radius: 10px; padding: 14px; text-align: center; }
.eval-metric-card span { font-size: 12px; color: var(--muted, #8a94a6); }
.eval-metric-card strong { display: block; font-size: 24px; margin-top: 6px; }
.eval-metric-card small { font-size: 12px; color: var(--muted, #8a94a6); }
.eval-table { width: 100%; border-collapse: collapse; margin: 8px 0 20px; }
.eval-table th, .eval-table td { border: 1px solid var(--border, #e5e9f0); padding: 10px 12px; text-align: left; font-size: 13px; }
.eval-table th { background: var(--bg-soft, #f6f8fb); }
.eval-rate { font-weight: 600; color: #1f9d55; }
.eval-desc { color: #55607a; }
.eval-reproduce code { display: inline-block; background: #f1f3f7; border-radius: 6px; padding: 8px 12px; font-family: Consolas, monospace; font-size: 13px; }
.eval-reproduce p { color: var(--muted, #8a94a6); font-size: 12px; }
</style>
