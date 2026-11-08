"""The fleet connection: several databases of a scanned fleet in one sandboxed connection.

Plan v0.4 Section 3.1 and 3.2, decision D-040. ``open_fleet`` opens an in-memory DuckDB,
attaches the chosen files read-only (paths from the catalog), then turns external access
off and locks the configuration, then builds one union view per table name:
``main.events`` holds the ``events`` rows of every attached file with a ``database`` column
that carries the file's rel_path. A query or a plot against the view therefore spans files.

Every fleet connection is a separate DuckDB instance with a temp folder of its own
(``<cache>/tmp/<pid>/fleet-<8 hex>``, removed when the connection closes): two instances in
one process must never share a temp folder (review W-01). Handles are opened per request and
closed in ``finally``; nothing is cached. User SQL never runs before the configuration is
locked, and aliases and view names are built from file and table names and always quoted.

Type conflicts. ``UNION ALL BY NAME`` does not fail when the view is created, however
incompatible the column types are; it fails when rows are read (``Unimplemented type for
cast``), and neither ``LIMIT 0`` nor a probe with NULL constants shows it (verified on duckdb
1.5.6, DECISIONS D-044). A column whose types differ across the members and are not all
numeric or all temporal is therefore cast to ``VARCHAR`` in every branch up front and listed in
``UnionView.casts``.
"""

from __future__ import annotations

import contextlib
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, Field, field_validator

from duckfleet_core.profiler import classify_error as classify_attach_error
from duckfleet_core.query import (
    QueryConfig,
    QueryError,
    QueryResult,
    as_query_errors,
    execute_limited,
)
from duckfleet_core.sqlutil import qualified_name, quote_ident
from duckfleet_core.tempdir import ensure_fleet_temp_dir, remove_temp_dir

MAX_MEMBERS = 50  # L-12, L-22
MEMORY = "memory"  # the name of the in-memory database of a fleet connection (verified)
LABEL_COLUMN = "database"
FALLBACK_LABEL_COLUMN = "duckfleet_database"  # D-044: when a member table has its own `database`
_ALIAS_MAX = 48  # TUNING(alias-rule)
_RESERVED_ALIASES = frozenset({MEMORY, "system", "temp"})  # TUNING(alias-rule)
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")

# TUNING(union-casts): column types that DuckDB unifies without a cast in a union.
_NUMERIC_BASES = frozenset(
    {
        "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
        "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "DOUBLE", "DECIMAL",
    }
)  # fmt: skip
_TEMPORAL_BASES = frozenset(
    {"DATE", "TIMESTAMP", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "TIMESTAMP WITH TIME ZONE"}
)


# ---------------------------------------------------------------- selection and aliases


def alias_base(rel_path: str) -> str:
    """The alias of a rel_path before collisions are resolved (plan Section 3.1).

    The rel_path without its extension, lower-cased, every run of characters outside
    ``[a-z0-9]`` replaced by ``_``, ``d_`` in front of a leading digit, cut to 48 characters.
    ``exp_01/run_03/metrics.duckdb`` becomes ``exp_01_run_03_metrics``.
    """
    # TUNING(alias-rule)
    stem = posixpath.splitext(rel_path.replace("\\", "/"))[0]
    text = _NOT_ALNUM.sub("_", stem.lower()) or "db"
    if text[0].isdigit():
        text = "d_" + text
    return text[:_ALIAS_MAX]


class FleetSelection(BaseModel):
    """The databases of one fleet connection: 1 to ``MAX_MEMBERS`` distinct rel_paths."""

    rel_paths: list[str] = Field(min_length=1)

    @field_validator("rel_paths")
    @classmethod
    def _members(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_MEMBERS:
            raise ValueError(
                f"at most {MAX_MEMBERS} databases can be attached at once (got {len(value)})"
            )
        repeated = sorted({rel for rel in value if value.count(rel) > 1})
        if repeated:
            raise ValueError(f"databases listed more than once: {', '.join(repeated)}")
        return value

    def aliases(self) -> dict[str, str]:
        """``{alias: rel_path}`` in the order of ``rel_paths``.

        A collision (two rel_paths with the same base, or a base that is a reserved name such
        as ``memory``) gets ``_2``, ``_3``, ... in rel_path order, so the same files always get
        the same aliases whatever order they were chosen in.
        """
        # TUNING(alias-rule)
        taken: set[str] = set(_RESERVED_ALIASES)
        by_rel: dict[str, str] = {}
        for rel_path in sorted(self.rel_paths):
            base = alias_base(rel_path)
            alias, number = base, 1
            while alias in taken:
                number += 1
                alias = f"{base}_{number}"
            taken.add(alias)
            by_rel[rel_path] = alias
        return {by_rel[rel_path]: rel_path for rel_path in self.rel_paths}


# ---------------------------------------------------------------- results


@dataclass
class AttachFailure:
    """A member that could not be attached; the others still work."""

    rel_path: str
    alias: str
    kind: str  # as ProfileError.type: locked, encrypted, corrupt, missing, ...
    message: str


@dataclass
class UnionView:
    schema_name: str
    table_name: str
    members: list[str]  # rel_paths that have the table
    casts: list[str]  # columns cast to VARCHAR because their types differed
    columns: list[tuple[str, str]]  # (name, type) of the view, label column first
    label_column: str = LABEL_COLUMN  # D-044: duckfleet_database when a member has `database`

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def cast_names(self) -> set[str]:
        """The cast columns, lower-cased (DuckDB column names are case-insensitive)."""
        return {name.lower() for name in self.casts}


@dataclass
class FleetSummary:
    """What a fleet connection looked like, kept after the connection is closed."""

    aliases: dict[str, str]  # alias -> rel_path, attached members only
    failures: list[AttachFailure]
    views: list[UnionView]
    view_failures: list[tuple[str, str]] = field(default_factory=list)  # (schema.table, message)


@dataclass
class FleetHandle(FleetSummary):
    con: duckdb.DuckDBPyConnection | None = None

    def summary(self) -> FleetSummary:
        return FleetSummary(self.aliases, self.failures, self.views, self.view_failures)

    def view(self, schema_name: str, table_name: str) -> UnionView | None:
        for view in self.views:
            if (view.schema_name, view.table_name) == (schema_name, table_name):
                return view
        return None


# ---------------------------------------------------------------- the connection


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _attach_all(
    con: duckdb.DuckDBPyConnection, selection: FleetSelection, paths: dict[str, str]
) -> tuple[dict[str, str], list[AttachFailure]]:
    attached: dict[str, str] = {}
    failures: list[AttachFailure] = []
    for alias, rel_path in selection.aliases().items():
        try:
            target = _literal(str(paths[rel_path]))
            con.execute(f"ATTACH {target} AS {quote_ident(alias)} (READ_ONLY)")
        except duckdb.Error as exc:
            error = classify_attach_error(exc)
            kind = error.type
            if not Path(str(paths[rel_path])).exists():  # the wording differs per OS and per case
                kind = "missing"
            failures.append(AttachFailure(rel_path, alias, kind, error.message))
        else:
            attached[alias] = rel_path
    return attached, failures


def _lock_down(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")


@contextlib.contextmanager
def open_fleet(
    selection: FleetSelection,
    paths: dict[str, str],
    config: QueryConfig,
    *,
    views: bool = True,
) -> Iterator[FleetHandle]:
    """Open the fleet connection of ``selection`` (``paths``: rel_path -> absolute path).

    Raises ``QueryError`` when a member is unknown or none can be attached (kind ``other``).
    A member that fails to attach is listed in ``FleetHandle.failures``; the others work.
    """
    unknown = [rel for rel in selection.rel_paths if rel not in paths]
    if unknown:
        raise QueryError("sql", f"unknown database(s): {', '.join(unknown)}")
    leaf = ensure_fleet_temp_dir(config.resolved_temp_dir())
    con: duckdb.DuckDBPyConnection | None = None
    try:
        with as_query_errors():
            con = duckdb.connect(
                config={
                    "threads": config.resolved_threads(),
                    "memory_limit": f"{config.resolved_memory_limit_mb()}MB",
                    "autoinstall_known_extensions": False,
                    "autoload_known_extensions": False,
                    # set at connect time, before external access is turned off (the order of
                    # the options matters, see query.connection_config)
                    "temp_directory": leaf,
                }
            )
            attached, failures = _attach_all(con, selection, paths)
            if not attached:
                detail = "; ".join(f"{f.rel_path}: {f.kind}: {f.message}" for f in failures[:3])
                more = f" (and {len(failures) - 3} more)" if len(failures) > 3 else ""
                raise QueryError(
                    "other", f"none of the databases could be attached: {detail}{more}"
                )
            _lock_down(con)
            built: list[UnionView] = []
            view_failures: list[tuple[str, str]] = []
            if views:
                built, view_failures = _build_views(con, attached)
        yield FleetHandle(attached, failures, built, view_failures, con)
    finally:
        if con is not None:
            con.close()
        remove_temp_dir(leaf)


# ---------------------------------------------------------------- what the members hold


_TABLES_SQL = (
    "SELECT database_name, schema_name, table_name FROM duckdb_tables() "
    "WHERE NOT internal AND NOT temporary "
    "UNION SELECT database_name, schema_name, view_name FROM duckdb_views() "
    "WHERE NOT internal AND NOT temporary"
)
_COLUMNS_SQL = (
    "SELECT database_name, schema_name, table_name, column_name, data_type FROM duckdb_columns() "
    "WHERE NOT internal ORDER BY database_name, schema_name, table_name, column_index"
)


def fleet_tables(
    con: duckdb.DuckDBPyConnection, aliases: dict[str, str]
) -> dict[tuple[str, str], list[str]]:
    """``{(schema, table): [rel_path, ...]}``: which members hold which table or view.

    ``aliases`` maps the attached aliases to rel_paths; the members of a table come in the
    order of ``aliases``.
    """
    position = {alias: index for index, alias in enumerate(aliases)}
    found: dict[tuple[str, str], list[str]] = {}
    for database, schema, table in sorted(con.execute(_TABLES_SQL).fetchall()):
        if database in position:
            found.setdefault((schema, table), []).append(database)
    return {
        key: [aliases[alias] for alias in sorted(holders, key=position.__getitem__)]
        for key, holders in sorted(found.items())
    }


def _family(data_type: str) -> str | None:
    base = data_type.upper().split("(")[0].strip()
    if base in _NUMERIC_BASES:
        return "numeric"
    if base in _TEMPORAL_BASES:
        return "temporal"
    return None


def _needs_cast(types: set[str]) -> bool:
    """Whether the differing ``types`` of one column need a cast to VARCHAR (D-044)."""
    # TUNING(union-casts)
    if len(types) < 2:
        return False
    families = {_family(t) for t in types}
    return None in families or len(families) > 1


def _label_column(names: set[str]) -> str:
    """``database``, or ``duckfleet_database`` (with underscores in front if need be) if a member
    table has a column of that name: the union binder refuses duplicate names (D-044)."""
    lowered = {name.lower() for name in names}
    label = LABEL_COLUMN
    if label in lowered:
        label = FALLBACK_LABEL_COLUMN
        while label in lowered:
            label = "_" + label
    return label


def _view_sql(
    schema: str,
    table: str,
    holders: list[tuple[str, str]],
    label: str,
    columns_of: dict[tuple[str, str, str], list[tuple[str, str]]],
    cast_names: set[str],
) -> str:
    """``CREATE VIEW`` over the ``holders`` (alias, rel_path); every branch carries the label."""
    branches = []
    for alias, rel_path in holders:
        replace = [
            f"CAST({quote_ident(name)} AS VARCHAR) AS {quote_ident(name)}"
            for name, _type in columns_of[(alias, schema, table)]
            if name.lower() in cast_names
        ]
        star = "*" + (f" REPLACE ({', '.join(replace)})" if replace else "")
        # AS label on every branch: a branch without it yields NULL labels (verified)
        branches.append(
            f"SELECT {_literal(rel_path)} AS {quote_ident(label)}, {star} "
            f"FROM {qualified_name(alias, schema, table)}"
        )
    return f"CREATE VIEW {qualified_name(schema, table)} AS " + " UNION ALL BY NAME ".join(branches)


def _build_views(
    con: duckdb.DuckDBPyConnection, attached: dict[str, str]
) -> tuple[list[UnionView], list[tuple[str, str]]]:
    members = fleet_tables(con, attached)
    by_alias_of = {rel: alias for alias, rel in attached.items()}
    columns_of: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for database, schema, table, column, data_type in con.execute(_COLUMNS_SQL).fetchall():
        if database in attached:
            columns_of.setdefault((database, schema, table), []).append((column, data_type))

    views: list[UnionView] = []
    failures: list[tuple[str, str]] = []
    for (schema, table), rel_paths in members.items():
        holders = [(by_alias_of[rel], rel) for rel in rel_paths]
        types: dict[str, set[str]] = {}
        for alias, _rel in holders:
            for name, data_type in columns_of.get((alias, schema, table), []):
                types.setdefault(name.lower(), set()).add(data_type)
        label = _label_column({name for name in types})
        wanted = {name for name, kinds in types.items() if _needs_cast(kinds)}
        differing = {name for name, kinds in types.items() if len(kinds) > 1}
        if schema != "main":
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(schema)}")
        casts = wanted
        try:
            try:
                con.execute(_view_sql(schema, table, holders, label, columns_of, casts))
            except (duckdb.BinderException, duckdb.ConversionException):
                # safety net: cast every column whose type differs, then give up on this view
                casts = differing
                con.execute(f"DROP VIEW IF EXISTS {qualified_name(schema, table)}")
                con.execute(_view_sql(schema, table, holders, label, columns_of, casts))
        except duckdb.Error as exc:
            failures.append((f"{schema}.{table}", str(exc)[:500]))
            continue
        columns = [
            (str(name), str(data_type))
            for name, data_type in con.execute(
                "SELECT column_name, data_type FROM duckdb_columns() "
                "WHERE database_name = ? AND schema_name = ? AND table_name = ? "
                "ORDER BY column_index",
                [MEMORY, schema, table],
            ).fetchall()
        ]
        views.append(UnionView(schema, table, rel_paths, sorted(casts), columns, label))
    return views, failures


# ---------------------------------------------------------------- queries on the fleet


def describe_fleet(
    selection: FleetSelection, paths: dict[str, str], config: QueryConfig
) -> FleetSummary:
    """Open the fleet connection, read what it holds (union views, attach failures), close it."""
    with as_query_errors(), open_fleet(selection, paths, config) as fleet:
        return fleet.summary()


def run_fleet_query(
    selection: FleetSelection,
    paths: dict[str, str],
    sql: str,
    config: QueryConfig,
    *,
    max_rows: int | None = None,
) -> tuple[QueryResult, FleetSummary]:
    """Run ``sql`` (as given) on the fleet connection and fetch at most ``max_rows`` rows.

    The union views and the per-file aliases are visible to the SQL; the result comes with the
    summary of the connection (attach failures, views). Raises ``QueryError``.
    """
    limit = config.max_rows if max_rows is None else max_rows
    with as_query_errors(), open_fleet(selection, paths, config) as fleet:
        assert fleet.con is not None
        result = execute_limited(fleet.con, sql, limit, config.timeout_s)
        return result, fleet.summary()


def export_fleet_query(
    selection: FleetSelection,
    paths: dict[str, str],
    sql: str,
    fmt: Literal["csv", "parquet"],
    config: QueryConfig,
    *,
    max_rows: int | None = None,
) -> tuple[bytes, int, bool, FleetSummary]:
    """Like ``query.export_query`` on the fleet connection; also returns the summary."""
    from duckfleet_core import query

    query.require_export_format(fmt)
    cap = config.max_download_rows if max_rows is None else max_rows
    with as_query_errors(), open_fleet(selection, paths, config) as fleet:
        assert fleet.con is not None
        table = query.fetch_arrow(fleet.con, sql, cap, config.timeout_s)
        summary = fleet.summary()
    data, rows, truncated = query.write_arrow(table, fmt, cap)
    return data, rows, truncated, summary
