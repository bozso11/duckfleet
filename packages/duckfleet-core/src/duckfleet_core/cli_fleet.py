"""CLI commands on several databases at once: ``fleet-tables`` and ``fleet-sql`` (plan v0.4).

The databases are chosen with ``--databases a,b`` (rel_paths as listed by ``duckfleet report``;
repeat the option for more, and a value that is exactly one rel_path is taken whole, so a
rel_path with a comma in it can be chosen, review P-12), ``--family F1`` (a strict family
``F1``... or a loose one ``L1``...) or ``--all`` (every database of the catalog, also those
whose last scan failed: attaching them shows why); without one of them the default is
``--all``. More than 50 databases (L-12, L-22) is a usage error. The paths come from the
catalog. A member that cannot be attached is reported on stderr and the others work, also when
the query fails (review X-06); ``fleet-sql`` runs on the fleet connection of
``duckfleet_core.fleet`` with its union views.

Exit codes as ``sql``: 0 ok, 1 usage, 2 fatal, 3 query error.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from duckfleet_core.catalog import CatalogReader
from duckfleet_core.cli_query import (
    EXIT_OK,
    EXIT_USAGE,
    CliFailure,
    _catalog,
    _clean_rel_path,
    _guarded,
    _positive_float,
    _positive_int,
    _query_config,
    write_rows,
)
from duckfleet_core.fleet import (
    MAX_MEMBERS,
    FleetQueryError,
    FleetSelection,
    FleetSummary,
    describe_fleet,
    run_fleet_query,
)
from duckfleet_core.query import QueryResult


def add_selection_options(parser: argparse.ArgumentParser, *, with_all: bool = True) -> None:
    """``--databases``, ``--family`` and (optionally) ``--all`` on a sub-parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--databases",
        action="append",
        metavar="A,B,...",
        help="comma separated rel_paths of the databases; repeatable (a value that is exactly "
        "one rel_path is taken whole, also when it contains a comma)",
    )
    group.add_argument(
        "--family", metavar="F1", help="all members of a schema family (F1, L1, ...)"
    )
    if with_all:
        group.add_argument(
            "--all", action="store_true", help="every database of the catalog (the default)"
        )


def add_parsers(sub: Any) -> None:
    """Register ``fleet-tables`` and ``fleet-sql`` on the sub-parsers object."""
    tables = sub.add_parser(
        "fleet-tables", help="which tables the chosen databases have, and which lack them"
    )
    tables.add_argument("root")
    add_selection_options(tables)
    tables.add_argument("--format", choices=["table", "csv", "json"], default="table")
    tables.add_argument("--cache-dir")

    fleet_sql = sub.add_parser(
        "fleet-sql",
        help="run read-only SQL on several databases at once (union views, a database column)",
    )
    fleet_sql.add_argument("root")
    fleet_sql.add_argument("sql", help="SQL text (several statements: the last result is shown)")
    add_selection_options(fleet_sql)
    fleet_sql.add_argument("--max-rows", type=_positive_int, help="rows shown (default 10000)")
    fleet_sql.add_argument(
        "--timeout", type=_positive_float, help="seconds before the query is stopped"
    )
    fleet_sql.add_argument("--format", choices=["table", "csv", "json"], default="table")
    fleet_sql.add_argument("--cache-dir")


# ---------------------------------------------------------------- the selection


def _split(text: str, known: set[str] | None = None) -> list[str]:
    """The rel_paths of one ``--databases`` value: the value itself when it is a known rel_path
    (it may contain commas), else its comma separated parts."""
    whole = _clean_rel_path(text)
    if known is not None and whole in known:
        return [whole]
    return [_clean_rel_path(part) for part in text.split(",") if part.strip()]


def _too_many(count: int) -> CliFailure:
    return CliFailure(
        EXIT_USAGE,
        f"{count} databases chosen, but at most {MAX_MEMBERS} can be attached at once (L-12); "
        "choose fewer with --databases or a smaller --family",
    )


def resolve_selection(
    reader: CatalogReader, args: argparse.Namespace
) -> tuple[FleetSelection, dict[str, str], list[dict[str, Any]]]:
    """``(selection, {rel_path: abs_path}, catalog rows of the members)`` for the options."""
    rows = reader.databases()
    by_rel = {row["rel_path"]: row for row in rows}
    databases = getattr(args, "databases", None)
    family = getattr(args, "family", None)
    if databases is not None:
        known = set(by_rel)
        chosen = list(dict.fromkeys(rel for text in databases for rel in _split(text, known)))
        if not chosen:
            raise CliFailure(EXIT_USAGE, "--databases needs at least one rel_path")
        unknown = [rel for rel in chosen if rel not in by_rel]
        if unknown:
            known = ", ".join(row["rel_path"] for row in rows[:8])
            more = f", ... ({len(rows)} in all)" if len(rows) > 8 else ""
            raise CliFailure(
                EXIT_USAGE,
                f"unknown database(s) {', '.join(repr(rel) for rel in unknown)} "
                f"(the catalog lists: {known}{more})",
            )
    elif family is not None:
        wanted = family.strip().upper()
        level = "strict" if wanted.startswith("F") else "loose"
        chosen = [row["rel_path"] for row in reader.family_members(level, wanted)]
        if not chosen:
            ids = [f["family_id"] for lvl in ("strict", "loose") for f in reader.families(lvl)]
            raise CliFailure(
                EXIT_USAGE,
                f"unknown family {family!r} (the catalog has: {', '.join(ids) or 'no family'})",
            )
    else:
        chosen = [row["rel_path"] for row in rows]
        if not chosen:
            raise CliFailure(EXIT_USAGE, "the catalog holds no database")
    if len(chosen) > MAX_MEMBERS:
        raise _too_many(len(chosen))
    paths = {rel: by_rel[rel]["abs_path"] for rel in chosen}
    return FleetSelection(rel_paths=chosen), paths, [by_rel[rel] for rel in chosen]


def report_failures(summary: FleetSummary) -> None:
    """One line per member that could not be attached, on stderr."""
    for failure in summary.failures:
        print(
            f"duckfleet: attach failed: {failure.rel_path} ({failure.kind}): {failure.message}",
            file=sys.stderr,
        )
    for name, message in summary.view_failures:
        print(f"duckfleet: union view failed: {name}: {message}", file=sys.stderr)


# ---------------------------------------------------------------- commands


@_guarded
def cmd_fleet_tables(args: argparse.Namespace) -> int:
    _store, reader = _catalog(args)
    selection, paths, _rows = resolve_selection(reader, args)
    summary = describe_fleet(selection, paths, _query_config(args))
    report_failures(summary)
    attached = list(summary.aliases.values())
    rows = [
        [
            f"{view.schema_name}.{view.table_name}",
            len(view.members),
            len(attached),
            ", ".join(rel for rel in attached if rel not in view.members),
            ", ".join(view.casts),
        ]
        for view in summary.views
    ]
    print(f"duckfleet: fleet: {len(attached)} of {len(selection.rel_paths)} databases attached",
          file=sys.stderr)  # fmt: skip
    write_rows(
        QueryResult(
            columns=["table", "members", "of", "missing_in", "cast_to_text"],
            types=[],
            rows=[tuple(row) for row in rows],
            truncated=False,
            elapsed_s=0.0,
            statements=0,
        ),
        args.format,
    )
    return EXIT_OK


@_guarded
def cmd_fleet_sql(args: argparse.Namespace) -> int:
    _store, reader = _catalog(args)
    selection, paths, _rows = resolve_selection(reader, args)
    extra = {"max_rows": args.max_rows} if args.max_rows else {}
    try:
        result, summary = run_fleet_query(
            selection, paths, args.sql, _query_config(args, **extra), max_rows=args.max_rows
        )
    except FleetQueryError as error:  # X-06: the attach failures are printed before the error
        report_failures(error.summary)
        raise
    report_failures(summary)
    write_rows(result, args.format)
    return EXIT_OK
