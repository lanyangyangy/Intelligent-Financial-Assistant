# 金融 RAG 专项评测报告

- 生成时间：`2026-08-27T04:05:34.278759+00:00`
- 语料记录数：`1000`
- 评测用例数：`150`
- 评测模式：`offline_lexical_rag_proxy`
- TopK：`3`

## 总体指标

| 指标 | 当前值 |
| --- | ---: |
| Top3 候选命中率 | 97.3% |
| ContextPrecision | 99.3% |
| ContextRecall | 99.3% |
| Faithfulness Proxy | 100.0% |
| Answer Relevancy Proxy | 99.3% |
| 检索耗时 P50 / P95 | 179.799ms / 258.402ms |

## 分类指标

| 类别 | Top3 | Precision | Recall | Faithfulness | Relevancy |
| --- | ---: | ---: | ---: | ---: | ---: |
| customer | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| faq | 90.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| policy | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| product | 96.7% | 96.7% | 96.7% | 100.0% | 96.7% |
| risk_rule | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

> 注：当前报告为离线词法检索基线，用来快速验证金融知识库覆盖度和检索命中情况；
> Faithfulness / Answer Relevancy 为抽取式 proxy，不等同于接入真实 LLM 与 Ragas 后的线上指标。

## 样例

- `product-risk-f444354fdfb8` 恒信短债增强001号的风险等级是多少？ -> Top3=命中，Recall=100.0%
- `faq-fca48b33b6d8` 恒信短债增强001号起投金额是多少? -> Top3=命中，Recall=100.0%
- `policy-cddcf41917b7` SUIT-001：风险等级匹配的核心要求是什么？ -> Top3=命中，Recall=100.0%
- `customer-CUST-2026-00001` CUST-2026-00001的风险等级、金融资产和最近事件是什么？ -> Top3=命中，Recall=100.0%
- `risk-8779acf7ca92` AML-RW-001规则的风险等级和处置动作是什么？ -> Top3=命中，Recall=100.0%
- `product-amount-4a1b1893e230` 恒信短债增强001号起投金额是多少？ -> Top3=命中，Recall=100.0%
- `faq-d4c42367d24c` 恒信固收稳享002号赎回多久到账? -> Top3=命中，Recall=100.0%
- `policy-b0b71d6bba18` SUIT-002：产品分级的核心要求是什么？ -> Top3=命中，Recall=100.0%
- `customer-CUST-2026-00002` CUST-2026-00002的风险等级、金融资产和最近事件是什么？ -> Top3=命中，Recall=100.0%
- `risk-c603d0358284` AML-RW-002规则的风险等级和处置动作是什么？ -> Top3=命中，Recall=100.0%