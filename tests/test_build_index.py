import json

import pytest

from flightintel.rag.build_index import (
    EMBEDDING_DIMS,
    batched,
    build_index_manifest,
    embedding_input,
    load_corpus,
    vector_literal,
)


def test_batched_splits_with_remainder():
    assert list(batched(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    assert list(batched([], 2)) == []
    assert list(batched([1], 5)) == [[1]]


def test_embedding_input_prepends_breadcrumb():
    record = {"breadcrumb": "Doc > Section", "text": "body text"}
    assert embedding_input(record) == "Doc > Section\n\nbody text"


def test_vector_literal_round_trippable():
    assert vector_literal([1.0, -2.5, 0.125]) == "[1.0,-2.5,0.125]"
    assert vector_literal([]) == "[]"


def test_index_manifest_records_provenance():
    corpus_manifest = {"corpus_version": 7}
    m = build_index_manifest(corpus_manifest, "some-model", 42, "17.10", "0.8.6")
    assert m["corpus_version"] == 7
    assert m["embedding_model"] == "some-model"
    assert m["embedding_dims"] == EMBEDDING_DIMS
    assert m["chunk_count"] == 42
    assert m["postgres_version"] == "17.10"
    assert m["pgvector_version"] == "0.8.6"
    assert m["distance"] == "cosine"
    assert m["ann_index"] is None


def _write_corpus(tmp_path, records, totals):
    manifest = {"corpus_version": 1, "totals": totals}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    lines = "".join(json.dumps(r) + "\n" for r in records)
    (tmp_path / "chunks.jsonl").write_text(lines, encoding="utf-8")


def test_load_corpus_accepts_consistent_snapshot(tmp_path):
    records = [{"id": "a", "chars": 3}, {"id": "b", "chars": 5}]
    _write_corpus(tmp_path, records, {"chunks": 2, "chars": 8})
    manifest, loaded = load_corpus(tmp_path)
    assert manifest["corpus_version"] == 1
    assert [r["id"] for r in loaded] == ["a", "b"]


def test_load_corpus_fails_loudly_on_mismatch(tmp_path):
    records = [{"id": "a", "chars": 3}]
    _write_corpus(tmp_path, records, {"chunks": 2, "chars": 8})
    with pytest.raises(SystemExit, match="does not match"):
        load_corpus(tmp_path)
