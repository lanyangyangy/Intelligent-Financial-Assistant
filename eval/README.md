# Agent 离线评测

面向简历指标的确定性评测系统：**一条命令**生成机器可读 JSON 与人可读 Markdown 报告。

## 设计原则

- **完全离线**：不调用真实 LLM / 数据库 / Redis，任何环境（含 CI、无 Docker）都可运行；
- **确定性**：每个用例直接调用被测代码的真实函数路径（路由、SQL 校验、权限矩阵、健康检查、降级逻辑）；
- **可追溯**：报告记录每个用例的执行结果、耗时、Token 估算，可回溯到具体函数；
- **安全硬门禁**：越权拦截、SQL 注入拦截、降级正确性等安全用例单独统计，任一失败即整体不合格，不能被平均分掩盖。

## 数据集（当前 100 条）

| 类别 | 数量 | 覆盖内容 |
| --- | ---: | --- |
| Supervisor 路由 | 20 | 五 Agent 路由、客户/员工边界、确认响应、歧义（高净值净值咨询）、越权不回退、业务兜底 |
| RAG / GraphRAG | 20 | 知识库文档完整性、业务词覆盖、Neo4j 默认禁用、pgvector 适配器、灌入/同步脚本、混合检索适配器 |
| NL2SQL 安全护栏 | 20 | 只读 SQL 校验、危险语句拦截（含 GRANT/CREATE/ALTER/TRUNCATE/MERGE）、表名白名单、LIMIT 封顶、引号内分号、意图分类 |
| 权限与高风险操作 | 20 | 意图×角色权限矩阵（8 意图）、Agent 角色边界、确定性解析、零售/金卡/私行分层确认阈值 |
| 故障与降级 | 20 | 无 Key 不发起请求、配置态与在线态分离、健康状态矩阵、LLM 未配置明确报错、P0 配置校验 |

## 运行

```powershell
.\.venv\Scripts\python.exe eval\run_eval.py                          # 全部
.\.venv\Scripts\python.exe eval\run_eval.py --category nl2sql        # 单类别
.\.venv\Scripts\python.exe eval\run_eval.py --require-pass           # CI：任一失败退出码 1
```

输出：

- `eval/reports/eval_report.json` — 机器可读指标（成功率、P50/P95、Token、成本估算、硬门禁）
- `eval/reports/eval_report.md` — 人可读报告

## 指标口径

- 成功率：通过用例 / 总用例；
- 硬门禁成功率：安全用例单独统计（如 SQL 注入拦截、越权拦截），不被平均分掩盖；
- P50/P95：单用例执行耗时分位数（离线确定性评测，毫秒级）；
- Token / 成本：按输入字符估算（`len * 0.7` token，qwen-plus 参考价 `¥0.0008/千 token`），报告中标注为估算。

## 扩展新用例

1. 在 `eval/datasets/<类别>.jsonl` 追加一行 JSON；
2. 若需新检查逻辑，在 `eval/runners.py` 中实现 runner 并注册到 `_DISPATCH`；
3. 重跑 `eval\run_eval.py` 验证。
