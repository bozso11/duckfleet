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

from duckfleet_core.config import default_cache_dir
from duckfleet_core.sqlutil import qualified_name, quote_ident
from duckfleet_core.tempdir import ensure_file_temp_dir, process_temp_dir

T = TypeVar("T")

_MAX_THREADS = 4
_MAX_MEMORY_MB = 4096  # TUNING(query-resources)
_MIN_MEMORY_MB = 512
_MESSAGE_LIMIT = 1_000
_POLL_S = 0.25  # wait slice of the timeout watcher

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
    # The per-process root of DuckDB's temp directories (V-01, D-034): None means
    # <default cache>/tmp/<pid>; the CLI and the UI pass <their cache dir>/tmp/<pid>. Each file
    # gets a leaf of its own below it (W-01, D-042), see tempdir.file_temp_dir.
    temp_dir: str | None = None

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

    def resolved_temp_dir(self) -> Path:
        """``temp_dir``, or ``<default cache dir>/tmp/<pid>`` when unset."""
        if self.temp_dir:
            return Path(self.temp_dir)
        return process_temp_dir(default_cache_dir())


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


def connection_config(config: QueryConfig, db_path: str | Path) -> dict[str, Any]:
    """The connect ``config`` of every connection to the scanned file ``db_path`` (D-021, D-042).

    ``temp_directory`` is the leaf of this file under the process folder
    (``tempdir.file_temp_dir``), created here before connecting: without it DuckDB would spill
    into ``<database path>.tmp`` beside the scanned file (review V-01), and two DuckDB
    instances (two files) must never share one folder (review W-01). The key order matters
    (see the comment below).
    """
    return {
        "threads": config.resolved_threads(),
        "memory_limit": f"{config.resolved_memory_limit_mb()}MB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "enable_external_access": False,
        # DuckDB applies the options in this order and refuses to change the temp directory once
        # external access is off, so this key must stay after "enable_external_access".
        "temp_directory": ensure_file_temp_dir(config.resolved_temp_dir(), db_path),
        "lock_configuration": True,
    }


@contextlib.contextmanager
def open_readonly(
    db_path: str | Path, config: QueryConfig
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open ``db_path`` read-only under the sandbox config and close it afterwards."""
    con = duckdb.connect(
        str(db_path), read_only=True, config=connection_config(config, db_path)
    )
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------- errors


# Case-insensitive substrings of DuckDB's messages (plan 3.3). The exception type decides first
# (review V-06), so text echoed from the user's SQL cannot set the kind: only an
# InvalidInputException is looked at for its wording.
_MISSING = ("database does not exist", "no such file")  # "Cannot open ... in read-only mode: ..."
_BLOCKED = ("disabled by configuration", "configuration has been locked", "extensions is disabled")
_INTERRUPTED = "interrupt error"  # DuckDB's label: "INTERRUPT Error: Interrupted!"
_SQL_EXCEPTIONS = (
    duckdb.ParserException,
    duckdb.BinderException,
    duckdb.CatalogException,
    duckdb.ConversionException,
)


def classify_error(exc: BaseException) -> QueryError:
    """Map an exception raised while running a query to a ``QueryError``."""
    if isinstance(exc, QueryError):
        return exc
    text = str(exc) or type(exc).__name__
    lower = text.lower()
    kind: ErrorKind
    if isinstance(exc, duckdb.InterruptException) or (
        isinstance(exc, duckdb.InvalidInputException) and _INTERRUPTED in lower
    ):
        # a fetch interrupted mid-stream arrives as an InvalidInputException (V-02); the wording
        # is looked at for that type only, so SQL text echoed in another error cannot set the
        # kind (W-07: ``select 'interrupt error'::INTEGER`` is a ConversionException)
        kind = "timeout"
    elif isinstance(exc, duckdb.PermissionException):
        kind = "blocked"
    elif isinstance(exc, duckdb.InvalidInputException):
        if any(s in lower for s in _MISSING):
            kind = "other"  # opening a missing file also mentions read-only mode
        elif lower.startswith("invalid input error: cannot execute statement") or (
            "read-only mode" in lower
        ):
            kind = "read_only"
        elif any(s in lower for s in _BLOCKED):
            kind = "blocked"
        else:
            kind = "sql"
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


def _wait(future: concurrent.futures.Future, timeout_s: float) -> bool:
    """Wait up to ``timeout_s`` for ``future``; ``True`` when it finished.

    Waits in short slices: on Windows a lock wait is not interrupted by Ctrl-C, so one long wait
    would only deliver the ``KeyboardInterrupt`` when it ends (V-04).
    """
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return future.done()
        done, _pending = concurrent.futures.wait([future], timeout=min(_POLL_S, remaining))
        if done:
            return True


def _with_timeout(con: duckdb.DuckDBPyConnection, fn: Callable[[], T], timeout_s: float) -> T:
    """Run ``fn`` (which uses ``con``) in a worker thread; interrupt ``con`` after ``timeout_s``.

    After an interrupt every DuckDB error of the worker is a timeout (V-02): a fetch that is
    interrupted mid-stream does not always raise ``InterruptException``. A ``KeyboardInterrupt``
    (or any other ``BaseException``) while waiting interrupts the query too (V-04), so
    ``shutdown(wait=True)`` returns at once instead of waiting for the whole query.
    """
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="duckfleet-query"
    )
    try:
        future = executor.submit(fn)
        try:
            if _wait(future, timeout_s):
                return future.result()
            con.interrupt()
            try:
                return future.result()  # the thread ends once the interrupt is noticed
            except duckdb.Error:
                raise QueryError(
                    "timeout", f"the query ran longer than {timeout_s:g} s and was interrupted"
                ) from None
        except Exception:
            raise  # an error (or the timeout above): nothing is left running that needs stopping
        except BaseException:  # KeyboardInterrupt and friends while waiting
            con.interrupt()
            raise
    finally:
        executor.shutdown(wait=True)


# ---------------------------------------------------------------- run_query


def execute_limited(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    max_rows: int,
    timeout_s: float,
    *,
    params: list[Any] | None = None,
) -> QueryResult:
    """Run ``sql`` (with optional bound ``params``) under the timeout and fetch ``max_rows``."""
    started = time.perf_counter()
    statements = len(con.extract_statements(sql))  # a parser error is a DuckDB error
    if statements == 0:
        raise QueryError("sql", "no SQL statement to run")

    def work() -> tuple[list[str], list[str], list[tuple], bool]:
        cursor = con.execute(sql, params) if params else con.execute(sql)
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


_DATABASE_NAME_SQL = "select database_name from duckdb_databases() where not internal"
_COLUMNS_SQL = (
    "select column_name, data_type from duckdb_columns() "
    "where database_name = ? and schema_name = ? and table_name = ? and not internal "
    "order by column_index"
)


def database_name(con: duckdb.DuckDBPyConnection, *, timeout_s: float | None = None) -> str:
    """The name of the one database of a scanned file; under ``timeout_s`` when given (W-10)."""
    if timeout_s is None:
        row = con.execute(_DATABASE_NAME_SQL).fetchone()
    else:
        rows = execute_limited(con, _DATABASE_NAME_SQL, 1, timeout_s).rows
        row = rows[0] if rows else None
    if row is None:
        raise QueryError("other", "the file holds no database")
    return str(row[0])


def _table_columns(
    con: duckdb.DuckDBPyConnection,
    db_name: str,
    schema_name: str,
    table_name: str,
    *,
    timeout_s: float | None = None,
) -> list[tuple[str, str]]:
    """``(column, type)`` of a table or view; under ``timeout_s`` when given (W-10)."""
    params = [db_name, schema_name, table_name]
    if timeout_s is None:
        rows = con.execute(_COLUMNS_SQL, params).fetchall()
    else:
        rows = execute_limited(con, _COLUMNS_SQL, 100_000, timeout_s, params=params).rows
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
