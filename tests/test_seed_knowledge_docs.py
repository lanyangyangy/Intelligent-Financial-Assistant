import subprocess
import sys

from scripts import seed_knowledge_docs as seed


def test_finance_archive_markdown_and_text_files_are_registered() -> None:
    docs = seed.build_document_specs()

    relative_paths = {
        doc["path"].relative_to(seed.ROOT).as_posix() for doc in docs
    }

    assert "金融/公司业务/个人理财产品手册.md" in relative_paths
    assert "金融/公司信息/高频问答对.txt" in relative_paths
    assert "金融/金融政策/理财产品销售管理办法.md" in relative_paths
    assert "金融/用户研判规则/投资者风险画像研判规则.md" in relative_paths
    assert "金融/功能设计文档.html" not in relative_paths


def test_finance_archive_documents_get_stable_keys_and_categories() -> None:
    docs = seed.build_document_specs()
    by_name = {doc["file_name"]: doc for doc in docs}

    faq = by_name["高频问答对.txt"]
    assert faq["document_key"] == "finance-company-info-faq-pairs"
    assert faq["category"] == "faq"
    assert faq["file_type"] == "plain_text"

    product = by_name["个人理财产品手册.md"]
    assert product["document_key"] == "finance-company-business-personal-wealth-products"
    assert product["category"] == "product"
    assert product["file_type"] == "markdown"

    rule = by_name["投资者风险画像研判规则.md"]
    assert rule["category"] == "policy"


def test_document_keys_are_unique_when_archive_has_same_stem_files() -> None:
    docs = seed.build_document_specs()
    keys = [doc["document_key"] for doc in docs]

    assert len(keys) == len(set(keys))
    assert {
        doc["document_key"] for doc in docs if doc["file_name"].startswith("用户信息数据示例")
    } == {
        "finance-user-assessment-rules-user-info-data-example",
        "finance-user-assessment-rules-user-info-data-example-txt",
    }


def test_seed_script_can_be_loaded_by_file_path() -> None:
    script = seed.ROOT / "scripts" / "seed_knowledge_docs.py"
    command = (
        "import runpy, sys; "
        f"root = {str(seed.ROOT)!r}; "
        f"script_dir = {str(script.parent)!r}; "
        "sys.path = [script_dir] + [p for p in sys.path if p not in {'', root}]; "
        f"runpy.run_path({str(script)!r}, run_name='seed_knowledge_docs_probe')"
    )

    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=seed.ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
