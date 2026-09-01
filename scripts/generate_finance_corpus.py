from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_ROOT = ROOT / "金融"


@dataclass(frozen=True)
class GeneratedDocument:
    path: Path
    content: str
    record_count: int


RISK_LABELS = {
    "R1": "低风险",
    "R2": "中低风险",
    "R3": "中风险",
    "R4": "中高风险",
    "R5": "高风险",
}

CUSTOMER_LABELS = {
    "C1": "保守型",
    "C2": "稳健型",
    "C3": "平衡型",
    "C4": "进取型",
    "C5": "激进型",
}

PRODUCT_TYPES = [
    ("现金管理", "R1", 1_000, "T+0/T+1", "货币市场工具、同业存单、短久期高等级债券"),
    ("短债增强", "R2", 10_000, "T+1", "国债、政策性金融债、AAA 信用债、短久期利率债"),
    ("固收稳享", "R2", 10_000, "每月开放", "中短久期债券、银行存款、少量可转债增强"),
    ("固收加", "R3", 50_000, "每季度开放", "债券、可转债、指数增强、量化对冲策略"),
    ("均衡配置", "R3", 50_000, "每半年开放", "债券、权益基金、黄金 ETF、现金管理工具"),
    ("权益精选", "R4", 100_000, "每周开放", "主动权益基金、指数增强、行业主题基金"),
    ("QDII 全球配置", "R4", 100_000, "T+7/T+10", "海外债券、全球股票 ETF、美元货币基金"),
    ("私行专享", "R5", 1_000_000, "封闭 12-24 个月", "私募证券基金、CTA、量化中性、另类资产"),
]

INDUSTRIES = ["消费", "医药", "新能源", "先进制造", "金融地产", "科技", "公用事业", "全球配置"]
CHANNELS = ["APP", "官网", "线下财富中心", "企业网银", "私行专属"]
CITIES = ["上海", "深圳", "杭州", "成都", "南京", "苏州", "武汉", "广州", "北京", "重庆"]
NAMES = ["李明", "王芳", "陈杰", "赵敏", "刘洋", "周颖", "黄磊", "吴静", "郑凯", "孙悦"]


def _money(value: int) -> str:
    return f"{value:,} 元"


def _product(index: int) -> dict:
    ptype, risk, minimum, liquidity, scope = PRODUCT_TYPES[index % len(PRODUCT_TYPES)]
    industry = INDUSTRIES[index % len(INDUSTRIES)]
    code = f"WM-{ptype[:2].encode('unicode_escape').hex()[-4:].upper()}-{2026}{index:04d}"
    name = f"恒信{ptype}{index:03d}号"
    duration = [7, 30, 60, 90, 180, 270, 365, 540][index % 8]
    base_low = 1.65 + (index % 9) * 0.18
    base_high = base_low + 0.45 + (index % 4) * 0.08
    return {
        "code": code,
        "name": name,
        "type": ptype,
        "risk": risk,
        "risk_label": RISK_LABELS[risk],
        "minimum": minimum + (index % 5) * minimum,
        "duration": duration,
        "liquidity": liquidity,
        "scope": scope,
        "industry": industry,
        "benchmark": f"{base_low:.2f}%-{base_high:.2f}%/年",
        "manager_fee": f"{0.12 + (index % 6) * 0.05:.2f}%/年",
        "custody_fee": f"{0.03 + (index % 4) * 0.02:.2f}%/年",
        "sales_fee": f"{0.00 if risk == 'R1' else 0.05 + (index % 3) * 0.03:.2f}%/年",
        "channel": CHANNELS[index % len(CHANNELS)],
        "effective": f"2026-{(index % 12) + 1:02d}-01",
    }


def _md_header(title: str, record_count: int, description: str) -> list[str]:
    return [
        f"# {title}",
        "",
        f"> 数据集版本：SIM-2026-08 | 记录数：{record_count} | 数据性质：仿真业务数据，仅用于 RAG、投顾推荐、风控规则和面试演示。",
        f"> {description}",
        "",
        "---",
        "",
    ]


def _product_block(index: int, heading_prefix: str = "产品条目") -> str:
    p = _product(index)
    suitability = {
        "R1": "C1 及以上客户，可作为现金管理底仓",
        "R2": "C2 及以上客户，适合稳健收益和短中期配置",
        "R3": "C3 及以上客户，需确认净值波动承受能力",
        "R4": "C4 及以上客户，需充分揭示权益或海外市场波动",
        "R5": "C5 客户或合格投资者，必须进行人工复核和双录",
    }[p["risk"]]
    return "\n".join(
        [
            f"### {heading_prefix} {index:03d}：{p['name']}",
            "",
            "| 项目 | 详情 |",
            "|------|------|",
            f"| 产品代码 | {p['code']} |",
            f"| 产品类型 | {p['type']} |",
            f"| 风险等级 | {p['risk']}（{p['risk_label']}） |",
            f"| 起投金额 | {_money(p['minimum'])} |",
            f"| 投资期限 | {p['duration']} 天 |",
            f"| 开放/赎回 | {p['liquidity']} |",
            f"| 业绩比较基准 | {p['benchmark']}，不构成收益承诺 |",
            f"| 投资范围 | {p['scope']} |",
            f"| 行业主题 | {p['industry']} |",
            f"| 管理费 | {p['manager_fee']} |",
            f"| 托管费 | {p['custody_fee']} |",
            f"| 销售服务费 | {p['sales_fee']} |",
            f"| 销售渠道 | {p['channel']} |",
            f"| 生效日期 | {p['effective']} |",
            "",
            f"**适当性要求**：{suitability}。推荐前必须核验客户风险测评有效期、资产门槛、持仓集中度和最新风险预警。",
            "",
            f"**推荐理由模板**：若客户目标为稳健增值，可说明该产品投资于{p['scope']}，风险等级为 {p['risk']}，与客户风险等级匹配后方可展示；不得使用“保本”“稳赚”“无风险”等表述。",
            "",
        ]
    )


def _personal_products(count: int) -> GeneratedDocument:
    lines = _md_header(
        "恒信财富个人理财产品手册",
        count,
        "保持产品说明书表格格式，覆盖现金管理、固收、固收加、权益、QDII 和私行产品。",
    )
    lines += [
        "## 一、产品目录",
        "",
        "本手册用于智能投顾、智能客服和适当性筛选链路。所有产品均为仿真产品，字段与真实投顾知识库保持一致。",
        "",
    ]
    for i in range(1, count + 1):
        lines.append(_product_block(i))
    return GeneratedDocument(CORPUS_ROOT / "公司业务" / "个人理财产品手册.md", "\n".join(lines), count)


def _enterprise_services(count: int) -> GeneratedDocument:
    lines = _md_header(
        "恒信财富企业金融服务方案",
        count,
        "保持方案条目格式，覆盖现金管理、薪酬代发、企业年金、票据、供应链和外汇避险场景。",
    )
    service_types = ["企业现金管理", "薪酬代发", "企业年金顾问", "供应链票据", "跨境结算", "机构理财", "员工持股计划"]
    for i in range(1, count + 1):
        service = service_types[i % len(service_types)]
        p = _product(i + 200)
        lines += [
            f"### 方案 {i:03d}：{service}服务包",
            "",
            "| 字段 | 内容 |",
            "|------|------|",
            f"| 方案编号 | ENT-2026-{i:04d} |",
            f"| 适用企业 | 年营收 {5 + i % 80}00 万元以上，近 12 个月无重大合规处罚 |",
            f"| 推荐产品 | {p['name']}（{p['code']}） |",
            "| 资金属性 | 工资、备用金、结算沉淀资金或阶段性闲置资金 |",
            f"| 风险等级 | {p['risk']}（{p['risk_label']}） |",
            f"| 最低留存金额 | {_money(100_000 + (i % 12) * 50_000)} |",
            f"| 服务城市 | {CITIES[i % len(CITIES)]}财富中心 |",
            f"| 审批 SLA | T+{1 + i % 3} 个工作日完成企业尽调与额度确认 |",
            "",
            f"**服务说明**：企业客户需提交营业执照、受益所有人识别材料、授权经办人信息和资金来源说明。系统根据企业规模、资金用途和风险等级匹配 {service} 策略。",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "公司业务" / "企业金融服务方案.md", "\n".join(lines), count)


def _hnw_standards(count: int) -> GeneratedDocument:
    lines = _md_header(
        "高净值客户服务规范",
        count,
        "保持服务规范条目格式，覆盖客户分层、私行准入、双录、人工复核和定期回访。",
    )
    scenes = ["私行产品准入", "资产配置复盘", "家族信托咨询", "跨境资产说明", "大额申赎复核", "风险预警回访"]
    for i in range(1, count + 1):
        scene = scenes[i % len(scenes)]
        lines += [
            f"### 服务规范 {i:03d}：{scene}",
            "",
            "| 项目 | 要求 |",
            "|------|------|",
            f"| 客户层级 | {'私行' if i % 3 == 0 else '钻石' if i % 3 == 1 else '白金'} |",
            f"| 金融资产门槛 | {_money(3_000_000 + (i % 20) * 500_000)} |",
            f"| 风险等级要求 | C{3 + i % 3} 及以上，R5 产品仅限 C5 或合格投资者 |",
            f"| 服务频率 | 每 {30 + (i % 6) * 15} 天一次组合复盘 |",
            "| 留痕要求 | CRM 服务记录、产品说明书确认、风险揭示书、必要时双录 |",
            "| 升级条件 | 近 30 天出现高风险预警、产品越级、客户投诉或集中赎回 |",
            "",
            f"**执行口径**：顾问在 {scene} 场景下必须先核验客户资产证明、风险测评有效期和历史持仓集中度，推荐理由需引用产品代码、风险等级和适当性依据。",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "公司业务" / "高净值客户服务规范.md", "\n".join(lines), count)


def _company_info(count: int) -> GeneratedDocument:
    lines = _md_header(
        "恒信财富企业信息",
        count,
        "保持企业信息条目格式，覆盖机构资质、网点、系统、客服、审计和数据治理。",
    )
    topics = ["机构资质", "服务网点", "客户服务", "数据治理", "合规审计", "系统安全", "产品管理", "投顾流程"]
    for i in range(1, count + 1):
        topic = topics[i % len(topics)]
        lines += [
            f"### 企业信息 {i:03d}：{topic}",
            "",
            f"- 信息编号：INFO-2026-{i:04d}",
            f"- 责任部门：{['零售金融部', '合规风控部', '数据智能部', '客户运营部'][i % 4]}",
            f"- 适用渠道：{CHANNELS[i % len(CHANNELS)]}",
            f"- 生效日期：2026-{(i % 12) + 1:02d}-15",
            f"- 核心说明：恒信财富在{CITIES[i % len(CITIES)]}等核心城市提供持牌财富管理服务，{topic}信息应以后台配置和正式公告为准。",
            f"- RAG 使用提示：回答涉及{topic}时必须引用本条信息编号，不得虚构牌照、收益承诺或网点地址。",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "公司信息" / "企业信息.md", "\n".join(lines), count)


def _onboarding(count: int) -> GeneratedDocument:
    lines = _md_header(
        "公司新人指南",
        count,
        "保持 SOP 条目格式，覆盖投顾作业、知识库查询、客户画像、订单确认和风险上报。",
    )
    actions = ["客户首次接待", "产品资料查询", "风险测评解释", "推荐理由撰写", "申购前核验", "赎回咨询", "投诉工单创建", "可疑行为上报"]
    for i in range(1, count + 1):
        action = actions[i % len(actions)]
        lines += [
            f"### SOP {i:03d}：{action}",
            "",
            "1. 登录员工后台并确认本人角色权限。",
            "2. 在客户画像页核验客户风险等级、测评日期、资产规模和最近风险标签。",
            f"3. 在知识库中检索“{action}”相关产品说明、制度条款或 FAQ。",
            "4. 若涉及推荐，必须执行适当性筛选；若涉及交易，必须完成二次确认和审计留痕。",
            f"5. 记录服务结论：SOP-2026-{i:04d}，并将客户反馈写入会话归档。",
            "",
            "**常见错误**：跳过风险测评有效期、直接承诺收益、未引用产品说明书、将客户临时表达覆盖高优先级 KYC 信息。",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "公司信息" / "公司新人指南.md", "\n".join(lines), count)


def _faq(count: int) -> GeneratedDocument:
    templates = [
        ("{name}的风险等级是多少?", "{name}的风险等级为{risk}（{risk_label}），推荐前需确认客户风险等级不低于该产品风险等级。"),
        ("{name}起投金额是多少?", "{name}起投金额为{minimum}，追加申购以产品说明书为准。"),
        ("{name}赎回多久到账?", "{name}开放和赎回规则为{liquidity}，遇周末或法定节假日顺延。"),
        ("C2客户能买{risk}产品吗?", "C2客户通常只能购买R1-R2产品；若产品为{risk}，需按适当性规则阻断或人工复核。"),
        ("产品{code}可以在哪个渠道购买?", "产品{code}当前销售渠道为{channel}，销售前需核验渠道权限和产品状态。"),
        ("业绩比较基准是否等于预期收益?", "不等于。业绩比较基准仅为投资管理参考，不构成收益承诺，回答时必须提示市场波动风险。"),
        ("风险测评过期还能推荐产品吗?", "不能直接推荐高于现金管理类的产品。需客户重新完成风险测评后再进行适当性匹配。"),
        ("客户出现高风险预警后还能推荐吗?", "高风险预警客户应阻断高风险产品推荐，中风险客户应降档推荐或转人工复核。"),
    ]
    lines = []
    for i in range(1, count + 1):
        p = _product(i)
        formatted = {**p, "minimum": _money(p["minimum"])}
        q, a = templates[i % len(templates)]
        lines.append(
            q.format(**formatted)
            + "\t"
            + a.format(**formatted)
        )
    return GeneratedDocument(CORPUS_ROOT / "公司信息" / "高频问答对.txt", "\n".join(lines) + "\n", count)


def _customer_doc(file_name: str, start: int, count: int, title: str) -> GeneratedDocument:
    lines = _md_header(title, count, "保持客户样本卡片格式，覆盖画像、持仓、风险测评、会话偏好和推荐约束。")
    for offset in range(count):
        i = start + offset
        risk = f"C{1 + i % 5}"
        p = _product(i)
        lines += [
            f"### 客户样本 {i:04d}：{NAMES[i % len(NAMES)]}",
            "",
            "| 字段 | 值 |",
            "|------|----|",
            f"| 客户编号 | CUST-2026-{i:05d} |",
            f"| 年龄 | {24 + i % 48} |",
            f"| 城市 | {CITIES[i % len(CITIES)]} |",
            f"| 客户层级 | {['普通', '金卡', '白金', '钻石', '私行'][i % 5]} |",
            f"| 风险等级 | {risk}（{CUSTOMER_LABELS[risk]}） |",
            f"| 金融资产 | {_money(80_000 + (i % 60) * 90_000)} |",
            f"| 投资期限偏好 | {['1个月以内', '3-6个月', '6-12个月', '1-3年', '3年以上'][i % 5]} |",
            f"| 当前持仓 | {p['name']}（{p['code']}），市值 {_money(20_000 + i * 1_000)} |",
            f"| 会话偏好 | 关注{['流动性', '低波动', '稳健收益', '长期增值', '海外配置'][i % 5]}，不接受无来源推荐 |",
            "| 推荐约束 | 不得推荐高于客户风险等级的产品；风险预警存在时需降档或转人工复核 |",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "用户测试数据" / file_name, "\n".join(lines), count)


def _aml_rules(count: int) -> GeneratedDocument:
    lines = _md_header("反洗钱可疑交易识别规则", count, "保持规则条目格式，覆盖大额、高频、拆分、夜间、快进快出和异常行为。")
    scenes = ["单笔大额", "七日高频", "快进快出", "分散转入集中转出", "集中转入分散转出", "夜间异常", "整数金额规避", "频繁撤单", "身份资料异常", "可疑意图"]
    for i in range(1, count + 1):
        scene = scenes[i % len(scenes)]
        threshold = 50_000 + (i % 20) * 10_000
        lines += [
            f"### AML-RW-{i:03d}：{scene}识别规则",
            "",
            f"- 触发条件：客户在观察窗口内出现{scene}行为，单笔或累计金额达到 {_money(threshold)}，或交易频次超过 {3 + i % 12} 次。",
            f"- 观察窗口：{1 + i % 30} 个自然日，跨渠道合并统计。",
            f"- 风险等级：{['low', 'medium', 'high'][i % 3]}。",
            "- 处置动作：记录 risk_alert；中高风险生成 work_order；必要时要求客户补充资金来源说明。",
            "- Agent 联动：风控 Agent 发布 risk_alert 事件，投顾 Agent 在推荐前读取 CROSS_AGENT_RISK_ALERT 标签。",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "用户研判规则" / "反洗钱可疑交易识别规则.md", "\n".join(lines), count)


def _risk_profile_rules(count: int) -> GeneratedDocument:
    lines = _md_header("投资者风险画像研判规则", count, "保持画像规则条目格式，覆盖基础属性、投资经验、偏好、行为异常和冲突治理。")
    dimensions = ["基础属性", "投资经验", "风险偏好", "资产规模", "流动性需求", "亏损承受", "行为异常", "会话抽取", "标签冲突", "置信度校准"]
    for i in range(1, count + 1):
        dimension = dimensions[i % len(dimensions)]
        lines += [
            f"### PROFILE-RULE-{i:03d}：{dimension}评分规则",
            "",
            "| 字段 | 规则 |",
            "|------|------|",
            f"| 适用维度 | {dimension} |",
            f"| 数据来源 | {['KYC', 'QUESTIONNAIRE', 'USER_STATED', 'SYSTEM_BEHAVIOR'][i % 4]} |",
            f"| 初始置信度 | {['0.90', '0.90', '0.60', '0.20'][i % 4]} |",
            f"| 生效条件 | 最近 {30 + i % 180} 天内数据有效，且未被更高优先级来源覆盖 |",
            "| 冲突处理 | 同优先级不同值进入 NEEDS_REVIEW，高优先级来源自动覆盖低优先级来源 |",
            "| 投顾影响 | 影响产品风险等级上限、期限匹配、流动性约束和推荐解释口径 |",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "用户研判规则" / "投资者风险画像研判规则.md", "\n".join(lines), count)


def _user_info_md(count: int) -> GeneratedDocument:
    lines = _md_header("用户信息数据示例", count, "保持 Markdown 表格格式，提供批量客户画像样本。")
    lines += [
        "| 客户编号 | 姓名 | 年龄 | 城市 | 风险等级 | 金融资产 | 偏好 | 最近事件 |",
        "|----------|------|------|------|----------|----------|------|----------|",
    ]
    for i in range(1, count + 1):
        risk = f"C{1 + i % 5}"
        lines.append(
            f"| CUST-2026-{i:05d} | {NAMES[i % len(NAMES)]} | {22 + i % 55} | {CITIES[i % len(CITIES)]} | {risk} | {_money(60_000 + i * 35_000)} | {['现金管理', '固收', '固收加', '权益', '全球配置'][i % 5]} | {['无', '测评临近过期', '中风险预警', '投诉回访', '大额申购复核'][i % 5]} |"
        )
    return GeneratedDocument(CORPUS_ROOT / "用户研判规则" / "用户信息数据示例.md", "\n".join(lines), count)


def _user_info_txt(count: int) -> GeneratedDocument:
    lines = []
    for i in range(1, count + 1):
        risk = f"C{1 + i % 5}"
        lines.append(
            f"CUST-2026-{i:05d}\t姓名={NAMES[i % len(NAMES)]}\t风险等级={risk}\t资产={60_000 + i * 35_000}\t偏好={['现金管理', '固收', '固收加', '权益', '全球配置'][i % 5]}\t标签来源=SIM"
        )
    return GeneratedDocument(CORPUS_ROOT / "用户研判规则" / "用户信息数据示例.txt", "\n".join(lines) + "\n", count)


def _policy_doc(file_name: str, title: str, prefix: str, count: int) -> GeneratedDocument:
    lines = _md_header(title, count, "保持制度条款格式，覆盖适当性、反洗钱、销售管理、留痕和复核要求。")
    topics = ["客户识别", "风险等级匹配", "产品分级", "销售留痕", "双录", "信息披露", "投诉处理", "到期回访", "人工复核", "模型回答约束"]
    for i in range(1, count + 1):
        topic = topics[i % len(topics)]
        lines += [
            f"### {prefix}-{i:03d}：{topic}",
            "",
            f"第一款：涉及{topic}的投顾服务，应在推荐前核验客户身份、风险等级、测评有效期和产品销售状态。",
            "第二款：若产品风险等级高于客户风险等级，系统应阻断推荐；确需继续服务的，应转人工复核并完成风险揭示。",
            "第三款：大模型生成内容必须引用知识库来源，不得出现保本、稳赚、无风险、内部收益保证等违规表述。",
            f"第四款：本条适用于 {CHANNELS[i % len(CHANNELS)]} 渠道，自 2026-{(i % 12) + 1:02d}-01 起执行。",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / "金融政策" / file_name, "\n".join(lines), count)


def _root_md(file_name: str, title: str, count: int) -> GeneratedDocument:
    lines = _md_header(title, count, "保持项目说明条目格式，用于答辩、开发和 RAG 流程说明。")
    for i in range(1, count + 1):
        lines += [
            f"### 说明条目 {i:03d}",
            "",
            f"- 模块：{['知识库清洗', '混合检索', '投顾推荐', '适当性复核', '风险联动'][i % 5]}",
            "- 输入：客户问题、客户画像、产品编码、风险等级、知识库片段。",
            "- 输出：可追溯回答、推荐理由、风险提示、引用来源和评测指标。",
            "- 验收：命中正确依据，回答忠实于上下文，禁止无依据收益承诺。",
            "",
        ]
    return GeneratedDocument(CORPUS_ROOT / file_name, "\n".join(lines), count)


def _root_txt(file_name: str, count: int) -> GeneratedDocument:
    lines = [
        f"交付物-{i:03d}\t知识库样本、RAG评测用例、产品推荐案例、风险联动记录、合规复核结果中的第{i}项"
        for i in range(1, count + 1)
    ]
    return GeneratedDocument(CORPUS_ROOT / file_name, "\n".join(lines) + "\n", count)


def build_corpus() -> list[GeneratedDocument]:
    return [
        _root_md("开发引导.md", "智能财富助手开发引导", 20),
        _root_txt("答辩所需文本交付物清单.txt", 10),
        _root_md("答辩须知.md", "答辩须知", 10),
        _personal_products(160),
        _enterprise_services(70),
        _hnw_standards(50),
        _company_info(30),
        _onboarding(40),
        _faq(250),
        _customer_doc("客户A-高净值.md", 1, 20, "客户A组：高净值客户样本"),
        _customer_doc("客户B-普通投资者.md", 101, 20, "客户B组：普通投资者样本"),
        _aml_rules(50),
        _risk_profile_rules(50),
        _user_info_md(60),
        _user_info_txt(40),
        _policy_doc("个人投资者适当性管理指南.md", "个人投资者适当性管理指南", "SUIT", 45),
        _policy_doc("反洗钱合规操作手册.md", "反洗钱合规操作手册", "AML-POLICY", 40),
        _policy_doc("理财产品销售管理办法.md", "理财产品销售管理办法", "SALES", 35),
    ]


def write_corpus() -> None:
    documents = build_corpus()
    total = sum(document.record_count for document in documents)
    if total != 1000:
        raise RuntimeError(f"generated record count must be 1000, got {total}")
    for document in documents:
        document.path.parent.mkdir(parents=True, exist_ok=True)
        document.path.write_text(document.content, encoding="utf-8", newline="\n")
        print(f"{document.record_count:4d} {document.path.relative_to(ROOT)}")
    print(f"total_records={total}")


if __name__ == "__main__":
    write_corpus()
