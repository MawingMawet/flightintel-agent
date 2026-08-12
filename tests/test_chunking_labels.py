"""Unit tests for bold-label splitting in oversized leaf sections
(corpus v2, PHASE2_PLAN Q14)."""

from flightintel.rag.chunking import _bold_label, chunk_markdown


def qa_doc(block_size: int = 120) -> str:
    """A doc whose Q&A section exceeds a small cap and holds three
    bold-labeled blocks plus an unlabeled preamble."""
    pad = "x" * block_size
    return (
        "# Plan\n\nintro text long enough to not be a stub. " + pad + "\n\n"
        "## Judgment Q&A\n\n"
        "Preamble paragraph before any label. " + pad + "\n\n"
        f"**Q1. First question?**\nAnswer one. {pad}\n\n"
        f"Follow-up paragraph for question one. {pad}\n\n"
        f"**Q2. Second question?**\nAnswer two. {pad}\n\n"
        f"**Q3. Third question?**\nAnswer three. {pad}\n"
    )


def test_labels_become_own_chunks_with_label_headings():
    # cap 400: each labeled block fits whole; the section does not.
    chunks = chunk_markdown(qa_doc(), "plan", cap=400, floor=40)
    qa = [c for c in chunks if "Q" in c.heading and c.heading.endswith("?")]
    assert [c.heading for c in qa] == [
        "Q1. First question?", "Q2. Second question?", "Q3. Third question?"
    ]
    assert all(c.breadcrumb.endswith(c.heading) for c in qa)
    # the follow-up paragraph stays with its question, not the next one
    q1 = next(c for c in qa if c.heading.startswith("Q1"))
    assert "Follow-up paragraph" in q1.text


def test_unlabeled_preamble_keeps_section_heading():
    chunks = chunk_markdown(qa_doc(), "plan", cap=400, floor=40)
    preamble = next(c for c in chunks if "Preamble paragraph" in c.text)
    assert preamble.heading == "Judgment Q&A"


def test_sections_without_labels_pack_exactly_as_before():
    pad = "y" * 150
    doc = (
        "# Doc\n\nintro. " + pad + "\n\n"
        "## Long section\n\n"
        f"one. {pad}\n\ntwo. {pad}\n\nthree. {pad}\n"
    )
    chunks = chunk_markdown(doc, "doc", cap=300, floor=40)
    packed = [c for c in chunks if c.heading == "Long section"]
    assert len(packed) > 1
    assert all(c.part is not None for c in packed)


def test_bold_label_detection():
    assert _bold_label("**Q9. Loop design?**\nbody") == "Q9. Loop design?"
    assert _bold_label("plain paragraph **with bold** inside") is None
    assert _bold_label("**unclosed bold\nmore") is None
