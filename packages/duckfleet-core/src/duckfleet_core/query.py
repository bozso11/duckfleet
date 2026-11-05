"""Read-only, sandboxed queries against one scanned database (plan v0.2 Section 3).

Every open of a scanned file inside the UI or CLI process goes through
``open_readonly`` with one fixed connection config (decision D-021): read-only,
extension autoload off, external access off, configuration locked, threads and
memory fixed. DuckDB refuses a second connection to the same file in the same
process when the config differs, so ``QueryConfig.threads`` and
``memory_limit_mb`` must not change while connections are open; ``max_rows``
and ``timeout_s`` are free to differ per call.

Every query runs in a one-thread executor under a timeout; on expiry the
connection is interrupted from the calling thread (decision D-024), which works
the same on Windows and Linux.

The session time zone is the machine's: DuckDB does not accept ``TimeZone`` in
the connect config, and a ``SET TimeZone`` before locking would only reach the
first connection of an instance (decision D-026).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import io
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import duckdb
import psutil
from pydantic import BaseModel, Field

from duckfleet_core.sqlutil import qualified_name, quote_ident

T = TypeVar("T")

_MAX_THREADS = 4
_MAX_MEMORY_MB = 4096  # TUNING(query-resources)
_MIN_MEMORY_MB = 512
_MESSAGE_LIMIT = 1_000

ErrorKind = Literal["sql", "read_only", "blocked", "timeout", "other"]


class QueryConfig(BaseModel):
    """Limits and resources of the query layer (docs/LIMITS.md L-08, L-09, L-14 to L-17)."""

    threads: int | None = Field(default=None, ge=1)  # L-17
    memory_limit_mb: int | None = Field(default=None, ge=64)  # L-17
    max_rows: int = Field(default=10_000, ge=1)  # L-09
    max_download_rows: int = Field(default=1_000_000, ge=1)  # L-09
    timeout_s: float = Field(default=60.0, gt=0)  # L-14
    preview_rows: int = Field(default=1_000, ge=1)  # L-08
    preview_max_columns: int = Field(default=50, ge=1)  # L-15
    stats_row_threshold: int = 20_000_000  # L-05, TUNING(stats-threshold)
    stats_sample_rows: int = 1_000_000  # L-06, TUNING(stats-threshold)

    def resolved_threads(self) -> int:
        """``threads``, or ``min(4, cpu_count)`` when unset."""
        # TUNING(query-resources)
        if self.threads is not None:
            return self.threads
        return max(1, min(_MAX_THREADS, os.cpu_count() or 1))

    def resolved_memory_limit_mb(self) -> int:
        """``memory_limit_mb``, or ``min(4096, total_ram_mb // 4)`` with a floor of 512."""
        # TUNING(query-resources)
        if self.memory_limit_mb is not None:
            return self.memory_limit_mb
        total_mb = psutil.virtual_memory().total // (1024 * 1024)
        return max(_MIN_MEMORY_MB, min(_MAX_MEMORY_MB, total_mb // 4))


class QueryError(Exception):
    """A failed query. ``kind`` tells the caller how to present it."""

    def __init__(self, kind: ErrorKind, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind: ErrorKind = kind
        self.message = message[:_MESSAGE_LIMIT]


class MissingDependencyError(RuntimeError):
    """An optional dependency is missing (``pyarrow`` for exports)."""


@dataclass
class QueryResult:
    columns: list[str]
    types: list[str]  # str(description[i][1]), e.g. "DECIMAL(18,3)"
    rows: list[tuple]  # at most max_rows (or the preview page size)
    truncated: bool  # more rows existed
    elapsed_s: float
    statements: int  # from con.extract_statements(sql)


# ---------------------------------------------------------------- connection


def connection_config(config: QueryConfig) -> dict[str, Any]:
    """The connect ``config`` of every connection to a scanned file (decision D-021)."""
    return {
        "threads": config.resolved_threads(),
        "memory_limit": f"{config.resolved_memory_limit_mb()}MB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "enable_external_access": False,
        "lock_configuration": True,
    }


@contextlib.contextmanager
def open_readonly(
    db_path: str | Path, config: QueryConfig
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open ``db_path`` read-only under the sandbox config and close it afterwards."""
    con = duckdb.connect(str(db_path), read_only=True, config=connection_config(config))
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------- errors


# Case-insensitive substrings of DuckDB's messages, checked in this order (plan 3.3).
_MISSING = ("database does not exist", "no such file")  # "Cannot open ... in read-only mode: ..."
_READ_ONLY = ("read-only mode",)
_BLOCKED = ("disabled by configuration", "configuration has been locked", "extensions is disabled")
_SQL_EXCEPTIONS = (
    duckdb.ParserException,
    duckdb.BinderException,
    duckdb.CatalogException,
    duckdb.ConversionException,
    duckdb.InvalidInputException,
)


def classify_error(exc: BaseException) -> QueryError:
    """Map an exception raised while running a query to a ``QueryError``."""
    if isinstance(exc, QueryError):
        return exc
    text = str(exc) or type(exc).__name__
    lower = text.lower()
    kind: ErrorKind
    if isinstance(exc, duckdb.InterruptException):
        kind = "timeout"
    elif any(s in lower for s in _MISSING):
        kind = "other"  # opening a missing file also mentions read-only mode
    elif any(s in lower for s in _READ_ONLY):
        kind = "read_only"
    elif any(s in lower for s in _BLOCKED):
        kind = "blocked"
    elif isinstance(exc, _SQL_EXCEPTIONS):
        kind = "sql"
    else:
        kind = "other"
    return QueryError(kind, text)


@contextlib.contextmanager
def as_query_errors() -> Iterator[None]:
    """Turn every DuckDB failure in the block into a ``QueryError``."""
    try:
        yield
    except QueryError:
        raise
    except duckdb.Error as exc:
        raise classify_error(exc) from exc


# ---------------------------------------------------------------- timeout


def _with_timeout(con: duckdb.DuckDBPyConnection, fn: Callable[[], T], timeout_s: float) -> T:
    """Run ``fn`` (which uses ``con``) in a worker thread; interrupt ``con`` after ``timeout_s``."""
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="duckfleet-query"
    )
    try:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            con.interrupt()
            try:
                return future.result()  # the thread ends once the interrupt is noticed
            except duckdb.InterruptException:
                raise QueryError(
                    "timeout", f"the query ran longer than {timeout_s:g} s and was interrupted"
                ) from None
    finally:
        executor.shutdown(wait=True)


# ---------------------------------------------------------------- run_query


def execute_limited(
    con: duckdb.DuckDBPyConnection, sql: str, max_rows: int, timeout_s: float
) -> QueryResult:
    started = time.perf_counter()
    statements = len(con.extract_statements(sql))  # a parser error is a DuckDB error
    if statements == 0:
        raise QueryError("sql", "no SQL statement to run")

    def work() -> tuple[list[str], list[str], list[tuple], bool]:
        cursor = con.execute(sql)
        if cursor.description is None:  # a statement without a result set
            return [], [], [], False
        names = [str(d[0]) for d in cursor.description]
        types = [str(d[1]) for d in cursor.description]
        rows = cursor.fetchmany(max_rows)
        truncated = len(rows) == max_rows and bool(cursor.fetchmany(1))
        return names, types, rows, truncated

    names, types, rows, truncated = _with_timeout(con, work, timeout_s)
    return QueryResult(
        columns=names,
        types=types,
        rows=rows,
        truncated=truncated,
        elapsed_s=time.perf_counter() - started,
        statements=statements,
    )


def run_query(
    db_path: str | Path, sql: str, config: QueryConfig, *, max_rows: int | None = None
) -> QueryResult:
    """Run ``sql`` (as given, never modified) and fetch at most ``max_rows`` rows.

    Several statements run in order and the last result is returned. Raises
    ``QueryError``.
    """
    limit = config.max_rows if max_rows is None else max_rows
    with as_query_errors(), open_readonly(db_path, config) as con:
        return execute_limited(con, sql, limit, config.timeout_s)


# ---------------------------------------------------------------- export_query


def export_query(
    db_path: str | Path,
    sql: str,
    fmt: Literal["csv", "parquet"],
    config: QueryConfig,
    *,
    max_rows: int | None = None,
) -> tuple[bytes, int, bool]:
    """Run ``sql`` and return ``(file bytes, rows written, truncated)``.

    Built in memory through Arrow, cut at ``max_rows`` (default
    ``config.max_download_rows``). The SQL text is not modified; the cap is a
    ``LIMIT`` on the relation. Needs ``pyarrow`` (extra ``export``).
    """
    if fmt not in ("csv", "parquet"):
        raise ValueError(f"unknown export format: {fmt!r}")
    try:
        import pyarrow.csv as pa_csv
        import pyarrow.parquet as pa_parquet
    except ImportError as exc:
        raise MissingDependencyError(
            "exporting needs pyarrow: install it with: pip install 'duckfleet-core[export]'"
        ) from exc

    cap = config.max_download_rows if max_rows is None else max_rows
    with as_query_errors(), open_readonly(db_path, config) as con:

        def work() -> Any:
            relation = con.sql(sql)
            if relation is None:
                raise QueryError("sql", "the statement returns no rows")
            limited = relation.limit(cap + 1)
            to_table = getattr(limited, "to_arrow_table", None) or limited.fetch_arrow_table
            return to_table()

        table = _with_timeout(con, work, config.timeout_s)

    truncated = table.num_rows > cap
    if truncated:
        table = table.slice(0, cap)
    buffer = io.BytesIO()
    try:
        if fmt == "parquet":
            pa_parquet.write_table(table, buffer)
        else:
            pa_csv.write_csv(table, buffer)
    except Exception as exc:  # pyarrow raises ArrowInvalid / ArrowNotImplementedError
        hint = "; download Parquet instead" if fmt == "csv" else ""
        raise QueryError("other", f"cannot write the result as {fmt}: {exc}{hint}") from exc
    return buffer.getvalue(), table.num_rows, truncated


# ---------------------------------------------------------------- tables and previews


def database_name(con: duckdb.DuckDBPyConnection) -> str:
    row = con.execute("select database_name from duckdb_databases() where not internal").fetchone()
    if row is None:
        raise QueryError("other", "the file holds no database")
    return str(row[0])


def _table_columns(
    con: duckdb.DuckDBPyConnection, db_name: str, schema_name: str, table_name: str
) -> list[tuple[str, str]]:
    rows = con.execute(
        "select column_name, data_type from duckdb_columns() "
        "where database_name = ? and schema_name = ? and table_name = ? and not internal "
        "order by column_index",
        [db_name, schema_name, table_name],
    ).fetchall()
    return [(str(name), str(data_type)) for name, data_type in rows]


def table_columns(
    db_path: str | Path, schema_name: str, table_name: str, config: QueryConfig
) -> list[tuple[str, str]]:
    """``(column name, type)`` of a table or view, in table order; empty when it does not exist."""
    with as_query_errors(), open_readonly(db_path, config) as con:
        return _table_columns(con, database_name(con), schema_name, table_name)


def preview_table(
    db_path: str | Path,
    schema_name: str,
    table_name: str,
    config: QueryConfig,
    *,
    columns: list[str] | None = None,  # None = all
    order_by: str | None = None,
    descending: bool = False,
    offset: int = 0,
    limit: int | None = None,
) -> QueryResult:
    """One page of a table: ``SELECT <columns> FROM "db"."schema"."table" [ORDER BY ...]``.

    ``columns`` and ``order_by`` must be names of the table, otherwise
    ``QueryError(kind="sql")``. ``limit`` defaults to ``config.preview_rows``;
    ``truncated`` says that more rows follow the page.
    """
    if offset < 0:
        raise ValueError("offset must not be negative")
    page = config.preview_rows if limit is None else limit
    with as_query_errors(), open_readonly(db_path, config) as con:
        db_name = database_name(con)
        known = _table_columns(con, db_name, schema_name, table_name)
        if not known:
            raise QueryError("sql", f"unknown table or view {schema_name}.{table_name}")
        names = {name for name, _type in known}
        wanted = [name for name, _type in known] if columns is None else list(columns)
        used = [*wanted, *([order_by] if order_by else [])]
        unknown = [name for name in used if name not in names]
        if unknown:
            raise QueryError(
                "sql", f"unknown column(s) in {schema_name}.{table_name}: {', '.join(unknown)}"
            )
        if not wanted:
            raise QueryError("sql", "no columns selected")
        select = "*" if columns is None else ", ".join(quote_ident(name) for name in wanted)
        sql = f"SELECT {select} FROM {qualified_name(db_name, schema_name, table_name)}"
        if order_by:
            sql += f" ORDER BY {quote_ident(order_by)} {'DESC' if descending else 'ASC'}"
        sql += f" LIMIT {page + 1} OFFSET {offset}"
        return execute_limited(con, sql, page, config.timeout_s)
