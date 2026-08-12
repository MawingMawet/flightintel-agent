import sqlite3

import pytest

from flightintel.db import (
    SchemaDriftError,
    assert_contract,
    connect_readonly,
    open_products_db,
)
from tests.conftest import create_contract_db


def test_assertion_passes_on_conforming_db(con):
    assert_contract(con)


def test_open_products_db_returns_working_connection(contract_db_path):
    con = open_products_db(contract_db_path)
    assert con.execute("SELECT COUNT(*) FROM od_hourly").fetchone()[0] == 3


def test_missing_db_file_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        connect_readonly(tmp_path / "nope.db")


def test_connection_rejects_writes(con):
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO od_hourly VALUES ('X', 'Y', 'h', 1)")


def _mutate(path, ddl):
    con = sqlite3.connect(path)
    con.execute(ddl)
    con.commit()
    con.close()


def test_missing_table_is_drift(tmp_path):
    path = tmp_path / "drift.db"
    create_contract_db(path)
    _mutate(path, "DROP TABLE heatmap_hourly")
    with pytest.raises(SchemaDriftError, match="missing table: heatmap_hourly"):
        assert_contract(connect_readonly(path))


def test_missing_column_is_drift(tmp_path):
    path = tmp_path / "drift.db"
    create_contract_db(path)
    _mutate(path, "ALTER TABLE od_hourly DROP COLUMN flight_count")
    with pytest.raises(SchemaDriftError, match="missing column flight_count"):
        assert_contract(connect_readonly(path))


def test_extra_column_is_drift(tmp_path):
    path = tmp_path / "drift.db"
    create_contract_db(path)
    _mutate(path, "ALTER TABLE flights ADD COLUMN surprise TEXT")
    with pytest.raises(SchemaDriftError, match="unexpected column surprise"):
        assert_contract(connect_readonly(path))


def test_changed_type_is_drift(tmp_path):
    path = tmp_path / "drift.db"
    create_contract_db(path)
    _mutate(path, "ALTER TABLE states DROP COLUMN velocity")
    _mutate(path, "ALTER TABLE states ADD COLUMN velocity TEXT")
    with pytest.raises(SchemaDriftError, match="expected type REAL, found TEXT"):
        assert_contract(connect_readonly(path))


def test_extra_table_outside_contract_is_tolerated(tmp_path):
    path = tmp_path / "extra.db"
    create_contract_db(path)
    _mutate(path, "CREATE TABLE upstream_new_thing (id INTEGER)")
    assert_contract(connect_readonly(path))
