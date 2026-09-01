from scripts.generate_finance_corpus import build_corpus


def test_generated_finance_corpus_has_exactly_1000_records() -> None:
    corpus = build_corpus()

    assert sum(document.record_count for document in corpus) == 1000


def test_faq_document_keeps_tab_separated_question_answer_format() -> None:
    corpus = build_corpus()
    faq = next(document for document in corpus if document.path.name == "高频问答对.txt")

    lines = [line for line in faq.content.splitlines() if line.strip()]

    assert faq.record_count == 250
    assert all("\t" in line for line in lines)
    assert all(len(line.split("\t", 1)) == 2 for line in lines)
