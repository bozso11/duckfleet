"""CLI command ``plot``: the reduced data of a chart, as CSV or JSON (plan v0.3 Phase 2).

With ``--databases`` or ``--family`` (v0.4) the plot spans several databases: ``plot ROOT
--family F1 main.events ...`` plots the union view of ``events`` (the rows of every member with a
``database`` column) and colours by ``database`` unless ``--color`` says otherwise.

The chart itself is the Plot page's job: core must not import the UI package, and the
chart code lives there (an HTML export from the CLI is an open option, docs/ROADMAP.md).
The command prints the rows ``fetch_plot_data`` would hand to the chart, a one-line summary
on stderr and, with ``--show-sql``, the final query on stderr too.

Exit codes as ``sql``: 0 ok, 1 usage (bad spec), 2 fatal, 3 query error.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from pydantic import ValidationError

from duckfleet_core.catalog import CatalogReader
from duckfleet_core.cli_fleet import add_selection_options, resolve_selection
from duckfleet_core.cli_query import (
    EXIT_OK,
    EXIT_USAGE,
    CliFailure,
    _catalog,
    _database,
    _guarded,
    _positive_float,
    _positive_int,
    _query_config,
    _table,
    write_rows,
)
from duckfleet_core.fleet import describe_fleet
from duckfleet_core.plotdata import PlotData, PlotSpec, fetch_fleet_plot_data, fetch_plot_data
from duckfleet_core.query import QueryError, QueryResult


def add_parser(sub: Any) -> None:
    """Register ``plot`` on the sub-parsers object."""
    plot = sub.add_parser("plot", help="reduced data of a chart (line, scatter, histogram, bar)")
    plot.add_argument("root")
    plot.add_argument(
        "database",
        help="rel_path of the database, as listed by 'report' (with --databases or --family: "
        "the table)",
    )
    plot.add_argument("table", nargs="?", metavar="SCHEMA.TABLE")
    add_selection_options(plot, with_all=False)
    plot.add_argument("--kind", required=True, choices=["line", "scatter", "histogram", "bar"])
    plot.add_argument("--x", metavar="COL", help="x column (default: the row order)")
    plot.add_argument(
        "--y", action="append", metavar="COL[,COL...]", help="value column(s); repeatable"
    )
    plot.add_argument("--color", metavar="COL", help="series column")
    plot.add_argument("--where", metavar="EXPR", help="SQL boolean expression")
    plot.add_argument("--max-points", type=_positive_int, help="point budget (default 50000)")
    plot.add_argument("--bins", type=_positive_int, help="histogram bins, 5 to 500 (default 50)")
    plot.add_argument("--agg", choices=["mean", "sum", "min", "max", "count"], help="bar measure")
    plot.add_argument("--format", choices=["csv", "json"], default="csv")
    plot.add_argument("--show-sql", action="store_true", help="print the final query on stderr")
    plot.add_argument("--timeout", type=_positive_float, help="seconds before the plot is stopped")
    plot.add_argument("--cache-dir")


def _columns(values: list[str] | None) -> list[str]:
    names = [name.strip() for value in values or [] for name in value.split(",")]
    return [name for name in names if name]


def _spec(args: argparse.Namespace, schema_name: str, table_name: str) -> PlotSpec:
    options: dict[str, Any] = {
        "schema_name": schema_name,
        "table_name": table_name,
        "kind": args.kind,
        "x": args.x,
        "y": _columns(args.y),
        "color": args.color,
        "where": args.where,
    }
    for field in ("max_points", "bins", "agg"):
        value = getattr(args, field)
        if value is not None:
            options[field] = value
    try:
        return PlotSpec(**options)
    except ValidationError as exc:
        text = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
        raise CliFailure(EXIT_USAGE, f"{text}" + (f" ({fields})" if fields else "")) from exc


def summary_line(data: PlotData, databases: int | None = None) -> str:
    """One line for stderr: databases (fleet plots), strategy, bucket, points, rows, elapsed."""
    parts = [f"databases={databases}"] if databases is not None else []
    parts.append(f"strategy={data.strategy}")
    if data.bucket:
        parts.append(f"bucket={data.bucket}")
    parts.append(f"points={data.points}")
    parts.append(f"rows_scanned={data.rows_scanned}")
    if data.sampled:
        parts.append("sampled")
    if data.folded_series:
        parts.append(f"series_folded={data.folded_series}")
    if data.folded_categories:
        parts.append(f"categories_folded={data.folded_categories}")
    parts.append(f"elapsed={data.elapsed_s:.2f} s")
    return "duckfleet: plot: " + " ".join(parts)


def _fleet_table(reader: CatalogReader, rel_paths: list[str], text: str) -> tuple[str, str]:
    """``(schema, table)`` of ``SCHEMA.TABLE`` (or a bare name that is unique) over the members."""
    found: dict[str, tuple[str, str]] = {}
    for rel_path in rel_paths:
        for row in reader.tables(rel_path):
            found[f"{row['schema_name']}.{row['table_name']}"] = (
                row["schema_name"],
                row["table_name"],
            )
    if text in found:
        return found[text]
    bare = [key for key, (_schema, name) in found.items() if name == text]
    if "." not in text and len(bare) == 1:
        return found[bare[0]]
    listing = ", ".join(sorted(found)[:12]) or "none"
    if len(bare) > 1:
        raise CliFailure(
            EXIT_USAGE, f"{text!r} is ambiguous in the chosen databases; use SCHEMA.TABLE"
        )
    raise CliFailure(
        EXIT_USAGE, f"unknown table {text!r} in the chosen databases (tables: {listing})"
    )


def _plot_fleet(args: argparse.Namespace, reader: CatalogReader) -> int:
    if args.table is not None:
        raise CliFailure(
            EXIT_USAGE,
            "with --databases or --family give only ROOT and SCHEMA.TABLE "
            "(plot ROOT --family F1 main.events ...)",
        )
    selection, paths, _rows = resolve_selection(reader, args)
    schema, table = _fleet_table(reader, selection.rel_paths, args.database)
    config = _query_config(args)
    summary = describe_fleet(selection, paths, config)
    view = next(
        (v for v in summary.views if (v.schema_name, v.table_name) == (schema, table)), None
    )
    if view is None:
        raise QueryError("sql", f"{schema}.{table} is not in any of the attachable databases")
    if args.color is None:
        args.color = view.label_column  # one series per database
    spec = _spec(args, schema, table)
    data = fetch_fleet_plot_data(selection, paths, spec, config)
    _print_plot(args, data, len(summary.aliases))
    return EXIT_OK


@_guarded
def cmd_plot(args: argparse.Namespace) -> int:
    _store, reader = _catalog(args)
    if args.databases is not None or args.family is not None:
        return _plot_fleet(args, reader)
    if args.table is None:
        raise CliFailure(EXIT_USAGE, "missing SCHEMA.TABLE (plot ROOT DATABASE SCHEMA.TABLE ...)")
    row = _database(reader, args.database)
    table = _table(reader, row["rel_path"], args.table)
    spec = _spec(args, table["schema_name"], table["table_name"])
    data = fetch_plot_data(row["abs_path"], spec, _query_config(args))
    _print_plot(args, data)
    return EXIT_OK


def _print_plot(args: argparse.Namespace, data: PlotData, databases: int | None = None) -> None:
    print(summary_line(data, databases), file=sys.stderr)
    for note in data.notes:
        print(f"duckfleet: note: {note}", file=sys.stderr)
    if args.show_sql:
        print(data.sql, file=sys.stderr)
    write_rows(
        QueryResult(
            columns=data.columns,
            types=["" for _ in data.columns],
            rows=data.rows,
            truncated=False,
            elapsed_s=data.elapsed_s,
            statements=1,
        ),
        args.format,
    )
