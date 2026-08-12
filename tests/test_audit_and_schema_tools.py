from flightintel.db import EXPECTED_SCHEMA
from flightintel.tools.audit import GetBuildAuditInput, get_build_audit
from flightintel.tools.schema import GRAIN, get_schema


def test_audit_returns_both_halves_newest_first(con):
    report = get_build_audit(con)
    assert [r.run_ts for r in report.flights_half] == [
        "2026-08-02T05:00:00Z",
        "2026-08-01T05:00:00Z",
    ]
    assert [r.run_ts for r in report.states_half] == [
        "2026-08-02T06:00:00Z",
        "2026-08-01T06:00:00Z",
    ]


def test_audit_limit_applies_per_half(con):
    report = get_build_audit(con, GetBuildAuditInput(limit=1))
    assert len(report.flights_half) == 1
    assert len(report.states_half) == 1


def test_audit_handles_null_stale_position_removed(con):
    report = get_build_audit(con)
    older = report.states_half[-1]
    assert older.stale_position_removed is None


def test_get_schema_covers_all_contract_tables_with_grain(con):
    report = get_schema(con)
    tables = {t.table: t for t in report.tables}
    assert set(tables) == set(EXPECTED_SCHEMA)
    for name, ts in tables.items():
        assert ts.grain == GRAIN[name]
        assert {c.name: c.type for c in ts.columns} == EXPECTED_SCHEMA[name]
