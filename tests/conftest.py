import sqlite3
from pathlib import Path

import pytest

from flightintel.db import EXPECTED_SCHEMA, connect_readonly


def create_contract_db(path: Path) -> None:
    con = sqlite3.connect(path)
    for table, cols in EXPECTED_SCHEMA.items():
        col_defs = ", ".join(f"{name} {ctype}" for name, ctype in cols.items())
        con.execute(f"CREATE TABLE {table} ({col_defs})")
    con.executemany(
        "INSERT INTO od_hourly VALUES (?, ?, ?, ?)",
        [
            ("VTBS", "VHHH", "2026-08-01T03:00Z", 4),
            ("VTBS", "VHHH", "2026-08-01T04:00Z", 2),
            ("VTBD", "VTCC", "2026-08-01T03:00Z", 1),
        ],
    )
    con.executemany(
        "INSERT INTO build_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2026-08-01T05:00:00Z", 10, 900, 12, 1, 30, 40, 800, 120),
            ("2026-08-02T05:00:00Z", 12, 1100, 15, 0, 35, 45, 1000, 150),
        ],
    )
    con.executemany(
        "INSERT INTO build_audit_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2026-08-01T06:00:00Z", 5, 2000, 100, 50, 200, 1650, 900, None),
            ("2026-08-02T06:00:00Z", 6, 2400, 120, 60, 240, 1980, 1100, 7),
        ],
    )
    con.commit()
    con.close()


@pytest.fixture
def contract_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample_products.db"
    create_contract_db(path)
    return path


@pytest.fixture
def con(contract_db_path: Path) -> sqlite3.Connection:
    connection = connect_readonly(contract_db_path)
    yield connection
    connection.close()
