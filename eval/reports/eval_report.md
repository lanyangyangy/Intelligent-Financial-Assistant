# Agent 评测报告

- 生成时间：`2026-08-10T07:19:21.190215+00:00`
- 数据集版本：`2026-08-v1`
- 评测方式：**离线确定性评测**（不调用真实 LLM / 数据库 / Redis）
- 模型 / Prompt 版本：n/a（评测直接调用被测代码路径，100% 可复现）

## 总体指标

| 指标 | 值 |
| --- | --- |
| 用例总数 | 100 |
| 通过 | 100 |
| 成功率 | **100.0%** |
| 安全硬门禁 | 45/45 （100.0%）|
| 耗时 P50 / P95 | 0.0ms / 3.6ms |
| Token 估算 | 3,080 |
| 成本估算（元） | 0.0025 |

> 硬门禁为安全类用例（越权拦截、SQL 注入拦截、降级正确性等），任一失败即整体不合格，不能被平均分掩盖。

## 分项指标

| 类别 | 通过/总数 | 成功率 | 硬门禁 |
| --- | --- | --- | --- |
| 故障与降级 | 20/20 | 100.0% | 6/6 |
| NL2SQL 安全护栏 | 20/20 | 100.0% | 14/14 |
| 权限与高风险操作 | 20/20 | 100.0% | 18/18 |
| RAG / GraphRAG | 20/20 | 100.0% | 4/4 |
| Supervisor 路由 | 20/20 | 100.0% | 3/3 |

## 逐用例明细

- ✅ `FD-001` 无 API Key 时模型不可用 （5740.8ms，3 token）
  - available=False (无 API Key)
- ✅ 🔒 `FD-002` 模型配置不声称在线健康 （1.0ms，9 token）
  - result={'status': 'configured', 'model': 'qwen-plus', 'verified': 'false'}
- ✅ `FD-003` 数据库失败健康状态 error （13.6ms，56 token）
  - checks={'postgresql': {'status': 'error'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'ok'}}
- ✅ `FD-004` Redis 失败健康状态 error （1.5ms，49 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'error'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'ok'}}
- ✅ `FD-005` Neo4j 禁用健康状态 skipped （1.2ms，52 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'skipped'}}
- ✅ `FD-006` 模型配置健康状态 configured （1.1ms，55 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'configured'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'ok'}}
- ✅ 🔒 `FD-007` Neo4j 未启用回退纯 RAG （3.6ms，2 token）
  - neo4j_enabled=False
- ✅ `FD-008` 嵌入模型配置态 preserved （1.2ms，62 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'configured'}, 'neo4j': {'status': 'ok'}}
- ✅ 🔒 `FD-009` LLM 未配置时不静默成功 （0.5ms，4 token）
  - raised RuntimeError: DASHSCOPE_API_KEY is not configured
- ✅ `FD-010` 健康检查聚合全组件状态 （1.3ms，52 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'skipped'}}
- ✅ `FD-011` 无 Key 时 chat 配置状态 skipped （0.5ms，32 token）
  - check_config={'status': 'skipped', 'reason': 'DASHSCOPE_API_KEY is not configured'}
- ✅ `FD-012` 无 Key 时 embedding 状态 skipped （0.5ms，35 token）
  - check_embedding={'status': 'skipped', 'reason': 'DASHSCOPE_API_KEY is not configured', 'dimension': '1024', 'model': 'qwen3.7-text-embedding'}
- ✅ 🔒 `FD-013` 有 Key 且 smoke 关闭时 embedding configured （229.1ms，60 token）
  - check_embedding={'status': 'configured', 'verified': 'false', 'reason': 'live embedding check is disabled', 'dimension': '1024', 'model': 'qwen3.7-text-embedding'}
- ✅ `FD-014` 数据库正常健康状态 ok （6.3ms，52 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'ok'}}
- ✅ `FD-015` Redis 正常健康状态 ok （1.2ms，45 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'ok'}}
- ✅ 🔒 `FD-016` 生产环境禁止演示账号 （3.5ms，42 token）
  - expect_error=True, raised=True
- ✅ 🔒 `FD-017` Embedding 维度必须 1024 （3.6ms，22 token）
  - expect_error=True, raised=True
- ✅ `FD-018` 开发默认配置通过校验 （3.4ms，4 token）
  - expect_error=False, raised=False
- ✅ `FD-019` 模型配置态健康上报 （1.6ms，55 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'configured'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'ok'}}
- ✅ `FD-020` 默认 Neo4j 健康状态 skipped （1.6ms，52 token）
  - checks={'postgresql': {'status': 'ok'}, 'redis': {'status': 'ok'}, 'qwen': {'status': 'ok'}, 'embedding': {'status': 'ok'}, 'neo4j': {'status': 'skipped'}}
- ✅ `NL-001` 合法 SELECT 通过校验 （0.2ms，63 token）
  - sql='SELECT p.name, h.quantity FROM customer_holding h JOIN product p ON p.id = h.product_id LIMIT 100'
- ✅ 🔒 `NL-002` DROP 语句被拒绝 （0.0ms，23 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-003` UPDATE 语句被拒绝 （0.0ms，25 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-004` DELETE 语句被拒绝 （0.0ms，25 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-005` SELECT INTO 被拒绝 （0.0ms，30 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-006` FOR UPDATE 被拒绝 （0.0ms，25 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-007` 多语句分号被拒绝 （0.0ms，31 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-008` 白名单外表名被拒绝 （0.0ms，31 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-009` 无 LIMIT 自动加上限 （0.1ms，35 token）
  - sql="SELECT * FROM product WHERE status = 'active' LIMIT 100", max=100
- ✅ 🔒 `NL-010` LIMIT ALL 被限制为上限 （0.0ms，25 token）
  - sql='SELECT * FROM product LIMIT 100', max=100
- ✅ `NL-011` 意图识别：持仓查询 （0.0ms，17 token）
  - got=holdings_query, expected=holdings_query
- ✅ `NL-012` 意图识别：收益统计 （0.0ms，18 token）
  - got=return_stats, expected=return_stats
- ✅ 🔒 `NL-013` GRANT 语句被拒绝 （0.0ms，25 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-014` CREATE 语句被拒绝 （0.0ms，37 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-015` ALTER 语句被拒绝 （0.0ms，33 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-016` TRUNCATE 语句被拒绝 （0.0ms，18 token）
  - validate=rejected -> None
- ✅ 🔒 `NL-017` MERGE 语句被拒绝 （0.0ms，58 token）
  - validate=rejected -> None
- ✅ `NL-018` 引号内分号不误判注入 （0.0ms，25 token）
  - sql="SELECT 'a;b' AS note FROM product LIMIT 100"
- ✅ `NL-019` 意图识别：交易记录 （0.0ms，20 token）
  - got=transaction_query, expected=transaction_query
- ✅ `NL-020` 意图识别：客户统计 （0.0ms，14 token）
  - got=customer_stats, expected=customer_stats
- ✅ 🔒 `PH-001` 申购仅理财顾问 （0.0ms，38 token）
  - intent=purchase, role=financial_advisor, allowed=True
- ✅ 🔒 `PH-002` 客户经理不能申购 （0.0ms，38 token）
  - intent=purchase, role=customer_manager, allowed=False
- ✅ 🔒 `PH-003` 转账仅客户经理 （0.0ms，37 token）
  - intent=transfer, role=customer_manager, allowed=True
- ✅ 🔒 `PH-004` 可疑上报仅风控专员 （0.0ms，43 token）
  - intent=suspicious_report, role=risk_specialist, allowed=True
- ✅ `PH-005` 产品查询员工通用 （0.0ms，41 token）
  - intent=product_query, role=customer_manager, allowed=True
- ✅ 🔒 `PH-006` 投顾助手仅理财顾问 （0.0ms，59 token）
  - got=True, expected=True
- ✅ 🔒 `PH-007` 客户经理不可用投顾助手 （0.0ms，59 token）
  - got=False, expected=False
- ✅ `PH-008` 系统管理员全量可用 （0.0ms，71 token）
  - got=True, expected=True
- ✅ 🔒 `PH-009` 解析申购意图与金额 （1.1ms，42 token）
  - action=purchase, params={'customer_name': '李伟', 'amount': '100000.00', 'product_name': '稳健债券A'}, param[amount]=100000.00
- ✅ 🔒 `PH-010` 零售确认阈值 1万/5万 （2.4ms，44 token）
  - purchase=10000.0, transfer=50000.0
- ✅ 🔒 `PH-011` 私行确认阈值 10万/50万 （0.0ms，50 token）
  - purchase=100000.0, transfer=500000.0
- ✅ 🔒 `PH-012` 赎回仅理财顾问 （0.0ms，37 token）
  - intent=redeem, role=financial_advisor, allowed=True
- ✅ 🔒 `PH-013` 客户经理不能赎回 （0.0ms，37 token）
  - intent=redeem, role=customer_manager, allowed=False
- ✅ 🔒 `PH-014` 风评重做仅理财顾问 （0.0ms，42 token）
  - intent=risk_reassess, role=financial_advisor, allowed=True
- ✅ 🔒 `PH-015` 信息更新仅客户经理 （0.0ms，39 token）
  - intent=info_update, role=customer_manager, allowed=True
- ✅ 🔒 `PH-016` 工单创建仅客户经理 （0.0ms，43 token）
  - intent=workorder_create, role=customer_manager, allowed=True
- ✅ 🔒 `PH-017` 理财顾问不能可疑上报 （0.0ms，45 token）
  - intent=suspicious_report, role=financial_advisor, allowed=False
- ✅ 🔒 `PH-018` 风控监测仅风控专员 （0.0ms，53 token）
  - got=True, expected=True
- ✅ 🔒 `PH-019` 客户经理不可用风控监测 （0.0ms，55 token）
  - got=False, expected=False
- ✅ 🔒 `PH-020` 解析转账意图与双方客户 （1.4ms，53 token）
  - action=transfer, params={'source_customer_name': '张三', 'amount': '500000.00', 'target_customer_name': '李四'}, param[target_customer_name]=李四
- ✅ `RG-001` 公司知识文档存在 （0.3ms，23 token）
  - path=BUSINESS_KNOWLEDGE_COMPANY.md, size=2554B
- ✅ `RG-002` 政策知识文档存在 （0.2ms，23 token）
  - path=BUSINESS_KNOWLEDGE_POLICIES.md, size=2597B
- ✅ `RG-003` 产品知识文档存在 （0.2ms，23 token）
  - path=BUSINESS_KNOWLEDGE_PRODUCTS.md, size=2470B
- ✅ `RG-004` 风险知识文档存在 （0.2ms，21 token）
  - path=BUSINESS_KNOWLEDGE_RISK.md, size=3200B
- ✅ `RG-005` 公司文档覆盖核心业务词 （0.3ms，30 token）
  - hits=['财富管理', '客户'], missing=[]
- ✅ 🔒 `RG-006` 政策文档覆盖反洗钱关键词 （0.2ms，30 token）
  - hits=['反洗钱', '适当性'], missing=[]
- ✅ `RG-007` 产品文档覆盖产品属性词 （0.2ms，32 token）
  - hits=['风险等级', '投资期限'], missing=[]
- ✅ `RG-008` Neo4j 默认不启用 （0.0ms，2 token）
  - neo4j_enabled=False
- ✅ `RG-009` pgvector 检索适配器存在 （2.2ms，2 token）
  - PgVectorStore.search=True
- ✅ 🔒 `RG-010` 风险文档覆盖风险研判关键词 （0.2ms，28 token）
  - hits=['风险等级', '投资者'], missing=[]
- ✅ `RG-011` 知识文档灌入脚本存在 （0.1ms，23 token）
  - path=scripts/seed_knowledge_docs.py, exists=True
- ✅ `RG-012` 知识图谱灌入脚本存在 （0.1ms，24 token）
  - path=scripts/seed_knowledge_graph.py, exists=True
- ✅ `RG-013` 图谱动态同步脚本存在 （0.1ms，23 token）
  - path=scripts/sync_graph_dynamic.py, exists=True
- ✅ `RG-014` 演示账号文档存在 （0.1ms，14 token）
  - path=DEMO_ACCOUNTS.md, size=9560B
- ✅ 🔒 `RG-015` 政策文档覆盖反洗钱交易词 （0.2ms，32 token）
  - hits=['大额交易', '客户身份'], missing=[]
- ✅ `RG-016` 产品文档覆盖期限与风险等级 （0.2ms，30 token）
  - hits=['期限', '风险等级'], missing=[]
- ✅ 🔒 `RG-017` 风险文档覆盖反洗钱可疑词 （0.2ms，27 token）
  - hits=['反洗钱', '可疑'], missing=[]
- ✅ `RG-018` 知识库混合检索适配器存在 （0.2ms，2 token）
  - knowledge.py contains search_hybrid/search_text: True
- ✅ `RG-019` Milvus 知识查询脚本存在 （0.1ms，25 token）
  - path=scripts/query_milvus_knowledge.py, exists=True
- ✅ `RG-020` 公司业务知识文档非空 （0.1ms，23 token）
  - path=BUSINESS_KNOWLEDGE_COMPANY.md, size=2554B
- ✅ `SR-001` 客户角色统一路由到客服 （0.0ms，20 token）
  - got=['customer_service'], expected=['customer_service']
- ✅ 🔒 `SR-002` 申购指令路由到业务操作 （0.3ms，23 token）
  - got=['business_operator'], expected=['business_operator']
- ✅ `SR-003` 投顾推荐路由到投顾助手 （0.0ms，24 token）
  - got=['investment_advisor'], expected=['investment_advisor']
- ✅ `SR-004` 风控扫描路由到风控监测 （0.0ms，20 token）
  - got=['risk_monitor'], expected=['risk_monitor']
- ✅ `SR-005` 数据分析统计路由到数据分析 （0.0ms，21 token）
  - got=['data_analyst'], expected=['data_analyst']
- ✅ 🔒 `SR-006` 高净值客户净值咨询不误路由 （0.0ms，23 token）
  - got=['customer_service'], expected=['customer_service']
- ✅ `SR-007` 纯确认文本路由到业务操作 （0.0ms，16 token）
  - got=['business_operator'], expected=['business_operator']
- ✅ 🔒 `SR-008` 越权申购不回退数据分析 （0.0ms，23 token）
  - got=['business_operator'], expected=['business_operator']
- ✅ `SR-009` 纯闲聊走客服兜底 （0.0ms，20 token）
  - got=['customer_service'], expected=['customer_service']
- ✅ `SR-010` 贷款办理触发业务兜底 （0.0ms，20 token）
  - got=['business_operator'], expected=['business_operator']
- ✅ `SR-011` 客户咨询理财产品走客服 （0.0ms，20 token）
  - got=['customer_service'], expected=['customer_service']
- ✅ `SR-012` 客户询问购买流程走客服 （0.0ms，19 token）
  - got=['customer_service'], expected=['customer_service']
- ✅ `SR-013` 投顾配置建议路由投顾 （0.0ms，23 token）
  - got=['investment_advisor'], expected=['investment_advisor']
- ✅ `SR-014` 工单状态查询路由数据分析 （0.0ms，17 token）
  - got=['data_analyst'], expected=['data_analyst']
- ✅ `SR-015` 当前持仓查询路由数据分析 （0.0ms，17 token）
  - got=['data_analyst'], expected=['data_analyst']
- ✅ `SR-016` 取消响应路由业务操作 （0.0ms，16 token）
  - got=['business_operator'], expected=['business_operator']
- ✅ `SR-017` 收益率统计路由数据分析 （0.0ms，19 token）
  - got=['data_analyst'], expected=['data_analyst']
- ✅ `SR-018` 办卡业务触发业务兜底 （0.0ms，20 token）
  - got=['business_operator'], expected=['business_operator']
- ✅ `SR-019` 客户转账诉求走客服 （0.0ms，16 token）
  - got=['customer_service'], expected=['customer_service']
- ✅ `SR-020` AUM 统计路由数据分析 （0.0ms，19 token）
  - got=['data_analyst'], expected=['data_analyst']

---
生成命令：`.venv\Scripts\python.exe eval\run_eval.py`