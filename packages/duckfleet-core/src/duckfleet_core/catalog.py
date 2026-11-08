"""The catalog: a DuckDB file derived from all cached profiles.

``build_catalog`` is the only writer. It writes ``catalog.duckdb.tmp``, closes
the connection and swaps the file in with ``os.replace``. The catalog is
always rebuilt from all profiles, never updated incrementally (D-003).
``CatalogReader`` opens a read-only connection inside every method and closes
it again: an open handle would block the next scan's ``os.replace`` on Windows.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from duckfleet_core.discovery import RejectedFile
from duckfleet_core.fingerprint import (
    SchemaDiff,
    diff_schemas,
    family_groups,
    schema_map_from_rows,
    table_variants,
)
from duckfleet_core.models import DatabaseProfile, ScanSummary
from duckfleet_core.tempdir import ensure_temp_dir, process_temp_dir

logger = logging.getLogger("duckfleet")

REPLACE_RETRIES = 5
REPLACE_DELAY_S = 0.5

SCHEMA_SQL = """
CREATE TABLE scan_runs (
    run_id VARCHAR, root VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
    duckfleet_version VARCHAR, duckdb_version VARCHAR, workers INTEGER,
    files_walked INTEGER, files_candidates INTEGER, files_rejected INTEGER,
    files_profiled INTEGER, files_reused INTEGER, files_failed INTEGER,
    truncated BOOLEAN, config_json VARCHAR
);
CREATE TABLE databases (
    rel_path VARCHAR PRIMARY KEY, file_name VARCHAR, abs_path VARCHAR,
    size_bytes BIGINT, mtime_ns BIGINT, has_wal BOOLEAN, storage_version UBIGINT,
    status VARCHAR, error_type VARCHAR, error_message VARCHAR,
    profiled_at TIMESTAMP, profile_seconds DOUBLE,
    n_schemas INTEGER, n_tables INTEGER, n_views INTEGER, total_rows BIGINT,
    fp_strict VARCHAR, fp_loose VARCHAR, family_strict VARCHAR, family_loose VARCHAR,
    time_min TIMESTAMP, time_max TIMESTAMP, tags_json VARCHAR, health_json VARCHAR
);
CREATE TABLE tables (
    rel_path VARCHAR, schema_name VARCHAR, table_name VARCHAR, kind VARCHAR,
    n_columns INTEGER, row_count BIGINT, estimated_size BIGINT,
    fp_strict VARCHAR, fp_loose VARCHAR, time_min TIMESTAMP, time_max TIMESTAMP,
    error VARCHAR
);
CREATE TABLE columns (
    rel_path VARCHAR, schema_name VARCHAR, table_name VARCHAR, ordinal INTEGER,
    column_name VARCHAR, data_type VARCHAR, nullable BOOLEAN,
    is_temporal BOOLEAN, is_numeric BOOLEAN, non_null_count BIGINT,
    min_value VARCHAR, max_value VARCHAR, approx_distinct BIGINT, sampled BOOLEAN
);
CREATE TABLE families (
    level VARCHAR, family_id VARCHAR, fingerprint VARCHAR, n_members INTEGER,
    n_tables INTEGER, canonical_json VARCHAR
);
CREATE TABLE table_variants (
    schema_name VARCHAR, table_name VARCHAR, fp_strict VARCHAR,
    n_members INTEGER, n_columns INTEGER, canonical_json VARCHAR
);
CREATE TABLE rejected (rel_path VARCHAR, reason VARCHAR);
"""


def _dt(value: str | None) -> datetime | None:
    """ISO 8601 string to a naive datetime (aware values are converted to UTC first)."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: Sequence[Sequence[Any]]) -> None:
    """Insert Python rows by binding one list per column and unnesting it.

    Roughly 7x faster than ``executemany`` or multi-row ``VALUES`` for the
    100k+ column rows of a big fleet (measured on 120k rows: 1.1 s against
    7-56 s), and needs no pandas (decision D-016).
    """
    if not rows:
        return
    types = [
        r[0]
        for r in con.execute(
            "SELECT data_type FROM duckdb_columns() WHERE table_name = ? AND NOT internal "
            "ORDER BY column_index",
            [table],
        ).fetchall()
    ]
    select = ", ".join(f"unnest(?::{data_type}[])" for data_type in types)
    columns = [list(column) for column in zip(*rows, strict=True)]
    con.execute(f"INSERT INTO {table} SELECT {select}", columns)


def _replace_with_retry(source: Path, target: Path) -> None:
    last: PermissionError | None = None
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:  # Windows: another process holds the target open
            last = exc
            if attempt < REPLACE_RETRIES:
                time.sleep(REPLACE_DELAY_S)
    raise PermissionError(
        f"Could not replace the catalog file {target}: it is held open by another process "
        f"(close the program reading it, then scan again)"
    ) from last


def _remove_file(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def build_catalog(
    profiles: list[DatabaseProfile],
    summary: ScanSummary,
    rejected: list[RejectedFile],
    catalog_path: Path,
    tmp_path: Path,
) -> None:
    """Build the catalog from ``profiles`` in ``tmp_path``, then swap it in as ``catalog_path``."""
    include_views = summary.config.include_views_in_fingerprint
    strict_groups = family_groups(profiles, strict=True, include_views=include_views)
    loose_groups = family_groups(profiles, strict=False, include_views=include_views)
    family_strict = {rel: g.family_id for g in strict_groups for rel in g.members}
    family_loose = {rel: g.family_id for g in loose_groups for rel in g.members}

    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_file(tmp_path)  # a stale build target from a crashed scan
    _remove_file(tmp_path.with_name(tmp_path.name + ".wal"))

    db_rows: list[list[Any]] = []
    table_rows: list[list[Any]] = []
    column_rows: list[list[Any]] = []
    for p in sorted(profiles, key=lambda p: p.rel_path):
        error = p.error
        db_rows.append(
            [
                p.rel_path, p.file_name, p.abs_path, p.size_bytes, p.mtime_ns, p.has_wal,
                p.storage_version, p.status, error.type if error else None,
                error.message if error else None, _dt(p.profiled_at), p.profile_seconds,
                len(p.schemas), sum(t.kind == "table" for t in p.tables),
                sum(t.kind == "view" for t in p.tables), p.total_rows, p.fp_strict, p.fp_loose,
                family_strict.get(p.rel_path), family_loose.get(p.rel_path),
                _dt(p.time_min), _dt(p.time_max), _json(p.tags), _json(p.health),
            ]
        )  # fmt: skip
        for t in p.tables:
            table_rows.append(
                [
                    p.rel_path, t.schema_name, t.name, t.kind, len(t.columns), t.row_count,
                    t.estimated_size, t.fp_strict, t.fp_loose, _dt(t.time_min), _dt(t.time_max),
                    t.error,
                ]
            )  # fmt: skip
            for c in t.columns:
                s = c.stats
                column_rows.append(
                    [
                        p.rel_path, t.schema_name, t.name, c.ordinal, c.name, c.data_type,
                        c.nullable, c.is_temporal, c.is_numeric,
                        s.non_null_count if s else None, s.min if s else None,
                        s.max if s else None, s.approx_distinct if s else None,
                        s.sampled if s else None,
                    ]
                )  # fmt: skip

    family_rows = [
        [g.level, g.family_id, g.fingerprint, len(g.members), g.n_tables, _json(g.canonical)]
        for g in (*strict_groups, *loose_groups)
    ]
    variant_rows = [
        [v.schema_name, v.table_name, v.fp_strict, v.n_members, v.n_columns, _json(v.columns)]
        for v in table_variants(profiles)
    ]
    run_row = [
        summary.run_id, summary.root, _dt(summary.started_at), _dt(summary.finished_at),
        summary.duckfleet_version, summary.duckdb_version, summary.workers,
        summary.files_walked, summary.files_candidates, summary.files_rejected,
        summary.files_profiled, summary.files_reused, summary.files_failed, summary.truncated,
        summary.config.model_dump_json(),
    ]  # fmt: skip

    temp_dir = ensure_temp_dir(process_temp_dir(summary.config.resolved_cache_dir()))  # V-01
    con = duckdb.connect(
        str(tmp_path),
        config={
            "threads": 2,
            "memory_limit": "1GB",
            "temp_directory": temp_dir,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        },
    )
    try:
        con.execute(SCHEMA_SQL)
        _insert(con, "scan_runs", [run_row])
        _insert(con, "databases", db_rows)
        _insert(con, "tables", table_rows)
        _insert(con, "columns", column_rows)
        _insert(con, "families", family_rows)
        _insert(con, "table_variants", variant_rows)
        _insert(con, "rejected", [[r.rel_path, r.reason] for r in rejected])
        con.execute("CHECKPOINT")
    finally:
        con.close()
    _replace_with_retry(tmp_path, catalog_path)


# ---------------------------------------------------------------- reading


class CatalogReader:
    """Thin SQL queries over the catalog. Every method opens and closes its own connection."""

    def __init__(self, catalog_path: Path | str):
        self.path = Path(catalog_path)

    @contextlib.contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(
            str(self.path),
            read_only=True,
            config={
                "threads": 2,
                "memory_limit": "1GB",
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            },
        )
        try:
            yield con
        finally:
            con.close()

    def _rows(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        with self._connect() as con:
            cursor = con.execute(sql, list(params or []))
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def exists(self) -> bool:
        return self.path.is_file()

    def df(self, sql: str, params: Sequence[Any] | None = None):
        """Run ``sql`` and return a pandas DataFrame (pandas is imported here, UI use only)."""
        import pandas  # noqa: F401  (duckdb needs it importable; keeps core free of it)

        with self._connect() as con:
            return con.execute(sql, list(params or [])).df()

    def scan_run(self) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1")
        return rows[0] if rows else None

    def databases(self) -> list[dict[str, Any]]:
        """One row per database; ``tags`` (dict) and ``health`` (list) replace the JSON columns."""
        rows = self._rows("SELECT * FROM databases ORDER BY rel_path")
        for row in rows:
            row["tags"] = json.loads(row.pop("tags_json") or "{}")
            row["health"] = json.loads(row.pop("health_json") or "[]")
        return rows

    def database(self, rel_path: str) -> dict[str, Any] | None:
        """The row of one database (``tags`` and ``health`` parsed), or ``None``."""
        rows = self._rows("SELECT * FROM databases WHERE rel_path = ?", [rel_path])
        if not rows:
            return None
        row = rows[0]
        row["tags"] = json.loads(row.pop("tags_json") or "{}")
        row["health"] = json.loads(row.pop("health_json") or "[]")
        return row

    def tables(self, rel_path: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tables"
        params: list[Any] = []
        if rel_path is not None:
            sql += " WHERE rel_path = ?"
            params.append(rel_path)
        return self._rows(sql + " ORDER BY rel_path, schema_name, table_name", params)

    def columns(
        self, rel_path: str, schema_name: str | None = None, table_name: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM columns WHERE rel_path = ?"
        params: list[Any] = [rel_path]
        if schema_name is not None:
            sql += " AND schema_name = ?"
            params.append(schema_name)
        if table_name is not None:
            sql += " AND table_name = ?"
            params.append(table_name)
        return self._rows(sql + " ORDER BY schema_name, table_name, ordinal", params)

    def families(self, level: str) -> list[dict[str, Any]]:
        """Families of ``level`` ("strict" or "loose"), largest first, with their members."""
        member_col = "family_strict" if level == "strict" else "family_loose"
        rows = self._rows(
            f"SELECT f.level, f.family_id, f.fingerprint, f.n_members, f.n_tables, "
            f"f.canonical_json, list(d.rel_path ORDER BY d.rel_path) AS members "
            f"FROM families f LEFT JOIN databases d ON d.{member_col} = f.family_id "
            f"WHERE f.level = ? "
            f"GROUP BY f.level, f.family_id, f.fingerprint, f.n_members, f.n_tables, "
            f"f.canonical_json "
            f"ORDER BY f.n_members DESC, try_cast(substr(f.family_id, 2) AS INTEGER)",
            [level],
        )
        for row in rows:
            row["canonical"] = json.loads(row.pop("canonical_json"))
        return rows

    def family_members(self, level: str, family_id: str) -> list[dict[str, Any]]:
        member_col = "family_strict" if level == "strict" else "family_loose"
        return self._rows(
            f"SELECT * FROM databases WHERE {member_col} = ? ORDER BY rel_path", [family_id]
        )

    def family_table_matrix(self, level: str) -> list[dict[str, Any]]:
        """Long format: for each family and table, how many members have that table."""
        member_col = "family_strict" if level == "strict" else "family_loose"
        return self._rows(
            f"SELECT d.{member_col} AS family_id, t.schema_name, t.table_name, "
            f"count(DISTINCT d.rel_path) AS n_members_with_table "
            f"FROM databases d JOIN tables t ON t.rel_path = d.rel_path AND t.kind = 'table' "
            f"WHERE d.{member_col} IS NOT NULL "
            f"GROUP BY d.{member_col}, t.schema_name, t.table_name "
            f"ORDER BY try_cast(substr(d.{member_col}, 2) AS INTEGER), "
            f"t.schema_name, t.table_name"
        )

    def table_variants(self) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM table_variants "
            "ORDER BY schema_name, table_name, n_members DESC, fp_strict"
        )
        for row in rows:
            row["columns"] = json.loads(row.pop("canonical_json"))
        return rows

    def timeline(self) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT rel_path, family_strict, time_min, time_max FROM databases "
            "WHERE time_min IS NOT NULL AND time_max IS NOT NULL ORDER BY time_min, rel_path"
        )

    def health(self) -> list[dict[str, Any]]:
        """Databases that failed or carry health flags."""
        rows = self._rows(
            "SELECT rel_path, status, error_type, error_message, health_json FROM databases "
            "WHERE status = 'error' OR health_json <> '[]' ORDER BY rel_path"
        )
        for row in rows:
            row["health"] = json.loads(row.pop("health_json") or "[]")
        return rows

    def rejected(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM rejected ORDER BY rel_path")

    def diff(self, rel_path_a: str, rel_path_b: str) -> SchemaDiff:
        """Structural difference between two databases (tables, columns, types)."""
        maps = []
        for rel_path in (rel_path_a, rel_path_b):
            maps.append(schema_map_from_rows(self.tables(rel_path), self.columns(rel_path)))
        return diff_schemas(maps[0], maps[1])
