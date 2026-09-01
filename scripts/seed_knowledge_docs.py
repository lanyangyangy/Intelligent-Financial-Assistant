"""Seed the knowledge base with business documents and the local finance corpus.

Usage (from repo root, using the project venv):
    .venv\\Scripts\\python.exe scripts\\seed_knowledge_docs.py

The built-in docs provide summarized business knowledge. If the ``金融/``
directory exists, Markdown and text files under it are also registered as
source knowledge documents. HTML files are skipped because the current parser
ingests plain text only.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.models.knowledge import KnowledgeDocument  # noqa: E402
from app.repositories.knowledge import KnowledgeRepository  # noqa: E402

FINANCE_CORPUS_ROOT = ROOT / "金融"

BASE_DOCUMENTS = [
    {
        "path": ROOT / "docs" / "BUSINESS_KNOWLEDGE_PRODUCTS.md",
        "document_key": "business-knowledge-products",
        "file_name": "BUSINESS_KNOWLEDGE_PRODUCTS.md",
        "category": "product",
        "file_type": "markdown",
    },
    {
        "path": ROOT / "docs" / "BUSINESS_KNOWLEDGE_POLICIES.md",
        "document_key": "business-knowledge-policies",
        "file_name": "BUSINESS_KNOWLEDGE_POLICIES.md",
        "category": "policy",
        "file_type": "markdown",
    },
    {
        "path": ROOT / "docs" / "BUSINESS_KNOWLEDGE_COMPANY.md",
        "document_key": "business-knowledge-company",
        "file_name": "BUSINESS_KNOWLEDGE_COMPANY.md",
        "category": "faq",
        "file_type": "markdown",
    },
    {
        "path": ROOT / "docs" / "BUSINESS_KNOWLEDGE_RISK.md",
        "document_key": "business-knowledge-risk",
        "file_name": "BUSINESS_KNOWLEDGE_RISK.md",
        "category": "policy",
        "file_type": "markdown",
    },
]

FINANCE_DIR_KEYS = {
    "公司业务": "company-business",
    "公司信息": "company-info",
    "用户测试数据": "user-test-data",
    "用户研判规则": "user-assessment-rules",
    "金融政策": "financial-policy",
}

FINANCE_CATEGORY_BY_DIR = {
    "公司业务": "product",
    "公司信息": "faq",
    "用户测试数据": "customer",
    "用户研判规则": "policy",
    "金融政策": "policy",
}

FINANCE_STEM_KEYS = {
    "个人理财产品手册": "personal-wealth-products",
    "企业金融服务方案": "enterprise-financial-services",
    "高净值客户服务规范": "high-net-worth-service-standards",
    "企业信息": "enterprise-info",
    "公司新人指南": "employee-onboarding-guide",
    "高频问答对": "faq-pairs",
    "客户A-高净值": "customer-a-high-net-worth",
    "客户B-普通投资者": "customer-b-regular-investor",
    "反洗钱可疑交易识别规则": "aml-suspicious-transaction-rules",
    "投资者风险画像研判规则": "investor-risk-profile-rules",
    "用户信息数据示例": "user-info-data-example",
    "答辩所需文本交付物清单": "defense-deliverables-checklist",
    "答辩须知": "defense-notes",
    "开发引导": "developer-guide",
    "个人投资者适当性管理指南": "individual-investor-suitability-guide",
    "反洗钱合规操作手册": "aml-compliance-manual",
    "理财产品销售管理办法": "wealth-product-sales-rules",
}

FILE_TYPES = {
    ".md": "markdown",
    ".txt": "plain_text",
}


def _finance_document_key(path: Path) -> str:
    relative = path.relative_to(FINANCE_CORPUS_ROOT)
    parts = list(relative.parts)
    stem_key = FINANCE_STEM_KEYS.get(path.stem, path.stem)
    if path.suffix.lower() == ".txt" and path.with_suffix(".md").exists():
        stem_key = f"{stem_key}-txt"
    if len(parts) == 1:
        return f"finance-{stem_key}"
    dir_key = FINANCE_DIR_KEYS.get(parts[0], parts[0])
    return f"finance-{dir_key}-{stem_key}"


def _finance_category(path: Path) -> str:
    relative = path.relative_to(FINANCE_CORPUS_ROOT)
    if len(relative.parts) <= 1:
        return "general"
    return FINANCE_CATEGORY_BY_DIR.get(relative.parts[0], "general")


def _finance_documents() -> list[dict]:
    if not FINANCE_CORPUS_ROOT.exists():
        return []

    documents: list[dict] = []
    for path in sorted(FINANCE_CORPUS_ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in FILE_TYPES:
            continue
        documents.append(
            {
                "path": path,
                "document_key": _finance_document_key(path),
                "file_name": path.name,
                "category": _finance_category(path),
                "file_type": FILE_TYPES[path.suffix.lower()],
            }
        )
    return documents


def build_document_specs() -> list[dict]:
    return [*BASE_DOCUMENTS, *_finance_documents()]


DOCUMENTS = build_document_specs()


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    repo = KnowledgeRepository(db)

    base = await repo.ensure_default_base()
    print(f"knowledge_base: {base.name} ({base.id})")

    for doc in build_document_specs():
        path: Path = doc["path"]
        if not path.exists():
            print(f"SKIP (missing): {path.name}")
            continue
        async with db.session_factory() as session:
            existing = (
                await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.knowledge_base_id == str(base.id),
                        KnowledgeDocument.document_key == doc["document_key"],
                        KnowledgeDocument.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
        if existing is not None:
            print(f"SKIP (exists): {doc['file_name']} (key={doc['document_key']})")
            continue
        text = path.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            document = await repo.create_document(
                knowledge_base_id=str(base.id),
                document_key=doc["document_key"],
                file_name=doc["file_name"],
                source_path=str(path),
                file_type=doc["file_type"],
                file_size=path.stat().st_size,
                content_hash=content_hash,
                category=doc["category"],
                permission_level="public",
            )
            print(f"created document: {doc['file_name']} (category={doc['category']})")
            event_id = await repo.enqueue_ingestion(str(document.id))
            print(f"  enqueued ingestion: event_id={event_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR on {doc['file_name']}: {type(exc).__name__}: {exc}")

    await db.dispose()
    print(
        "done. Run the knowledge worker (python -m workers.knowledge_worker) to vectorise."
    )


if __name__ == "__main__":
    asyncio.run(main())
