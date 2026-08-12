"""Snapshot a bounded real-data subset of products.db into sample.db (Q6).

The committed sample.db makes the repo runnable without AirflowOSky: point
FLIGHT_DB_PATH at it to run the CLI, the tests, or the eval harness. It is
a real-data subset, not synthetic, so the upstream provenance rule is
untouched. Regenerate and re-commit when the contract changes.

Run from the project root:
    .venv/Scripts/python.exe evals/fixtures/build_sample_db.py [--days N]
"""

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flightintel.config import load_settings
from flightintel.db import (
    EXPECTED_SCHEMA,
    assert_contract,
    connect_readonly,
    open_products_db,
)

OUT_PATH = Path("evals/fixtures/sample.db")
AUDIT_RUNS_KEPT = 20


def hour_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--days", type=int, default=30,
        help="Days of coverage kept per data half, anchored to that half's "
        "most recent data (the two halves were captured in different "
        "sessions, so a single shared window would empty one of them).",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    src = open_products_db(settings.flight_db_path)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUT_PATH.exists():
        OUT_PATH.unlink()
    dst = sqlite3.connect(OUT_PATH)

    # Exact DDL copy from sqlite_master, not a reconstruction: the sample
    # must be schema-identical to the source by construction.
    for table in EXPECTED_SCHEMA:
        ddl = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()[0]
        dst.execute(ddl)

    flights_cut = (
        src.execute("SELECT MAX(last_seen) FROM flights").fetchone()[0]
        - args.days * 86400
    )
    states_cut = (
        src.execute("SELECT MAX(last_contact) FROM states").fetchone()[0]
        - args.days * 86400
    )

    # Hour-aligned windows keep gold and silver reconcilable in the sample.
    copies = {
        "flights": ("SELECT * FROM flights WHERE last_seen >= ?", (flights_cut,)),
        "od_hourly": (
            "SELECT * FROM od_hourly WHERE hour_utc >= ?",
            (hour_str(flights_cut),),
        ),
        "states": ("SELECT * FROM states WHERE last_contact >= ?", (states_cut,)),
        "heatmap_hourly": (
            "SELECT * FROM heatmap_hourly WHERE hour_utc >= ?",
            (hour_str(states_cut),),
        ),
        "build_audit": (
            "SELECT * FROM (SELECT * FROM build_audit ORDER BY run_ts DESC "
            "LIMIT ?) ORDER BY run_ts",
            (AUDIT_RUNS_KEPT,),
        ),
        "build_audit_states": (
            "SELECT * FROM (SELECT * FROM build_audit_states ORDER BY run_ts "
            "DESC LIMIT ?) ORDER BY run_ts",
            (AUDIT_RUNS_KEPT,),
        ),
    }
    for table, (query, params) in copies.items():
        rows = [tuple(r) for r in src.execute(query, params).fetchall()]
        if rows:
            placeholders = ", ".join("?" * len(rows[0]))
            dst.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})", rows
            )
        print(f"{table}: {len(rows)} rows")
    dst.commit()
    dst.close()
    src.close()

    # Verify the artifact the way every consumer will open it.
    ver = connect_readonly(OUT_PATH)
    assert_contract(ver)
    for table in ("od_hourly", "heatmap_hourly"):
        lo, hi = ver.execute(
            f"SELECT MIN(hour_utc), MAX(hour_utc) FROM {table}"
        ).fetchone()
        print(f"{table} coverage: {lo} .. {hi}")
    ver.close()
    print(f"OK: {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB), contract verified")


if __name__ == "__main__":
    main()
