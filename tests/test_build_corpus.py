import json
import subprocess

import pytest

from flightintel.rag.build_corpus import (
    build_snapshot,
    chunk_id,
    collect,
    resolve_version,
    slugify,
    tracked_files,
)
from flightintel.rag.chunking import Chunk


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "A.md").write_text("# Doc A\n\nalpha body\n", encoding="utf-8")
    (root / "docs" / "B.md").write_text("# Doc B\n\nbeta body\n", encoding="utf-8")
    (root / "docs" / "UNTRACKED.md").write_text("# Private\n\nnot committed\n", encoding="utf-8")
    git(root, "init")
    git(root, "add", "docs/A.md", "docs/B.md")
    git(root, "commit", "-m", "init")
    return root


def test_collect_selects_tracked_and_reports_untracked(repo):
    tracked = tracked_files(repo)
    selected, skipped = collect(repo, ["docs/*.md"], tracked)
    assert selected == ["docs/A.md", "docs/B.md"]
    assert skipped == ["docs/UNTRACKED.md"]


def test_collect_fails_loudly_on_dead_pattern(repo):
    with pytest.raises(FileNotFoundError):
        collect(repo, ["docs/MISSING*.md"], tracked_files(repo))


def test_slugify():
    assert slugify("Options considered") == "options-considered"
    assert slugify("Q&A (written gates)") == "q-a-written-gates"


def test_chunk_ids_whole_file_section_part_and_collision():
    used = set()
    whole = Chunk(breadcrumb="Doc", heading="Doc", text="t")
    section = Chunk(breadcrumb="Doc > Context", heading="Context", text="t")
    part = Chunk(breadcrumb="Doc > Wall", heading="Wall", text="t", part=2)
    assert chunk_id("R", "d/a.md", whole, "Doc", used) == "R/d/a.md"
    assert chunk_id("R", "d/a.md", section, "Doc", used) == "R/d/a.md#context"
    assert chunk_id("R", "d/a.md", section, "Doc", used) == "R/d/a.md#context-2"
    assert chunk_id("R", "d/a.md", part, "Doc", used) == "R/d/a.md#wall-p2"


def test_build_snapshot_manifest_and_records(repo):
    manifest, records = build_snapshot([("TestRepo", repo, ["docs/*.md"])])
    assert manifest["totals"]["files"] == 2
    assert [f["path"] for f in manifest["files"]] == ["docs/A.md", "docs/B.md"]
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])
    (r,) = manifest["repos"]
    assert len(r["commit"]) == 40
    assert r["dirty_files"] == []
    assert r["skipped_untracked"] == ["docs/UNTRACKED.md"]
    ids = [rec["id"] for rec in records]
    assert ids == ["TestRepo/docs/A.md", "TestRepo/docs/B.md"]
    assert records[0]["breadcrumb"] == "Doc A"
    assert "alpha body" in records[0]["text"]


def test_build_snapshot_records_dirty_files(repo):
    (repo / "docs" / "A.md").write_text("# Doc A\n\nedited\n", encoding="utf-8")
    manifest, _ = build_snapshot([("TestRepo", repo, ["docs/*.md"])])
    assert manifest["repos"][0]["dirty_files"] == ["docs/A.md"]


def make_manifest(version, hashes):
    return {
        "corpus_version": version,
        "files": [
            {"repo": r, "path": p, "sha256": h} for (r, p), h in hashes.items()
        ],
    }


def test_resolve_version_first_build_defaults_to_1():
    new = make_manifest(None, {("R", "a.md"): "x"})
    assert resolve_version(None, new, None) == 1


def test_resolve_version_unchanged_content_keeps_version():
    old = make_manifest(3, {("R", "a.md"): "x"})
    new = make_manifest(None, {("R", "a.md"): "x"})
    assert resolve_version(old, new, None) == 3


def test_resolve_version_changed_content_without_bump_fails():
    old = make_manifest(3, {("R", "a.md"): "x"})
    new = make_manifest(None, {("R", "a.md"): "y"})
    with pytest.raises(SystemExit, match="--corpus-version 4"):
        resolve_version(old, new, None)


def test_resolve_version_bump_must_exceed_existing():
    old = make_manifest(3, {("R", "a.md"): "x"})
    new = make_manifest(None, {("R", "a.md"): "y"})
    with pytest.raises(SystemExit, match="greater"):
        resolve_version(old, new, 3)
    assert resolve_version(old, new, 4) == 4


def test_records_serialize_to_jsonl(repo):
    _, records = build_snapshot([("TestRepo", repo, ["docs/*.md"])])
    line = json.dumps(records[0], ensure_ascii=False)
    assert json.loads(line)["id"] == "TestRepo/docs/A.md"
