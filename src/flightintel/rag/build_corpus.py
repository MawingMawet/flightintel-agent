"""Corpus builder: committed markdown from both repos into a versioned
snapshot (PHASE2_PLAN deliverable 1; scope per Q2 and Q3a).

Only git-tracked files enter the corpus, which structurally enforces the
no-private-files rule: anything git-ignored is invisible here. The manifest
records the corpus version, each repo's HEAD commit, and per-file hashes; a
rebuild whose content differs from the committed manifest must bump the
version explicitly, so a corpus refresh is a versioned event evals can
reference (Q2).

Run from the project root:
    .venv/Scripts/python.exe -m flightintel.rag.build_corpus [--corpus-version N]
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flightintel.config import load_settings
from flightintel.rag.chunking import CAP_CHARS, FLOOR_CHARS, Chunk, chunk_markdown

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = PROJECT_ROOT / "corpus"

UPSTREAM_SOURCES = [
    "working-docs/adr/0*.md",
    "docs/ARCHITECTURE.md",
    "docs/PRESENTATION.md",
    "docs/FLIGHTINTEL_CONSUMER.md",
    "working-docs/knowledge/LEARNING_NOTES.md",
    "working-docs/knowledge/JUDGMENT_DRILLS.md",
    "working-docs/knowledge/GRADED_ANSWERS.md",
    "working-docs/knowledge/FIELD_NOTES.md",
    "CHANGELOG.md",
]
DOWNSTREAM_SOURCES = [
    "working-docs/adr/0*.md",
    "working-docs/knowledge/architecture.md",
    "working-docs/knowledge/under-the-hood.md",
    "working-docs/knowledge/review/0*.md",
    "working-docs/plans/*.md",
    "docs/*.md",
    "README.md",
]


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def tracked_files(root: Path) -> set[str]:
    return set(_git(root, "ls-files").splitlines())


def repo_commit(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def dirty_files(root: Path, paths: list[str]) -> list[str]:
    if not paths:
        return []
    out = _git(root, "status", "--porcelain", "--", *paths)
    # Porcelain lines are "XY path"; slice off the two status chars and strip.
    return sorted(line[2:].strip() for line in out.splitlines() if line)


def collect(root: Path, patterns: list[str], tracked: set[str]) -> tuple[list[str], list[str]]:
    """Resolve glob patterns to repo-relative posix paths; only tracked files
    are selected, untracked matches are reported."""
    selected: list[str] = []
    skipped: list[str] = []
    for pattern in patterns:
        matches = sorted(p.relative_to(root).as_posix() for p in root.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"corpus pattern matched nothing: {root} :: {pattern}")
        for rel in matches:
            (selected if rel in tracked else skipped).append(rel)
    return selected, skipped


def slugify(s: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in s)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def chunk_id(repo: str, path: str, chunk: Chunk, doc_title: str, used: set[str]) -> str:
    if chunk.breadcrumb == doc_title and chunk.part is None:
        base = f"{repo}/{path}"
    else:
        slug = slugify(chunk.heading) or "section"
        base = f"{repo}/{path}#{slug}"
        if chunk.part is not None:
            base += f"-p{chunk.part}"
    cid, n = base, 2
    while cid in used:
        cid, n = f"{base}-{n}", n + 1
    used.add(cid)
    return cid


def build_snapshot(
    repos: list[tuple[str, Path, list[str]]],
    cap: int = CAP_CHARS,
    floor: int = FLOOR_CHARS,
) -> tuple[dict, list[dict]]:
    manifest_repos = []
    manifest_files = []
    records: list[dict] = []
    for name, root, patterns in repos:
        tracked = tracked_files(root)
        selected, skipped = collect(root, patterns, tracked)
        for rel in skipped:
            print(f"WARNING: skipping untracked file (not committed): {name}/{rel}")
        dirty = dirty_files(root, selected)
        for rel in dirty:
            print(f"WARNING: file differs from HEAD, snapshot is of the working tree: {name}/{rel}")
        manifest_repos.append(
            {
                "name": name,
                "commit": repo_commit(root),
                "dirty_files": dirty,
                "skipped_untracked": skipped,
            }
        )
        used_ids: set[str] = set()
        for rel in selected:
            text = (root / rel).read_text(encoding="utf-8")
            chunks = chunk_markdown(text, Path(rel).stem, cap=cap, floor=floor)
            doc_title = chunks[0].breadcrumb.split(" > ")[0] if chunks else Path(rel).stem
            for chunk in chunks:
                records.append(
                    {
                        "id": chunk_id(name, rel, chunk, doc_title, used_ids),
                        "repo": name,
                        "path": rel,
                        "breadcrumb": chunk.breadcrumb,
                        "heading": chunk.heading,
                        "part": chunk.part,
                        "chars": len(chunk.text),
                        "text": chunk.text,
                    }
                )
            manifest_files.append(
                {
                    "repo": name,
                    "path": rel,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "chars": len(text),
                    "chunks": len(chunks),
                }
            )
    manifest = {
        "corpus_version": None,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cap_chars": cap,
        "floor_chars": floor,
        "repos": manifest_repos,
        "files": manifest_files,
        "totals": {
            "files": len(manifest_files),
            "chunks": len(records),
            "chars": sum(r["chars"] for r in records),
        },
    }
    return manifest, records


def resolve_version(existing: dict | None, new_manifest: dict, requested: int | None) -> int:
    """A content change without an explicit version bump is an error: corpus
    refreshes are versioned events (Q2)."""
    if existing is None:
        return requested if requested is not None else 1
    old_version = existing["corpus_version"]
    old_hashes = {(f["repo"], f["path"]): f["sha256"] for f in existing["files"]}
    new_hashes = {(f["repo"], f["path"]): f["sha256"] for f in new_manifest["files"]}
    changed = old_hashes != new_hashes
    if requested is None:
        if changed:
            raise SystemExit(
                f"Corpus content changed but the version was not bumped. "
                f"Re-run with --corpus-version {old_version + 1}."
            )
        return old_version
    if requested <= old_version:
        raise SystemExit(
            f"--corpus-version {requested} must be greater than the existing {old_version}."
        )
    return requested


def print_stats(manifest: dict, records: list[dict]) -> None:
    sizes = sorted(r["chars"] for r in records)
    totals = manifest["totals"]
    print(f"\ncorpus v{manifest['corpus_version']}: "
          f"{totals['files']} files, {totals['chunks']} chunks, {totals['chars']} chars")
    for repo in manifest["repos"]:
        n = sum(1 for r in records if r["repo"] == repo["name"])
        print(f"  {repo['name']}: {n} chunks @ {repo['commit'][:9]}"
              f"{' DIRTY' if repo['dirty_files'] else ''}")
    mid = sizes[len(sizes) // 2]
    over = [r for r in records if r["chars"] > manifest["cap_chars"]]
    print(f"  chunk chars: min={sizes[0]} median={mid} max={sizes[-1]}; "
          f"over cap: {len(over)}")
    for r in over:
        print(f"    OVER CAP (lone paragraph, index build must handle): {r['id']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the versioned RAG corpus snapshot.")
    parser.add_argument("--corpus-version", type=int, default=None)
    args = parser.parse_args(argv)

    settings = load_settings()
    if settings.upstream_repo_path is None or not settings.upstream_repo_path.is_dir():
        raise SystemExit("Set UPSTREAM_REPO_PATH in .env to the local AirflowOSky checkout.")

    repos = [
        ("AirflowOSky", settings.upstream_repo_path, UPSTREAM_SOURCES),
        ("FlightIntelAgent", PROJECT_ROOT, DOWNSTREAM_SOURCES),
    ]
    manifest, records = build_snapshot(repos)

    manifest_path = CORPUS_DIR / "manifest.json"
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    manifest["corpus_version"] = resolve_version(existing, manifest, args.corpus_version)

    CORPUS_DIR.mkdir(exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with (CORPUS_DIR / "chunks.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print_stats(manifest, records)
    print(f"\nwrote {manifest_path.relative_to(PROJECT_ROOT)} and corpus/chunks.jsonl")


if __name__ == "__main__":
    sys.exit(main())
