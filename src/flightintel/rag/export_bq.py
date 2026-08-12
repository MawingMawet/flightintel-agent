"""BigQuery vector table export (PHASE4_PLAN Q2a): copies the doc_chunks
rows, vectors included, from the local pgvector store into the cloud
table, so both backends search IDENTICAL vectors and any rank difference
is the engine's, not the embedder's.

The export is a versioned event like build_index: it cross-checks the
local index manifest first, creates the dataset if missing (Bangkok
region, labeled per house rule), replaces the table in one WRITE_TRUNCATE
load job, verifies row count and dims after the load, and records
corpus/bq_index_manifest.json.

Run from the project root (WSL Postgres up; ADC per plan Q2d):
    .venv/Scripts/python.exe -m flightintel.rag.export_bq
"""

import json
import sys
from datetime import datetime, timezone

import psycopg
from google.cloud import bigquery

from flightintel.config import load_settings
from flightintel.rag.build_index import CORPUS_DIR, EMBEDDING_DIMS, PROJECT_ROOT
from flightintel.tools.docs_bq import make_bq_client

DATASET_LOCATION = "asia-southeast1"
DATASET_LABELS = {"project": "flightintel"}

# pgvector's text form is a JSON float array; ::text keeps the export free
# of any vector-type adapter.
EXPORT_SQL = """
SELECT id, repo, path, breadcrumb, heading, part, chars, text, embedding::text
FROM doc_chunks
ORDER BY id
"""

SCHEMA = [
    bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("repo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("path", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("breadcrumb", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("heading", "STRING"),
    bigquery.SchemaField("part", "INT64"),
    bigquery.SchemaField("chars", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("text", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
]


def main() -> None:
    settings = load_settings()
    if settings.pg_dsn is None:
        raise SystemExit("Set PG_DSN in .env; the export reads the local store.")
    if settings.bq_vector_table is None:
        raise SystemExit("Set BQ_VECTOR_TABLE in .env (project.dataset.table).")

    index_manifest = json.loads(
        (CORPUS_DIR / "index_manifest.json").read_text(encoding="utf-8")
    )

    with psycopg.connect(settings.pg_dsn.get_secret_value()) as conn:
        rows = conn.execute(EXPORT_SQL).fetchall()
    if len(rows) != index_manifest["chunk_count"]:
        raise SystemExit(
            f"local store has {len(rows)} chunks, index manifest says "
            f"{index_manifest['chunk_count']}; rebuild before exporting."
        )

    records = []
    for r in rows:
        vec = json.loads(r[8])
        if len(vec) != EMBEDDING_DIMS:
            raise SystemExit(
                f"chunk {r[0]}: {len(vec)} dims, expected {EMBEDDING_DIMS}."
            )
        records.append(
            {
                "id": r[0], "repo": r[1], "path": r[2], "breadcrumb": r[3],
                "heading": r[4], "part": r[5], "chars": r[6], "text": r[7],
                "embedding": vec,
            }
        )

    client = make_bq_client(settings)
    project, dataset_id, _table = settings.bq_vector_table.split(".")
    dataset = bigquery.Dataset(f"{project}.{dataset_id}")
    dataset.location = DATASET_LOCATION
    dataset.labels = DATASET_LABELS
    client.create_dataset(dataset, exists_ok=True)

    job = client.load_table_from_json(
        records,
        settings.bq_vector_table,
        job_config=bigquery.LoadJobConfig(
            schema=SCHEMA, write_disposition="WRITE_TRUNCATE"
        ),
    )
    job.result()

    check = list(
        client.query(
            f"""
            SELECT COUNT(*) AS n,
                   MIN(ARRAY_LENGTH(embedding)) AS min_dims,
                   MAX(ARRAY_LENGTH(embedding)) AS max_dims
            FROM `{settings.bq_vector_table}`
            """
        ).result()
    )[0]
    if (
        check.n != len(records)
        or check.min_dims != EMBEDDING_DIMS
        or check.max_dims != EMBEDDING_DIMS
    ):
        raise SystemExit(
            f"post-load check failed: rows={check.n}, "
            f"dims={check.min_dims}..{check.max_dims}"
        )

    manifest = {
        "corpus_version": index_manifest["corpus_version"],
        "embedding_model": index_manifest["embedding_model"],
        "embedding_dims": EMBEDDING_DIMS,
        "task_type_documents": index_manifest["task_type_documents"],
        "task_type_queries": index_manifest["task_type_queries"],
        "distance": "cosine",
        "source": "pgvector-export",
        "table": settings.bq_vector_table,
        "dataset_location": DATASET_LOCATION,
        "chunk_count": len(records),
        "load_job_id": job.job_id,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out = CORPUS_DIR / "bq_index_manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"loaded {len(records)} chunks x {EMBEDDING_DIMS} dims -> "
        f"{settings.bq_vector_table} (job {job.job_id})"
    )
    print(f"wrote {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
