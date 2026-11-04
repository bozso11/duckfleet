"""Command line interface: scan, report, make-demo, paths.

Exit codes: 0 success (even when some files failed), 1 usage error, 2 fatal
(root missing, catalog build failed). ``scan --progress json`` prints one JSON
object per line on stdout and nothing else there; logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from duckfleet_core import __version__
from duckfleet_core.catalog import CatalogReader
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_core.report import build_report
from duckfleet_core.store import ProfileStore

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FATAL = 2


class _Parser(argparse.ArgumentParser):
    """argparse exits with 2 on usage errors; this project reserves 2 for fatal errors."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="duckfleet", description="Explore a folder tree of DuckDB files.")
    parser.add_argument("--version", action="version", version=f"duckfleet {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    scan = sub.add_parser("scan", help="discover and profile the DuckDB files under ROOT")
    scan.add_argument("root")
    scan.add_argument("--workers", type=_positive_int, help="worker processes (default cpu-2)")
    scan.add_argument("--max-files", type=_positive_int, help="stop discovery after N files")
    scan.add_argument("--full", action="store_true", help="re-profile everything")
    scan.add_argument("--cache-dir", help="cache directory (default: the user cache)")
    scan.add_argument("--extensions", help="comma separated, default .duckdb,.db,.ddb")
    scan.add_argument("--sniff-all", action="store_true", help="header-check every file")
    scan.add_argument("--progress", choices=["text", "json", "none"], default="text")

    report = sub.add_parser("report", help="text summary of the last scan of ROOT")
    report.add_argument("root")
    report.add_argument("--format", choices=["text", "md"], default="text")
    report.add_argument("--cache-dir")

    demo = sub.add_parser("make-demo", help="generate a small demo fleet in DIR")
    demo.add_argument("dir")
    demo.add_argument("--seed", type=int, default=0)

    paths = sub.add_parser("paths", help="print the cache locations for ROOT")
    paths.add_argument("root")
    paths.add_argument("--cache-dir")
    return parser


def _text_event(event: dict) -> str | None:
    kind = event["event"]
    if kind == "discovered":
        text = (
            f"found {event['candidates']} candidate files "
            f"({event['walked']} files walked, {event['rejected']} rejected)"
        )
        return text + (" - WARNING: stopped at the file limit" if event["truncated"] else "")
    if kind == "planned":
        return f"profiling {event['to_profile']} files, reusing {event['reused']} cached profiles"
    if kind == "profiled":
        outcome = "ok" if event["status"] == "ok" else f"error:{event['error_type']}"
        return (
            f"[{event['done']}/{event['total']}] {outcome} {event['rel_path']} "
            f"({event['seconds']:.2f} s)"
        )
    if kind == "catalog":
        return f"catalog written: {event['path']}"
    return None


def _progress_handler(mode: str) -> Callable[[dict], None] | None:
    if mode == "json":

        def emit_json(event: dict) -> None:
            # ensure_ascii (the default) keeps stdout pure ASCII whatever the console encoding
            print(json.dumps(event), flush=True)

        return emit_json
    if mode == "text":

        def emit_text(event: dict) -> None:
            line = _text_event(event)
            if line:
                print(line, file=sys.stderr, flush=True)

        return emit_text
    return None


def _config_from_args(args: argparse.Namespace) -> ScanConfig:
    options: dict = {"full": args.full, "sniff_all_files": args.sniff_all}
    if args.workers:
        options["workers"] = args.workers
    if args.max_files:
        options["max_files"] = args.max_files
    if args.cache_dir:
        options["cache_dir"] = args.cache_dir
    if args.extensions:
        options["extensions"] = args.extensions.split(",")
    return ScanConfig(**options)


def _fatal(message: str, progress: str = "text") -> int:
    print(f"duckfleet: error: {message}", file=sys.stderr, flush=True)
    if progress == "json":
        print(json.dumps({"event": "fatal", "message": message}), flush=True)
    return EXIT_FATAL


def _cmd_scan(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    try:
        summary = run_scan(Path(args.root), config, _progress_handler(args.progress))
    except Exception as exc:
        logging.getLogger("duckfleet").debug("scan failed", exc_info=True)
        return _fatal(str(exc) or type(exc).__name__, args.progress)
    if args.progress == "text":
        ok = summary.files_candidates - summary.files_failed
        print(
            f"Scanned {summary.root}: {summary.files_candidates} candidates, "
            f"{summary.files_rejected} rejected, {ok} ok, {summary.files_failed} failed, "
            f"{summary.files_reused} reused, {summary.workers} workers"
        )
        print(f"Catalog: {summary.catalog_path}")
    return EXIT_OK


def _store_for(args: argparse.Namespace) -> ProfileStore:
    cache_dir = ScanConfig(cache_dir=args.cache_dir).resolved_cache_dir()
    return ProfileStore(cache_dir, Path(args.root))


def _cmd_report(args: argparse.Namespace) -> int:
    store = _store_for(args)
    if not store.catalog_path.is_file():
        return _fatal(
            f"no catalog for {store.root} in {store.cache_dir}; run: duckfleet scan {args.root}"
        )
    try:
        print(build_report(CatalogReader(store.catalog_path), args.format))
    except Exception as exc:
        logging.getLogger("duckfleet").debug("report failed", exc_info=True)
        return _fatal(f"cannot read the catalog {store.catalog_path}: {exc}")
    return EXIT_OK


def _cmd_make_demo(args: argparse.Namespace) -> int:
    root = make_demo_fleet(Path(args.dir), seed=args.seed)
    print(f"Demo fleet written to {root.resolve()}")
    print(f"Next: duckfleet scan {args.dir}")
    return EXIT_OK


def _cmd_paths(args: argparse.Namespace) -> int:
    store = _store_for(args)
    print(f"cache_dir: {store.cache_dir}")
    print(f"root_dir: {store.root_dir}")
    print(f"catalog: {store.catalog_path}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the exit code."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # never crash on a console that cannot show a character
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exit_request:  # --help and --version exit 0, usage errors 1
        return int(exit_request.code or 0)
    handlers = {
        "scan": _cmd_scan,
        "report": _cmd_report,
        "make-demo": _cmd_make_demo,
        "paths": _cmd_paths,
    }
    return handlers[args.command](args)
