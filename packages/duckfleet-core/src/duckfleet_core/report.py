"""Text and Markdown report of a fleet, built from the catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from duckfleet_core.catalog import CatalogReader

Line = tuple[int, str]  # (indent level, text)
Section = tuple[str, list[Line]]

_MAX_COLUMNS_SHOWN = 12


def _ts(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return "" if value is None else str(value)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _duration_seconds(run: dict[str, Any]) -> float | None:
    start, end = run.get("started_at"), run.get("finished_at")
    if isinstance(start, datetime) and isinstance(end, datetime):
        return (end - start).total_seconds()
    return None


def _summary(reader: CatalogReader, databases: list[dict[str, Any]]) -> list[Line]:
    run = reader.scan_run() or {}
    ok = sum(d["status"] == "ok" for d in databases)
    failed = len(databases) - ok
    duration = _duration_seconds(run)
    lines: list[Line] = [
        (0, f"root: {run.get('root', '?')}"),
        (0, f"scan: {run.get('run_id', '?')}, finished {_ts(run.get('finished_at'))} UTC"),
        (0, f"versions: duckfleet {run.get('duckfleet_version', '?')}, "
            f"duckdb {run.get('duckdb_version', '?')}"),
        (0, f"files walked: {run.get('files_walked', '?')}"),
        (0, f"candidates: {run.get('files_candidates', len(databases))}"),
        (0, f"rejected: {run.get('files_rejected', '?')}"),
        (0, f"ok: {ok}"),
        (0, f"failed: {failed}"),
        (0, f"reused profiles: {run.get('files_reused', '?')}"),
        (0, f"profiled in this run: {run.get('files_profiled', '?')}"),
        (0, f"workers: {run.get('workers', '?')}"),
        (0, f"duration: {duration:.1f} s" if duration is not None else "duration: ?"),
    ]  # fmt: skip
    if run.get("truncated"):
        lines.append((0, "WARNING: the walk stopped at the file limit; some files are not listed"))
    return lines


def _families(reader: CatalogReader, level: str) -> list[Line]:
    families = reader.families(level)
    if not families:
        return [(0, "none")]
    lines: list[Line] = []
    for family in families:
        lines.append(
            (0, f"{family['family_id']}: {_plural(family['n_members'], 'database')}, "
                f"{_plural(family['n_tables'], 'table')}")
        )  # fmt: skip
        tables = ", ".join(
            f"{schema}.{table} ({_plural(len(columns), 'column')})"
            for schema, table, columns in family["canonical"]
        )
        lines.append((1, f"tables: {tables}"))
        lines.append((1, "members:"))
        lines.extend((2, member) for member in family["members"])
    return lines


def _variants(reader: CatalogReader) -> list[Line]:
    variants = reader.table_variants()
    if not variants:
        return [(0, "none")]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for variant in variants:
        grouped.setdefault((variant["schema_name"], variant["table_name"]), []).append(variant)
    lines: list[Line] = []
    for (schema, table), items in grouped.items():
        total = sum(v["n_members"] for v in items)
        if len(items) == 1:
            lines.append((0, f"{schema}.{table}: 1 variant ({_plural(total, 'database')})"))
            continue
        lines.append((0, f"{schema}.{table}: {len(items)} variants ({_plural(total, 'database')})"))
        for item in items:
            shown = item["columns"][:_MAX_COLUMNS_SHOWN]
            columns = ", ".join(f"{name} {data_type}" for name, data_type in shown)
            if len(item["columns"]) > len(shown):
                columns += ", ..."
            lines.append(
                (1, f"{item['fp_strict']}: {_plural(item['n_members'], 'database')}, "
                    f"{_plural(item['n_columns'], 'column')}: {columns}")
            )  # fmt: skip
    return lines


def _identical_tables(reader: CatalogReader) -> list[Line]:
    """Per table with a content hash: how many different contents, and the largest group."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for variant in reader.content_variants():
        grouped.setdefault((variant["schema_name"], variant["table_name"]), []).append(variant)
    lines: list[Line] = []
    for (schema, table), items in grouped.items():
        databases = sum(v["n_members"] for v in items)
        largest = max(v["n_members"] for v in items)
        text = (
            f"{schema}.{table}: {_plural(len(items), 'content variant')} "
            f"over {_plural(databases, 'database')}"
        )
        if databases > 1:
            text += (
                f", largest group {_plural(largest, 'database')}"
                if largest > 1
                else ", no two are identical"
            )
        lines.append((0, text))
    return lines or [(0, "none")]


def _time_windows(reader: CatalogReader, databases: list[dict[str, Any]]) -> list[Line]:
    timeline = reader.timeline()
    family = {d["rel_path"]: d["family_strict"] for d in databases}
    lines: list[Line] = [
        (0, f"{_ts(row['time_min'])} .. {_ts(row['time_max'])}  {row['rel_path']}"
            + (f"  [{family[row['rel_path']]}]" if family.get(row["rel_path"]) else ""))
        for row in timeline
    ]  # fmt: skip
    windowed = {row["rel_path"] for row in timeline}
    missing = [d["rel_path"] for d in databases if d["rel_path"] not in windowed]
    if missing:
        lines.append((0, f"no time window ({len(missing)}):"))
        lines.extend((1, rel_path) for rel_path in missing)
    return lines or [(0, "none")]


def _health(reader: CatalogReader) -> list[Line]:
    rows = [r for r in reader.health() if r["health"]]
    return [(0, f"{r['rel_path']}: {', '.join(r['health'])}") for r in rows] or [(0, "none")]


def _errors(reader: CatalogReader) -> list[Line]:
    rows = [r for r in reader.health() if r["status"] == "error"]
    return [(0, f"{r['rel_path']}: {r['error_type']}: {r['error_message']}") for r in rows] or [
        (0, "none")
    ]


def _rejected(reader: CatalogReader) -> list[Line]:
    rows = reader.rejected()
    return [(0, f"{r['rel_path']}: {r['reason']}") for r in rows] or [(0, "none")]


def build_sections(reader: CatalogReader) -> list[Section]:
    """The report as ``(title, lines)`` sections."""
    databases = reader.databases()
    strict_n = len(reader.families("strict"))
    loose_n = len(reader.families("loose"))
    return [
        ("Summary", _summary(reader, databases)),
        (f"Strict schema families ({strict_n})", _families(reader, "strict")),
        (f"Loose schema families ({loose_n})", _families(reader, "loose")),
        ("Table variants", _variants(reader)),
        ("Identical small tables", _identical_tables(reader)),
        ("Time windows (sorted by start)", _time_windows(reader, databases)),
        ("Health flags", _health(reader)),
        ("Errors", _errors(reader)),
        ("Rejected files", _rejected(reader)),
    ]


def build_report(reader: CatalogReader, fmt: str = "text") -> str:
    """Render the fleet report as plain text (``"text"``) or Markdown (``"md"``)."""
    if fmt not in ("text", "md"):
        raise ValueError(f"unknown report format: {fmt!r}")
    out: list[str] = []
    if fmt == "md":
        out += ["# duckfleet report", ""]
    else:
        out += ["duckfleet report", "================", ""]
    for title, lines in build_sections(reader):
        if fmt == "md":
            out += [f"## {title}", ""]
            out += ["  " * level + "- " + text for level, text in lines]
        else:
            out += [title, "-" * len(title)]
            out += ["  " * (level + 1) + text for level, text in lines]
        out.append("")
    return "\n".join(out)
