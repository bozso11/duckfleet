"""CLI commands that read one database of a scanned fleet: ``sql``, ``summarize``, ``preview``.

The database is named by its ``rel_path`` as listed by ``duckfleet report``; the
absolute path comes from the catalog. Exit codes: 0 ok, 1 usage (unknown
database or table, bad option), 2 fatal (no catalog, database failed in the
scan, file missing), 3 query error (kind and message on stderr).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from duckfleet_core.catalog import CatalogReader
from duckfleet_core.config import ScanConfig
from duckfleet_core.query import QueryConfig, QueryError, QueryResult, preview_table, run_query
from duckfleet_core.store import ProfileStore, SummaryStore
from duckfleet_core.summary import TableSummary
from duckfleet_core.tempdir import process_temp_dir

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FATAL = 2
EXIT_QUERY = 3

_MAX_CELL = 80  # table format only; csv and json keep the full value
_DEFAULT_PREVIEW_ROWS = 20
_SUMMARY_COLUMNS = [
    "column_name", "column_type", "min", "max", "approx_unique", "avg", "std",
    "q25", "q50", "q75", "count", "null_percentage",
]  # fmt: skip


class CliFailure(Exception):
    """A failure with its exit code and the message for stderr."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- parsers


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not value > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def add_parsers(sub: Any) -> None:
    """Register ``sql``, ``summarize`` and ``preview`` on the sub-parsers object."""
    sql = sub.add_parser("sql", help="run read-only SQL on one database of a scanned fleet")
    sql.add_argument("root")
    sql.add_argument("database", help="rel_path of the database, as listed by 'report'")
    sql.add_argument("sql", help="SQL text (several statements: the last result is shown)")
    sql.add_argument("--max-rows", type=_positive_int, help="rows shown (default 10000)")
    sql.add_argument("--timeout", type=_positive_float, help="seconds before the query is stopped")
    sql.add_argument("--format", choices=["table", "csv", "json"], default="table")
    sql.add_argument("--cache-dir")

    summarize = sub.add_parser("summarize", help="per-column summary (SUMMARIZE) of one table")
    summarize.add_argument("root")
    summarize.add_argument("database")
    summarize.add_argument("table", metavar="SCHEMA.TABLE")
    summarize.add_argument("--refresh", action="store_true", help="ignore the cached summary")
    summarize.add_argument("--format", choices=["table", "json"], default="table")
    summarize.add_argument("--timeout", type=_positive_float)
    summarize.add_argument("--cache-dir")

    preview = sub.add_parser("preview", help="show rows of one table")
    preview.add_argument("root")
    preview.add_argument("database")
    preview.add_argument("table", metavar="SCHEMA.TABLE")
    preview.add_argument(
        "--rows", type=_positive_int, default=_DEFAULT_PREVIEW_ROWS, help="rows (default 20)"
    )
    preview.add_argument("--offset", type=_non_negative_int, default=0)
    preview.add_argument("--order-by", metavar="COLUMN")
    preview.add_argument("--desc", action="store_true", help="sort descending")
    preview.add_argument("--columns", help="comma separated column names (default all)")
    preview.add_argument("--format", choices=["table", "csv", "json"], default="table")
    preview.add_argument("--timeout", type=_positive_float)
    preview.add_argument("--cache-dir")


# ---------------------------------------------------------------- lookups


def _store_for(args: argparse.Namespace) -> ProfileStore:
    cache_dir = ScanConfig(cache_dir=args.cache_dir).resolved_cache_dir()
    return ProfileStore(cache_dir, Path(args.root))


def _catalog(args: argparse.Namespace) -> tuple[ProfileStore, CatalogReader]:
    store = _store_for(args)
    if not store.catalog_path.is_file():
        raise CliFailure(
            EXIT_FATAL,
            f"no catalog for {store.root} in {store.cache_dir}; run: duckfleet scan {args.root}",
        )
    return store, CatalogReader(store.catalog_path)


def _clean_rel_path(text: str) -> str:
    rel = text.strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _database(reader: CatalogReader, text: str) -> dict[str, Any]:
    """The catalog row of the database ``text``; checks that it can be queried."""
    rel_path = _clean_rel_path(text)
    try:
        rows = reader.databases()
    except Exception as exc:
        raise CliFailure(EXIT_FATAL, f"cannot read the catalog {reader.path}: {exc}") from exc
    row = next((r for r in rows if r["rel_path"] == rel_path), None)
    if row is None:
        known = ", ".join(r["rel_path"] for r in rows[:8])
        more = f", ... ({len(rows)} in all)" if len(rows) > 8 else ""
        raise CliFailure(
            EXIT_USAGE, f"unknown database {rel_path!r} (the catalog lists: {known}{more})"
        )
    if row["status"] != "ok":
        raise CliFailure(
            EXIT_FATAL,
            f"{rel_path} could not be read in the last scan: {row['error_type']}: "
            f"{row['error_message']}",
        )
    if not Path(row["abs_path"]).is_file():
        raise CliFailure(
            EXIT_FATAL, f"{row['abs_path']} no longer exists (file missing since the last scan)"
        )
    return row


def _table(reader: CatalogReader, rel_path: str, text: str) -> dict[str, Any]:
    """The catalog row of ``SCHEMA.TABLE`` (names may contain dots; a bare name must be unique)."""
    tables = reader.tables(rel_path)
    exact = [t for t in tables if f"{t['schema_name']}.{t['table_name']}" == text]
    if not exact and "." not in text:
        exact = [t for t in tables if t["table_name"] == text]
    if len(exact) == 1:
        return exact[0]
    listing = ", ".join(f"{t['schema_name']}.{t['table_name']}" for t in tables[:12]) or "none"
    if len(exact) > 1:
        raise CliFailure(EXIT_USAGE, f"{text!r} is ambiguous in {rel_path}; use SCHEMA.TABLE")
    raise CliFailure(EXIT_USAGE, f"unknown table {text!r} in {rel_path} (tables: {listing})")


def _query_config(args: argparse.Namespace, **extra: Any) -> QueryConfig:
    # DuckDB's temp directory goes under the cache, never beside the scanned file (V-01).
    cache_dir = ScanConfig(cache_dir=getattr(args, "cache_dir", None)).resolved_cache_dir()
    options = {"temp_dir": str(process_temp_dir(cache_dir)), **extra}
    if getattr(args, "timeout", None):
        options["timeout_s"] = args.timeout
    return QueryConfig(**options)


# ---------------------------------------------------------------- output


def _cell(value: Any) -> str:
    text = "NULL" if value is None else str(value)
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\t", " ")
    return text if len(text) <= _MAX_CELL else text[: _MAX_CELL - 3] + "..."


def format_table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Aligned text table: a header, a rule and one line per row."""
    cells = [[_cell(value) for value in row] for row in rows]
    heads = [_cell(name) for name in columns]
    widths = [
        max([len(head), *(len(row[i]) for row in cells)]) for i, head in enumerate(heads)
    ]
    lines = [
        " | ".join(head.ljust(width) for head, width in zip(heads, widths, strict=True)).rstrip(),
        "-+-".join("-" * width for width in widths),
    ]
    lines += [
        " | ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)).rstrip()
        for row in cells
    ]
    return "\n".join(lines)


def _unique_names(columns: Sequence[str]) -> list[str]:
    seen: dict[str, int] = {}
    names = []
    for name in columns:
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names


def _json_value(value: Any) -> Any:
    """JSON-safe copy of a fetched value: NaN and infinities become null, bytes hex text.

    ``json.dumps(allow_nan=False)`` raises on a float before it ever calls ``default``, so the
    values are walked first (V-07). Tuples become lists; anything else unusual (datetime,
    Decimal, UUID) is left to ``default=str``.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _dump_json(value: Any) -> str:
    return json.dumps(_json_value(value), default=str, allow_nan=False, ensure_ascii=False)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list | tuple | dict):
        return _dump_json(value)  # arrays as JSON text, not as a Python tuple repr
    return value


def utf8_stdout() -> None:
    """Write csv and json as UTF-8 with LF line ends whatever the console code page (V-03)."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", newline="")
        except (ValueError, OSError):  # e.g. a stream that cannot change its encoding
            pass


def write_rows(result: QueryResult, fmt: str) -> None:
    """Print a result as ``table``, ``csv`` or ``json`` (one object per line) on stdout.

    ``csv`` and ``json`` are UTF-8 on every platform; ``table`` keeps the console encoding
    (set ``PYTHONUTF8=1`` on Windows to see every character).
    """
    if fmt in ("csv", "json"):
        utf8_stdout()
    if fmt == "csv":
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(result.columns)
        writer.writerows([[_csv_value(v) for v in row] for row in result.rows])
    elif fmt == "json":
        names = _unique_names(result.columns)
        for row in result.rows:
            print(_dump_json(dict(zip(names, row, strict=True))))
    else:
        print(format_table(result.columns, result.rows))
        if result.truncated:
            print("... (truncated)")
    if result.truncated and fmt != "table":
        print(f"duckfleet: warning: output truncated at {len(result.rows)} rows", file=sys.stderr)


def _query_failed(error: QueryError) -> int:
    print(f"duckfleet: query error ({error.kind}): {error.message}", file=sys.stderr, flush=True)
    return EXIT_QUERY


def _guarded(handler: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        try:
            return handler(args)
        except CliFailure as failure:
            print(f"duckfleet: error: {failure.message}", file=sys.stderr, flush=True)
            return failure.code
        except QueryError as error:
            return _query_failed(error)

    return run


# ---------------------------------------------------------------- commands


@_guarded
def cmd_sql(args: argparse.Namespace) -> int:
    _store, reader = _catalog(args)
    row = _database(reader, args.database)
    extra = {"max_rows": args.max_rows} if args.max_rows else {}
    result = run_query(row["abs_path"], args.sql, _query_config(args, **extra))
    write_rows(result, args.format)
    return EXIT_OK


@_guarded
def cmd_preview(args: argparse.Namespace) -> int:
    _store, reader = _catalog(args)
    row = _database(reader, args.database)
    table = _table(reader, row["rel_path"], args.table)
    columns = [c.strip() for c in args.columns.split(",") if c.strip()] if args.columns else None
    result = preview_table(
        row["abs_path"],
        table["schema_name"],
        table["table_name"],
        _query_config(args),
        columns=columns,
        order_by=args.order_by,
        descending=args.desc,
        offset=args.offset,
        limit=args.rows,
    )
    write_rows(result, args.format)
    return EXIT_OK


def summary_rows(summary: TableSummary) -> list[list[Any]]:
    return [[getattr(column, name) for name in _SUMMARY_COLUMNS] for column in summary.columns]


@_guarded
def cmd_summarize(args: argparse.Namespace) -> int:
    store, reader = _catalog(args)
    row = _database(reader, args.database)
    table = _table(reader, row["rel_path"], args.table)
    summaries = SummaryStore(store.cache_dir, store.root)
    config = _query_config(args)
    cached = None
    if not args.refresh:
        stat = Path(row["abs_path"]).stat()
        cached = summaries.load(
            row["rel_path"],
            table["schema_name"],
            table["table_name"],
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
    summary = cached or summaries.get_or_compute(
        row["abs_path"],
        row["rel_path"],
        table["schema_name"],
        table["table_name"],
        config,
        row_count=table["row_count"],
        refresh=True,
    )
    if summary.error:
        print(f"duckfleet: query error: {summary.error}", file=sys.stderr, flush=True)
        return EXIT_QUERY
    if cached is not None:
        print(f"(cached {summary.computed_at})", file=sys.stderr)
    else:
        print(f"(computed in {summary.elapsed_s:.2f} s)", file=sys.stderr)
    if summary.sampled:
        print(f"(sampled: {summary.sample_rows} of {summary.row_count} rows)", file=sys.stderr)
    if args.format == "json":
        print(summary.model_dump_json(indent=2))
    else:
        print(format_table(_SUMMARY_COLUMNS, summary_rows(summary)))
    return EXIT_OK
