"""Two threads, two files, both spilling at once (review W-01); run as a script in a child.

Usage: ``python _spill_threads.py WORK_DIR [shared]``. Prints ``ok`` when both sorts return the
right count. With ``shared`` both instances are pointed at one temp folder, which is what 0.3.0
did; that is expected to crash the process and is only used to check that the test can fail.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import duckdb

from duckfleet_core import tempdir
from duckfleet_core.query import QueryConfig, run_query

ROWS = 12_000_000
SQL = f"select count(*) from (select hash(i) h from range({ROWS}) t(i) order by h)"


def main() -> int:
    work = Path(sys.argv[1])
    shared = len(sys.argv) > 2 and sys.argv[2] == "shared"
    if shared:
        tempdir.file_temp_dir = lambda root, _path: Path(root)  # type: ignore[assignment]
    databases = []
    for name in ("a.duckdb", "b.duckdb"):
        path = work / name
        duckdb.connect(str(path)).close()
        databases.append(path)
    config = QueryConfig(
        memory_limit_mb=64, threads=2, temp_dir=str(work / "cache" / "tmp" / "1"), timeout_s=300
    )
    results: dict[str, object] = {}

    def run(path: Path) -> None:
        try:
            results[path.name] = run_query(path, SQL, config).rows
        except Exception as exc:  # reported below
            results[path.name] = repr(exc)

    threads = [threading.Thread(target=run, args=(path,)) for path in databases]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if results != {"a.duckdb": [(ROWS,)], "b.duckdb": [(ROWS,)]}:
        print(results)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
