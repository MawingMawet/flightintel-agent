"""run_sql: guarded SELECT-only execution against the read-only products.db.

Guard layers, outermost first: the connection itself is mode=ro plus
query_only (the engine refuses writes), the statement allowlist gives the
model a clear early error instead of an engine error, and row/time limits
bound the cost of a runaway query. Execution failures are returned in the
result, not raised: the agent loop feeds them back so the model can correct
its own SQL (PHASE1_PLAN Q4).
"""

import sqlite3
import time

from pydantic import BaseModel, Field

MAX_ROWS_CAP = 200
DEFAULT_TIMEOUT_SECONDS = 5.0
_ALLOWED_FIRST_KEYWORDS = ("SELECT", "WITH")


class RunSqlInput(BaseModel):
    sql: str = Field(description="A single read-only SELECT statement.")
    max_rows: int = Field(default=50, ge=1, le=MAX_ROWS_CAP)


class SqlResult(BaseModel):
    columns: list[str] = []
    rows: list[list] = []
    row_count: int = 0
    truncated: bool = False
    error: str | None = None


def _reject(reason: str) -> SqlResult:
    return SqlResult(error=reason)


def run_sql(
    con: sqlite3.Connection,
    params: RunSqlInput,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> SqlResult:
    sql = params.sql.strip().rstrip(";").strip()
    if not sql:
        return _reject("Empty SQL statement.")
    if ";" in sql:
        return _reject(
            "Multiple statements are not allowed; send exactly one SELECT."
        )
    first_word = sql.split(None, 1)[0].upper()
    if first_word not in _ALLOWED_FIRST_KEYWORDS:
        return _reject(
            f"Only read-only queries are allowed (statement starts with "
            f"{first_word}; expected SELECT or WITH)."
        )

    deadline = time.monotonic() + timeout_seconds
    # The progress handler runs every N SQLite VM ops; returning nonzero
    # aborts the query with an 'interrupted' error.
    con.set_progress_handler(
        lambda: 1 if time.monotonic() > deadline else 0, 10_000
    )
    try:
        cur = con.execute(sql)
        fetched = cur.fetchmany(params.max_rows + 1)
        truncated = len(fetched) > params.max_rows
        rows = [list(r) for r in fetched[: params.max_rows]]
        columns = [d[0] for d in cur.description] if cur.description else []
        return SqlResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            return _reject(
                f"Query exceeded the {timeout_seconds:.0f}s time limit; "
                "narrow it (filter or aggregate) and retry."
            )
        return _reject(f"SQL error: {exc}")
    except sqlite3.Error as exc:
        return _reject(f"SQL error: {exc}")
    finally:
        con.set_progress_handler(None, 0)
