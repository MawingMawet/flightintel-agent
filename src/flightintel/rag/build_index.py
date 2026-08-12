"""Vector index builder: embeds the committed corpus snapshot into the
local pgvector store (PHASE2_PLAN deliverable 3; decisions in Q4 and Q7).

The build is a versioned event tied to the corpus snapshot: it reads
corpus/chunks.jsonl, checks it against corpus/manifest.json, embeds each
chunk's breadcrumb-prefixed text with the pinned embedding model, and
replaces the doc_chunks table content in one transaction. A rebuild is a
replace, never an in-place mutation; corpus/index_manifest.json records
exactly which model, dimensionality, and corpus version produced the
rows now in the table.

Run from the project root:
    .venv/Scripts/python.exe -m flightintel.rag.build_index
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import psycopg
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from flightintel.config import load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = PROJECT_ROOT / "corpus"

# Probed live at Q4: gemini-embedding-2 returns 3072 dims by default. The
# table column is typed to this; a model change is a rebuild, not a mutation.
EMBEDDING_DIMS = 3072
# Asymmetric retrieval embedding: documents and queries are embedded with
# different task types so the model places questions near the passages that
# answer them, not near passages that merely look similar.
TASK_TYPE_DOCUMENTS = "RETRIEVAL_DOCUMENT"
TASK_TYPE_QUERIES = "RETRIEVAL_QUERY"
BATCH_SIZE = 50

DDL = f"""
CREATE TABLE doc_chunks (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    breadcrumb TEXT NOT NULL,
    heading TEXT,
    part INTEGER,
    chars INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding vector({EMBEDDING_DIMS}) NOT NULL
)
"""

INSERT = """
INSERT INTO doc_chunks (id, repo, path, breadcrumb, heading, part, chars, text, embedding)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
"""


def load_corpus(corpus_dir: Path) -> tuple[dict, list[dict]]:
    """Load the snapshot and fail loudly if chunks and manifest disagree:
    the index must only ever be built from a consistent, committed corpus."""
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (corpus_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    totals = manifest["totals"]
    if len(records) != totals["chunks"] or sum(r["chars"] for r in records) != totals["chars"]:
        raise SystemExit(
            "corpus/chunks.jsonl does not match corpus/manifest.json; "
            "rebuild the corpus before building the index."
        )
    return manifest, records


def embedding_input(record: dict) -> str:
    """Breadcrumb prepended to the chunk text (Q3): the vector keeps document
    context even when the chunk is a deep subsection."""
    return f"{record['breadcrumb']}\n\n{record['text']}"


def batched(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def vector_literal(vec: Iterable[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def build_index_manifest(
    corpus_manifest: dict,
    model: str,
    chunk_count: int,
    server_version: str,
    pgvector_version: str,
) -> dict:
    return {
        "corpus_version": corpus_manifest["corpus_version"],
        "embedding_model": model,
        "embedding_dims": EMBEDDING_DIMS,
        "task_type_documents": TASK_TYPE_DOCUMENTS,
        "task_type_queries": TASK_TYPE_QUERIES,
        "distance": "cosine",
        "ann_index": None,
        "chunk_count": chunk_count,
        "postgres_version": server_version,
        "pgvector_version": pgvector_version,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def embed_with_retry(
    embedder: GoogleGenerativeAIEmbeddings, texts: list[str], tries: int = 4
) -> list[list[float]]:
    for attempt in range(tries):
        try:
            return embedder.embed_documents(texts, task_type=TASK_TYPE_DOCUMENTS)
        except Exception as exc:
            quota_hit = "429" in str(exc) and attempt < tries - 1
            if not quota_hit:
                raise
            wait = 5 * 2**attempt
            print(f"  quota 429, backing off {wait}s")
            time.sleep(wait)
    raise AssertionError("unreachable")


def main() -> None:
    settings = load_settings()
    if settings.pg_dsn is None:
        raise SystemExit("Set PG_DSN in .env (scripts/setup_pgvector.sh writes it).")
    if settings.gemini_api_key is None:
        raise SystemExit("Set GEMINI_API_KEY in .env.")

    corpus_manifest, records = load_corpus(CORPUS_DIR)
    inputs = [embedding_input(r) for r in records]

    # Connect before embedding: the DB being down should cost zero API
    # calls, and the open connection keeps the WSL VM from idling out
    # underneath the build.
    with psycopg.connect(settings.pg_dsn.get_secret_value()) as conn:
        ext = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if ext is None:
            raise SystemExit("pgvector extension missing; run scripts/setup_pgvector.sh.")
        server_version = conn.execute("SHOW server_version").fetchone()[0]

        embedder = GoogleGenerativeAIEmbeddings(
            model=f"models/{settings.embedding_model}",
            google_api_key=settings.gemini_api_key,
        )
        started = time.monotonic()
        vectors: list[list[float]] = []
        for batch in batched(inputs, BATCH_SIZE):
            vectors.extend(embed_with_retry(embedder, batch))
            print(f"  embedded {len(vectors)}/{len(inputs)}")
        for i, vec in enumerate(vectors):
            if len(vec) != EMBEDDING_DIMS:
                raise SystemExit(
                    f"chunk {records[i]['id']}: got {len(vec)} dims, expected {EMBEDDING_DIMS}."
                )

        with conn.transaction():
            conn.execute("DROP TABLE IF EXISTS doc_chunks")
            conn.execute(DDL)
            with conn.cursor() as cur:
                cur.executemany(
                    INSERT,
                    [
                        (
                            r["id"], r["repo"], r["path"], r["breadcrumb"],
                            r["heading"], r["part"], r["chars"], r["text"],
                            vector_literal(vec),
                        )
                        for r, vec in zip(records, vectors)
                    ],
                )

    index_manifest = build_index_manifest(
        corpus_manifest, settings.embedding_model, len(records), server_version, ext[0]
    )
    manifest_path = CORPUS_DIR / "index_manifest.json"
    manifest_path.write_text(json.dumps(index_manifest, indent=2) + "\n", encoding="utf-8")

    elapsed = time.monotonic() - started
    print(
        f"\nindexed corpus v{corpus_manifest['corpus_version']}: "
        f"{len(records)} chunks x {EMBEDDING_DIMS} dims "
        f"({settings.embedding_model}) in {elapsed:.0f}s"
    )
    print(f"wrote {manifest_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
