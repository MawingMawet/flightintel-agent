"""The committed sample.db must always satisfy the pinned contract; this is
what catches a stale sample after a contract change (PHASE1_PLAN Q6)."""

from pathlib import Path

import pytest

from flightintel.db import EXPECTED_SCHEMA, open_products_db

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "sample.db"


@pytest.mark.skipif(not SAMPLE_PATH.is_file(), reason="sample.db not built yet")
def test_sample_db_matches_contract_and_has_data():
    con = open_products_db(SAMPLE_PATH)
    try:
        for table in EXPECTED_SCHEMA:
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count > 0, f"{table} is empty in sample.db"
    finally:
        con.close()
