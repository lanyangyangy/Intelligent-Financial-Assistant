# pgvector + embedding + Ragas 评测报告

- 生成时间：`2026-08-27T14:26:23.547755+00:00`
- 数据集：`D:/xiangmu/Intelligent-Financial-Assistant/eval/datasets/rag_finance_generated.jsonl`
- 用例数：`150`
- TopK：`3`
- Embedding：`text-embedding-v4`
- Judge LLM：`qwen-plus`

## 检索指标

| 指标 | 当前值 |
| --- | ---: |
| Top3 候选命中率 | 100.0% |
| Context Term Recall | 100.0% |
| 端到端检索+生成 P50 / P95 | 1589.479ms / 3070.469ms |

## Ragas 指标

| 指标 | 当前值 |
| --- | ---: |
| context_precision | 82.6% |
| context_recall | 100.0% |
| faithfulness | 89.0% |
| answer_relevancy | 89.4% |

## 样例

- `product-risk-f444354fdfb8` 恒信短债增强001号的风险等级是多少？ -> Top3=命中，term_recall=100.0%
- `faq-fca48b33b6d8` 恒信短债增强001号起投金额是多少? -> Top3=命中，term_recall=100.0%
- `policy-cddcf41917b7` SUIT-001：风险等级匹配的核心要求是什么？ -> Top3=命中，term_recall=100.0%
- `customer-CUST-2026-00001` CUST-2026-00001的风险等级、金融资产和最近事件是什么？ -> Top3=命中，term_recall=100.0%
- `risk-8779acf7ca92` AML-RW-001规则的风险等级和处置动作是什么？ -> Top3=命中，term_recall=100.0%
- `product-amount-4a1b1893e230` 恒信短债增强001号起投金额是多少？ -> Top3=命中，term_recall=100.0%
- `faq-d4c42367d24c` 恒信固收稳享002号赎回多久到账? -> Top3=命中，term_recall=100.0%
- `policy-b0b71d6bba18` SUIT-002：产品分级的核心要求是什么？ -> Top3=命中，term_recall=100.0%