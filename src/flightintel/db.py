"""Read-only access to the upstream products.db and the contract assertion.

The cross-repo contract is the schema (ADR 0001): this module pins contract
v2 as data and fails loudly at startup if the live database drifts from it.
Within the six contract tables any difference (missing, extra, or retyped
column) is drift; tables outside the contract are ignored, because the
contract enumerates what the agent may know, not everything upstream builds.
"""

import sqlite3
from pathlib import Path

# Contract v2 (upstream Stage 3): table -> column -> declared type.
# Change policy (upstream ADR 0015, FLIGHTINTEL_CONSUMER.md): any column
# add/remove/retype inside these six tables is a contract VERSION BUMP,
# moved in one coordinated window in both repos - this pin and the prose
# doc must always agree. A SEMANTIC change (gate, dedup rule, measure
# definition) bumps too even though this structural assertion cannot see
# it; on such a bump, re-review the grain sentences and the
# caveat-derived eval cases, which encode the semantics in prose.
EXPECTED_SCHEMA: dict[str, dict[str, str]] = {
    "flights": {
        "icao24": "TEXT",
        "first_seen": "INTEGER",
        "last_seen": "INTEGER",
        "callsign": "TEXT",
        "est_departure_airport": "TEXT",
        "est_arrival_airport": "TEXT",
        "departure_candidates": "INTEGER",
        "arrival_candidates": "INTEGER",
        "source_file": "TEXT",
    },
    "od_hourly": {
        "origin": "TEXT",
        "destination": "TEXT",
        "hour_utc": "TEXT",
        "flight_count": "INTEGER",
    },
    "states": {
        "icao24": "TEXT",
        "last_contact": "INTEGER",
        "time_position": "INTEGER",
        "callsign": "TEXT",
        "longitude": "REAL",
        "latitude": "REAL",
        "baro_altitude": "REAL",
        "on_ground": "INTEGER",
        "velocity": "REAL",
        "source_file": "TEXT",
    },
    "heatmap_hourly": {
        "cell_lat": "REAL",
        "cell_lon": "REAL",
        "hour_utc": "TEXT",
        "obs_count": "INTEGER",
        "aircraft_count": "INTEGER",
    },
    "build_audit": {
        "run_ts": "TEXT",
        "files_read": "INTEGER",
        "records_parsed": "INTEGER",
        "duplicates_removed": "INTEGER",
        "conflicting_duplicates": "INTEGER",
        "rejected_null_departure": "INTEGER",
        "rejected_null_arrival": "INTEGER",
        "rows_flights": "INTEGER",
        "rows_od_hourly": "INTEGER",
    },
    "build_audit_states": {
        "run_ts": "TEXT",
        "files_read": "INTEGER",
        "records_parsed": "INTEGER",
        "duplicates_removed": "INTEGER",
        "rejected_null_position": "INTEGER",
        "rejected_on_ground": "INTEGER",
        "rows_states": "INTEGER",
        "rows_heatmap_hourly": "INTEGER",
        "stale_position_removed": "INTEGER",
    },
}


class SchemaDriftError(RuntimeError):
    """The live database no longer matches the pinned contract."""


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"products.db not found at {db_path}")
    # mode=ro rejects writes at the engine level; query_only guards this
    # connection even against accidental PRAGMA-level changes.
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    con.row_factory = sqlite3.Row
    return con


def assert_contract(con: sqlite3.Connection) -> None:
    problems: list[str] = []
    for table, expected_cols in EXPECTED_SCHEMA.items():
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            problems.append(f"missing table: {table}")
            continue
        actual_cols = {r["name"]: (r["type"] or "").upper() for r in rows}
        for col, ctype in expected_cols.items():
            if col not in actual_cols:
                problems.append(f"{table}: missing column {col}")
            elif actual_cols[col] != ctype:
                problems.append(
                    f"{table}.{col}: expected type {ctype}, found {actual_cols[col]}"
                )
        for col in actual_cols.keys() - expected_cols.keys():
            problems.append(f"{table}: unexpected column {col}")
    if problems:
        raise SchemaDriftError(
            "products.db does not match contract v2 "
            f"({len(problems)} problem(s)):\n  " + "\n  ".join(sorted(problems))
        )


def open_products_db(db_path: Path) -> sqlite3.Connection:
    """The one entry point: connect read-only, then verify the contract."""
    con = connect_readonly(db_path)
    assert_contract(con)
    return con
