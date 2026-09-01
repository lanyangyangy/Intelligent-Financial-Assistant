# 公司业务与产品知识总结

> 来源：`公司业务/个人理财产品手册.md`、`公司业务/高净值客户服务规范.md`、`公司业务/企业金融服务方案.md`
> 用途：产品目录、客户画像、产品适配、Agent 产品解释和工作人员后台。

## 1. 个人理财产品

产品类别：

```text
货币基金
债券基金
混合基金
股票基金
QDII 基金
银行理财
结构性存款
年金保险
增额终身寿险
```

每个产品至少需要结构化：

```text
name
product_type
risk_level
minimum_amount
term_days
liquidity
expected_return_or_benchmark
fee
subscription_rule
redemption_rule
target_customer_type
status
source_document
```

## 2. 风险等级映射

文档采用产品 R1-R5 和客户 C1-C5 两套等级：

```text
R1：低风险
R2：中低风险
R3：中风险
R4：中高风险
R5：高风险
```

常见客户匹配：

```text
C1 → R1
C2 → R1-R2
C3 → R1-R3
C4 → R1-R4
C5 → R1-R5
```

具体匹配必须以适当性规则和有效版本为准。

## 3. 高净值客户分层

按可投资资产进行服务分层：

```text
金卡：50万+
白金：200万+
钻石：600万+
私行：1000万+
```

服务可包含：

```text
专属客户经理
资产配置
家族信托
全球/跨境资产配置
法律税务协同
VIP 活动与增值服务
```

客户层级应由客观资产快照和有效规则计算，不由 LLM 直接决定。

## 4. 企业金融

企业产品与服务：

```text
企业信贷
供应链金融
企业理财
现金管理
跨境金融
外汇交易
跨境投融资
跨境资金池
```

企业客户画像需要额外字段：

```text
industry
annual_revenue
operating_cashflow
receivables
inventory
foreign_trade
subsidiaries
financing_need
settlement_currency
```

## 5. 产品推荐原则

推荐服务先使用确定性规则：

```text
客户类型匹配
风险等级满足
资产门槛满足
投资期限匹配
流动性需求匹配
产品状态 active
产品准入和销售资格有效
```

Agent 负责解释产品特点和匹配原因，不得绕过规则。

## 6. 销售流程

```text
产品准入
→ 产品风险评级
→ 信息披露
→ 客户 KYC/风险测评
→ 适当性匹配
→ 风险揭示
→ 双录/确认（如适用）
→ 申购
→ 确认/冷静期
→ 持续披露
→ 到期/赎回
```
