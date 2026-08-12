from flightintel.tools.sql import RunSqlInput, run_sql


def test_simple_select(con):
    r = run_sql(con, RunSqlInput(sql="SELECT origin, SUM(flight_count) AS n FROM od_hourly GROUP BY 1 ORDER BY 1"))
    assert r.error is None
    assert r.columns == ["origin", "n"]
    assert r.rows == [["VTBD", 1], ["VTBS", 6]]
    assert r.truncated is False


def test_trailing_semicolon_is_fine(con):
    r = run_sql(con, RunSqlInput(sql="SELECT 1;"))
    assert r.error is None
    assert r.rows == [[1]]


def test_cte_allowed(con):
    r = run_sql(con, RunSqlInput(sql="WITH t AS (SELECT flight_count FROM od_hourly) SELECT SUM(flight_count) FROM t"))
    assert r.error is None
    assert r.rows == [[7]]


def test_insert_rejected(con):
    r = run_sql(con, RunSqlInput(sql="INSERT INTO od_hourly VALUES ('A','B','h',1)"))
    assert r.error is not None
    assert "read-only" in r.error


def test_multiple_statements_rejected(con):
    r = run_sql(con, RunSqlInput(sql="SELECT 1; SELECT 2"))
    assert r.error is not None
    assert "one" in r.error.lower()


def test_empty_statement_rejected(con):
    r = run_sql(con, RunSqlInput(sql="   ;  "))
    assert r.error is not None


def test_syntax_error_returned_not_raised(con):
    r = run_sql(con, RunSqlInput(sql="SELECT frm od_hourly"))
    assert r.error is not None
    assert "SQL error" in r.error


def test_row_limit_truncates(con):
    r = run_sql(
        con,
        RunSqlInput(
            sql="WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 10) SELECT x FROM c",
            max_rows=5,
        ),
    )
    assert r.error is None
    assert r.row_count == 5
    assert r.truncated is True


def test_timeout_aborts_runaway_query(con):
    r = run_sql(
        con,
        RunSqlInput(
            sql="WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 100000000) SELECT COUNT(*) FROM c"
        ),
        timeout_seconds=0.1,
    )
    assert r.error is not None
    assert "time limit" in r.error


def test_connection_usable_after_timeout(con):
    run_sql(
        con,
        RunSqlInput(
            sql="WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 100000000) SELECT COUNT(*) FROM c"
        ),
        timeout_seconds=0.1,
    )
    r = run_sql(con, RunSqlInput(sql="SELECT 1"))
    assert r.error is None
    assert r.rows == [[1]]
