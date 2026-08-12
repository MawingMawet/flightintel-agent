"""get_build_audit: recent build runs from BOTH audit tables, per half.

The two halves audit different gates (flights vs states); a report that
reads only one half is wrong by construction, so the tool returns both
every time (PHASE1_PLAN deliverable 2).
"""

import sqlite3

from pydantic import BaseModel, Field


class FlightsBuildRun(BaseModel):
    run_ts: str
    files_read: int
    records_parsed: int
    duplicates_removed: int
    conflicting_duplicates: int
    rejected_null_departure: int
    rejected_null_arrival: int
    rows_flights: int
    rows_od_hourly: int


class StatesBuildRun(BaseModel):
    run_ts: str
    files_read: int
    records_parsed: int
    duplicates_removed: int
    rejected_null_position: int
    rejected_on_ground: int
    stale_position_removed: int | None
    rows_states: int
    rows_heatmap_hourly: int


class GetBuildAuditInput(BaseModel):
    limit: int = Field(default=5, ge=1, le=50, description="Runs per half.")


class BuildAuditReport(BaseModel):
    flights_half: list[FlightsBuildRun]
    states_half: list[StatesBuildRun]


def get_build_audit(
    con: sqlite3.Connection, params: GetBuildAuditInput | None = None
) -> BuildAuditReport:
    params = params or GetBuildAuditInput()
    flights = con.execute(
        "SELECT * FROM build_audit ORDER BY run_ts DESC LIMIT ?",
        (params.limit,),
    ).fetchall()
    states = con.execute(
        "SELECT * FROM build_audit_states ORDER BY run_ts DESC LIMIT ?",
        (params.limit,),
    ).fetchall()
    return BuildAuditReport(
        flights_half=[FlightsBuildRun(**dict(r)) for r in flights],
        states_half=[StatesBuildRun(**dict(r)) for r in states],
    )
