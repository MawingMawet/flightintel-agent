"""get_schema: table schemas plus the grain sentence for each table.

The grain sentence travels WITH the columns so the model reads what one row
means at the moment it writes SQL (PHASE1_PLAN Q2).
"""

import sqlite3

from pydantic import BaseModel

from flightintel.db import EXPECTED_SCHEMA

GRAIN: dict[str, str] = {
    "flights": (
        "One row = one flight, keyed (icao24, first_seen). Silver: nulls in "
        "the estimated airport fields are KEPT here; candidate counts are "
        "soft quality signals, not filters."
    ),
    "od_hourly": (
        "One row = flights from origin O to destination D whose first_seen "
        "falls in UTC hour H. Gold: behind a hard null-OD gate (flights with "
        "unknown origin or destination are absent). Sparse: no zero rows. "
        "hour_utc is UTC, formatted 'YYYY-MM-DDTHH:00Z'; Bangkok local time "
        "is UTC+7."
    ),
    "states": (
        "One row = one position observation, keyed (icao24, last_contact). "
        "Silver: unrounded lat/lon kept, nulls kept, on-ground rows kept. "
        "Re-binning to any grid starts here, not from heatmap_hourly."
    ),
    "heatmap_hourly": (
        "One row = airborne observations in grid cell "
        "(round(lat,2), round(lon,2)) during UTC hour H. Gold: behind "
        "null-position and on-ground gates. TWO measures: obs_count counts "
        "position reports (weights dwell time); aircraft_count counts "
        "distinct aircraft. Distinct counts do NOT sum across cells or "
        "hours. Sparse: no zero rows. Rounded cells do not nest into "
        "coarser grids. hour_utc is UTC, formatted 'YYYY-MM-DDTHH:00Z'."
    ),
    "build_audit": (
        "One row = one flights-half build run. Append-only audit history: "
        "parse, dedup, and rejection counts plus output row counts."
    ),
    "build_audit_states": (
        "One row = one states-half build run. Append-only, SEPARATE from "
        "build_audit; auditing the whole platform requires reading both."
    ),
}


class ColumnInfo(BaseModel):
    name: str
    type: str


class TableSchema(BaseModel):
    table: str
    grain: str
    columns: list[ColumnInfo]


class SchemaReport(BaseModel):
    tables: list[TableSchema]


def get_schema(con: sqlite3.Connection) -> SchemaReport:
    tables = []
    for table in EXPECTED_SCHEMA:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        tables.append(
            TableSchema(
                table=table,
                grain=GRAIN[table],
                columns=[
                    ColumnInfo(name=r["name"], type=(r["type"] or "").upper())
                    for r in rows
                ],
            )
        )
    return SchemaReport(tables=tables)
