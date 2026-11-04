# Limits

Every hard and soft limit, its default, where it is configured, and what
happens beyond it. Numbers marked (TBC) are proposals until v0.1
acceptance confirms them on real data.

Last updated: 2026-11-04.

| ID | Limit | Default | Config | Beyond the limit | Since |
|---|---|---|---|---|---|
| L-01 | Files discovered per root | 1,000 (TBC) | `ScanConfig.max_files`, `--max-files` | walk stops at the first further DuckDB file, which is listed as rejected `over_max_files`; `truncated=True`, warning in UI and report. A root with exactly `max_files` files is not truncated | v0.1 |
| L-02 | Worker processes | `max(1, cpu_count - 2)` | `ScanConfig.workers`, `--workers` | user choice; more workers than cores oversubscribes | v0.1 |
| L-03 | DuckDB threads per worker | 1 | `ScanConfig.worker_threads` | parallelism comes from the process count | v0.1 |
| L-04 | Memory limit per worker | `min(2048 MB, total_ram / 2 / workers)`, floor 256 MB (TBC) | `ScanConfig.worker_memory_limit_mb` | DuckDB spills or fails the query; the file is recorded as error | v0.1 |
| L-05 | Full column stats | tables up to 20 M rows (TBC) | `ScanConfig.stats_row_threshold` | non-temporal stats on a sample, `sampled=True` | v0.1 |
| L-06 | Sample size above L-05 | 1 M rows (TBC) | `ScanConfig.stats_sample_rows` | | v0.1 |
| L-07 | Content fingerprint | tables up to 10 k rows (TBC) | | skipped | v0.4 |
| L-08 | Data preview page | 1,000 rows (TBC) | | paging | v0.2 |
| L-09 | SQL result in grid / download | 10 k rows shown, 10 M rows download (TBC) | | truncated with a notice | v0.2 |
| L-10 | Worker crash | n/a | | a crashed pool marks the unfinished files `worker_crashed`; rerun the scan | v0.1 |
| L-11 | Points per chart | 50 k after bucketing (TBC) | | bucket aggregates (mean, min, max) | v0.3 |
| L-12 | Databases attached at once | 50 (TBC) | | pick a subset | v0.4 |
| L-13 | Recent roots remembered | 20 | | oldest dropped | v0.1 |

## Known limitations without a number

1. **Storage format compatibility.** Files written before DuckDB 0.10 and
   files written by a newer DuckDB than the installed package may not
   open. Reported per file as `incompatible_version`.
2. **Writer lock.** A file held open read-write by another process cannot
   be read. Reported per file as `locked`, not retried.
3. **Catalog single writer.** While a scan rebuilds the catalog the UI must
   not hold it open; readers open per request and close.
4. **Blocking scan.** In v0.1 the UI page waits behind a progress bar until
   the scan ends. The CLI can run the scan independently.
5. **No scan cancel** in v0.1; closing the page does not stop the
   subprocess, which finishes and writes the catalog.
6. **Heuristics.** Time windows use declared temporal column types only;
   epoch integers and date strings are not detected. Tags come from path
   tokens matching the default patterns. See `docs/TUNING.md`.
7. **Encrypted files** are not readable until F-SCAN-18 is built.
8. **Network drives** work but scan at network speed; no special handling.
9. **Views** are listed but not fingerprinted and not row-counted by
   default; a view that fails to bind is recorded as a table error.
10. **DuckDB opens SQLite files.** duckfleet rejects them by header check;
    if a real DuckDB file lacks the magic bytes (it should not), it is
    rejected too.
11. **Windows and writer-held files.** DuckDB opens a file exclusively while a
    read-write connection exists, and on Windows even reading the header then
    fails with `PermissionError`. Discovery keeps such a file as a candidate
    and the profiler reports `locked` (on Windows any `PermissionError` from
    the header read is read as locked; it can also be an ACL problem, the
    message carries the OS text). On Linux the lock is advisory, the header
    read works and DuckDB's own lock error is classified.
12. **Sampled statistics.** Above L-05 `non_null_count`, `approx_distinct` and
    numeric min/max describe the sample (`sampled=True`); temporal min/max
    are always exact. The row count is always exact.
13. **`timestamptz` in Python.** The Python client needs `pytz` to fetch
    `TIMESTAMP WITH TIME ZONE` values and it is not a dependency; the
    profiler converts those columns to UTC inside the SQL instead, so their
    min/max are UTC.
14. **Per-table failure is per table.** If the stats query of one table fails
    (for example a value outside the Python date range), that table keeps its
    row count and columns but has no column stats and is flagged
    `table_error:<schema>.<table>`.
15. **Reuse of cached profiles** compares size, mtime, profile schema version
    and the presence of the `.wal` sibling. A file that changes without a
    different size or mtime, or a WAL whose content changes while it stays
    present, is not noticed; `--full` re-profiles everything. Changed scan
    options (tag patterns, stats thresholds, views in fingerprints) also need
    `--full`.
16. **Pool size.** `ScanSummary.workers` is the configured count
    (`max(1, cpu_count - 2)` by default); the pool never starts more
    processes than there are files to profile, and one file or one worker
    runs in the scan process itself.

