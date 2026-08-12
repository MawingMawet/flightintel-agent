from flightintel.rag.chunking import Chunk, chunk_markdown


def sec(title: str, level: int, body_chars: int) -> str:
    return f"{'#' * level} {title}\n\n{'x' * body_chars}\n\n"


def test_small_file_is_one_whole_chunk():
    text = sec("ADR 0007: Vectors in pgvector", 1, 100) + sec("Context", 2, 200)
    [chunk] = chunk_markdown(text, "0007-vectors")
    assert chunk.breadcrumb == "ADR 0007: Vectors in pgvector"
    assert chunk.heading == "ADR 0007: Vectors in pgvector"
    assert chunk.text == text.strip("\n")
    assert chunk.part is None


def test_oversized_file_splits_at_sections_with_breadcrumbs():
    text = (
        sec("Big Doc", 1, 300)
        + sec("Alpha", 2, 900)
        + sec("Beta", 2, 900)
        + sec("Gamma", 2, 900)
    )
    chunks = chunk_markdown(text, "big", cap=1500, floor=50)
    crumbs = [c.breadcrumb for c in chunks]
    assert crumbs == ["Big Doc", "Big Doc > Alpha", "Big Doc > Beta", "Big Doc > Gamma"]
    assert chunks[1].text.startswith("## Alpha")
    assert chunks[1].heading == "Alpha"


def test_h1_title_not_duplicated_in_breadcrumb():
    text = sec("Doc", 1, 2000) + sec("Alpha", 2, 2000)
    chunks = chunk_markdown(text, "doc", cap=1500, floor=50)
    assert all(not c.breadcrumb.startswith("Doc > Doc") for c in chunks)


def test_recurses_into_subsections_when_section_over_cap():
    text = (
        sec("Doc", 1, 100)
        + sec("Huge", 2, 100)
        + sec("Sub A", 3, 900)
        + sec("Sub B", 3, 900)
        + sec("Small", 2, 300)
    )
    chunks = chunk_markdown(text, "doc", cap=1500, floor=50)
    crumbs = [c.breadcrumb for c in chunks]
    assert "Doc > Huge > Sub A" in crumbs
    assert "Doc > Huge > Sub B" in crumbs
    assert "Doc > Small" in crumbs


def test_fenced_hash_lines_are_not_headings():
    fenced = "# Doc\n\n```\n# not a heading\n## also not\n```\n\n" + "x" * 100
    [chunk] = chunk_markdown(fenced, "doc")
    assert "# not a heading" in chunk.text


def test_stub_section_merges_into_following_chunk():
    text = (
        sec("Doc", 1, 500)
        + "## Status stub\n\nAccepted.\n\n"
        + sec("Meaty", 2, 1200)
    )
    chunks = chunk_markdown(text, "doc", cap=1000, floor=60)
    assert all(len(c.text) >= 60 for c in chunks)
    merged = next(c for c in chunks if "Status stub" in c.text)
    assert merged.breadcrumb == "Doc > Meaty"


def test_trailing_stub_merges_into_previous_chunk():
    text = sec("Doc", 1, 500) + sec("Meaty", 2, 1200) + "## Tail\n\nok\n"
    chunks = chunk_markdown(text, "doc", cap=1000, floor=60)
    assert "## Tail" in chunks[-1].text
    assert all(len(c.text) >= 60 for c in chunks)


def test_leaf_over_cap_splits_at_paragraphs_with_parts():
    paras = "\n\n".join(f"para {i} " + "y" * 400 for i in range(8))
    text = sec("Doc", 1, 100) + "## Wall\n\n" + paras + "\n\n" + sec("Other", 2, 2000)
    chunks = chunk_markdown(text, "doc", cap=1500, floor=50)
    wall = [c for c in chunks if c.breadcrumb == "Doc > Wall"]
    assert len(wall) > 1
    assert [c.part for c in wall] == list(range(1, len(wall) + 1))
    assert all(len(c.text) <= 1500 for c in wall)
    assert "para 0" in wall[0].text and "para 7" in wall[-1].text


def test_no_headings_at_all_uses_fallback_title():
    text = "\n\n".join("z" * 400 for _ in range(10))
    chunks = chunk_markdown(text, "CHANGELOG", cap=1500, floor=50)
    assert all(c.breadcrumb == "CHANGELOG" for c in chunks)
    assert len(chunks) > 1


def test_empty_input_gives_no_chunks():
    assert chunk_markdown("\n\n", "empty") == []


def test_chunks_reconstruct_all_content():
    text = (
        sec("Doc", 1, 300)
        + sec("Alpha", 2, 900)
        + sec("Beta", 2, 900)
        + "## Stub\n\nshort\n\n"
        + sec("Gamma", 2, 900)
    )
    chunks = chunk_markdown(text, "doc", cap=1500, floor=60)
    joined = "\n".join(c.text for c in chunks)
    for token in ("# Doc", "## Alpha", "## Beta", "## Stub", "short", "## Gamma"):
        assert token in joined


def test_chunk_model_roundtrip():
    c = Chunk(breadcrumb="A > B", heading="B", text="body", part=2)
    assert Chunk.model_validate(c.model_dump()) == c
