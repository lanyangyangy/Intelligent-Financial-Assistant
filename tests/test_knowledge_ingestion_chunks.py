from app.services.knowledge_ingestion import _chunks


def test_markdown_heading_stays_with_following_table() -> None:
    text = """
### 产品条目 001：恒信短债增强001号

| 项目 | 详情 |
|------|------|
| 产品代码 | WM-3361-20260001 |
| 风险等级 | R2（中低风险） |

**适当性要求**：C2 及以上客户。
"""

    chunks = _chunks(text)

    assert any("产品条目 001" in chunk and "风险等级" in chunk for chunk in chunks)
