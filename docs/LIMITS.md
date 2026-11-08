# Limits

Every hard and soft limit, its default, where it is configured, and what
happens beyond it. Numbers marked (TBC) are proposals. The v0.1 and v0.2
acceptance runs used the demo fleet only, so they stay (TBC) until a real
fleet has been scanned.

Last updated: 2026-11-08 (0.3.0).

| ID | Limit | Default | Config | Beyond the limit | Since |
|---|---|---|---|---|---|
| L-01 | Files discovered per root | 1,000 (TBC) | `ScanConfig.max_files`, `--max-files` | walk stops at the first further DuckDB file, which is listed as rejected `over_max_files`; `truncated=True`, warning in UI and report. A root with exactly `max_files` files is not truncated | v0.1 |
| L-02 | Worker processes | `max(1, cpu_count - 2)`, at most 61 | `ScanConfig.workers`, `--workers` | capped at 61 (`MAX_POOL_WORKERS`, the Windows limit of `ProcessPoolExecutor`), also for an explicit value; more workers than cores oversubscribes | v0.1 |
| L-03 | DuckDB threads per worker | 1 | `ScanConfig.worker_threads` | parallelism comes from the process count | v0.1 |
| L-04 | Memory limit per worker | `min(2048 MB, total_ram / 2 / workers)`, floor 256 MB (TBC) | `ScanConfig.worker_memory_limit_mb` | DuckDB spills or fails the query; the file is recorded as error | v0.1 |
| L-05 | Full column stats | tables up to 20 M rows (TBC) | `ScanConfig.stats_row_threshold` | non-temporal stats on a sample, `sampled=True` | v0.1 |
| L-06 | Sample size above L-05 | 1 M rows (TBC) | `ScanConfig.stats_sample_rows` | | v0.1 |
| L-07 | Content fingerprint | tables up to 10 k rows (TBC) | | skipped | v0.4 |
| L-08 | Data preview page | 1,000 rows (TBC) | `QueryConfig.preview_rows` | paging; the page number input is bounded by the catalog's row count | v0.2 |
| L-09 | SQL result in grid / download | 10 k rows shown (page option 1 to 100 k), 1 M rows download (TBC) | `QueryConfig.max_rows`, `max_download_rows` | grid truncated with a notice; download cut at the cap with a warning (D-023) | v0.2 |
| L-10 | Worker crash | n/a | | a crashed pool marks the unfinished files `worker_crashed`; rerun the scan | v0.1 |
| L-11 | Points per chart | 50 k after bucketing (TBC) | `PlotSpec.max_points` (page option, `--max-points`) | line charts become bucket aggregates (mean, min, max), scatter charts a reservoir sample; histograms and bars are bounded by L-18 to L-20 instead; the final query is checked against the budget | v0.3 |
| L-12 | Databases attached at once | 50 (TBC) | | pick a subset | v0.4 |
| L-13 | Recent roots remembered | 20 | | oldest dropped | v0.1 |
| L-14 | Query timeout (SQL page, preview, summary, CLI) | 60 s (page option 1 to 3,600 s) (TBC) | `QueryConfig.timeout_s` | the query is interrupted and reported as `timeout`; the connection stays usable (D-024). A phase that never checks the interrupt (one huge allocation, the Arrow conversion of an export) can delay the stop; it is bounded and was never a hang in testing | v0.2 |
| L-15 | Preview columns shown by default | 50 (TBC) | `QueryConfig.preview_max_columns` | the rest selectable in the multiselect | v0.2 |
| L-16 | Per-column summary (`SUMMARIZE`) | exact up to L-05 rows, sampled above with L-06 rows | `QueryConfig.stats_row_threshold`, `stats_sample_rows` | `sampled=True` in the summary caption | v0.2 |
| L-17 | Query connection threads and memory | `min(4, cpu_count)` threads, `min(4 GB, RAM/4)` memory, floor 512 MB (TBC) | `QueryConfig.threads`, `memory_limit_mb`, env `DUCKFLEET_QUERY_THREADS`, `DUCKFLEET_QUERY_MEMORY_MB` | resolved once per process (D-021); DuckDB spills or fails the query at the memory limit | v0.2 |
| L-18 | Bar chart categories | 200 (TBC) | `PlotSpec.max_categories` | the rest summed into `(other)`, said in the caption | v0.3 |
| L-19 | Plot series (colour values) | 8 for line, histogram, bar; 3 for scatter (TBC) | `PlotSpec.max_series` | the top series by row count kept, the rest folded into one grey `(other)` series (D-032) | v0.3 |
| L-20 | Histogram bins | 50, range 5 to 500 (TBC) | `PlotSpec.bins` | rejected outside the range | v0.3 |
| L-21 | Plot point budget (page option) | 50,000, range 100 to 500,000 (TBC); same as L-11 | `PlotSpec.max_points` | bucketing, sampling or folding reduces the data in DuckDB until it fits; the plot queries run under L-14 | v0.3 |
| L-22 | Databases in one fleet connection (SQL and Plot scope, CLI `--databases`, `--family`, `--all`) | 50, same as L-12 | `FleetSelection` | a larger family is cut to the first 50 by rel_path with a caption; the CLI refuses more as a usage error | v0.4 |

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
   subprocess, which finishes and writes the catalog. A click on another
   widget during a scan makes Streamlit rerun the script inside the progress
   callback: the scan still finishes and writes the catalog (stdout is
   drained), but the finished notice and the active root are lost; press
   **Load existing catalog**.
6. **Heuristics.** Time windows use declared temporal column types only;
   epoch integers and date strings are not detected. Tags come from path
   tokens matching the default patterns. See `docs/TUNING.md`.
7. **Encrypted files** are not readable until F-SCAN-18 is built; they are
   listed with the error `encrypted`.
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
13. **`timestamptz` in Python.** Historical since v0.2: `pytz` is a core
    dependency (D-022), so the query layer fetches `TIMESTAMP WITH TIME ZONE`
    values; they appear in the machine's time zone (D-026). The profiler still
    converts those columns to UTC inside the SQL, so catalog min/max are UTC.
14. **Per-table failure is per table.** If the stats query of one table fails,
    that table keeps its row count and columns but has no column stats and is
    flagged `table_error:<schema>.<table>`. A temporal value outside Python's
    datetime range (for example year 10000) does not fail the query: the
    column's min/max come back as text, the table's time window is cleared, the
    other stats are kept, and the table carries the error `temporal value
    outside the supported range in column <name>` (also `table_error:`).
    `infinity` and `-infinity` are skipped when the window is computed.
15. **Reuse of cached profiles** compares size, mtime, profile schema version
    and the presence of the `.wal` sibling. A file that changes without a
    different size or mtime, or a WAL whose content changes while it stays
    present, is not noticed; `--full` re-profiles everything. Changed scan
    options (tag patterns, stats thresholds, views in fingerprints) also need
    `--full`. A profile with `table_error:*` health flags has status `ok` and
    is reused until the file changes. A different DuckDB version always
    re-profiles (a partial rescan cannot mix type spellings).
16. **Pool size.** `ScanSummary.workers` is the configured count
    (`max(1, cpu_count - 2)` by default); the pool never starts more
    processes than there are files to profile, and one file or one worker
    runs in the scan process itself.
17. **Timing on the demo fleet (Windows, 24 CPUs).** A scan with a process
    pool has a start-up cost of about 1.3 s per worker process (spawn, import
    of duckdb and pydantic), which dominates tiny fleets; a rescan that reuses
    all profiles runs in about 0.1 s. The catalog build takes about 1.4 s per
    120,000 column rows (400 files with 10 tables of 30 columns).
18. **Catalog rebuilt on every scan.** The cost grows with the total number of
    columns in the fleet, not with the data volume (D-003, D-016).
19. **Query sandbox (v0.2).** The SQL page and the CLI open scanned files with
    `enable_external_access=false` and a locked configuration: no `COPY ... TO`,
    `ATTACH`, `read_csv`, `INSTALL`, `LOAD` or `SET`, and writes fail with
    `read_only`. `CREATE TEMP TABLE` works (in memory, gone with the
    connection). Several statements in one text run in order and the last
    result is shown.
20. **Time zone in query results.** `TIMESTAMP WITH TIME ZONE` values appear in
    the machine's time zone (D-026), also in summaries; a summary cached on a
    machine in another zone keeps the old zone until it is recomputed.
21. **CSV download and nested columns.** The Arrow CSV writer has no list,
    struct or map type: such a result fails with a message that points to
    Parquet. Values that pandas or Arrow cannot hold in the grid (an integer
    beyond 64 bits, a year outside the pandas range) are shown as text.
22. **Summaries of views.** `SUMMARIZE` of a view runs the view's query and is
    not sampled (the row count of a view is unknown); it can reach the timeout.
23. **One query config per process.** `DUCKFLEET_QUERY_THREADS` and
    `DUCKFLEET_QUERY_MEMORY_MB` are read at the first `query_config()` call
    (the first render of the SQL page or of the Preview tab), not when the
    first query runs; restart `duckfleet-ui` to change them.
24. **DuckDB temp directory (0.2.1).** Every connection duckfleet opens uses
    `<cache dir>/tmp/<pid>` as DuckDB's `temp_directory`; without it DuckDB
    would spill into `<database path>.tmp` beside the scanned file (D-034).
    The folder is created before connecting and removed when the process
    exits; `scan` removes folders of processes that no longer exist. A query
    that needs more than the memory limit spills into that folder, so the
    cache drive needs free space for it.
25. **Preview cache (0.2.1).** Preview pages are cached by the Streamlit
    cache, keyed by the database file's current size and mtime, so a file that
    changed after the scan shows its new rows (the header says it changed).
    Edits that keep both size and mtime are not noticed. Entries: 64 pages.
26. **CLI text encoding on Windows.** `--format csv` and `json` are written
    as UTF-8 with LF line ends whatever the console code page. `--format
    table` uses the console encoding and replaces characters it cannot show
    with `?`; set `PYTHONUTF8=1` (or use another format) to see them.
27. **Plot data is reduced in DuckDB (0.3.0).** The reduction reads the whole
    filtered table once for the counts and once for the data, so a plot of a
    very large table or of a slow view can reach the timeout (L-14); narrow the
    filter or lower the budget. The row-order x (`row_number() over ()`)
    follows the storage order of the table.
28. **Rows that cannot be placed.** Rows whose x is NULL, NaN or infinite are
    left out of line and scatter charts, NULL and non-finite values of a
    histogram column out of its bins; the notes under the chart give the
    counts. `rows_scanned` is the count after the filter.
29. **Series.** A colour column shows at most 8 series (3 on a scatter chart,
    L-19); the rest is one grey `(other)` series, NULL values are a grey
    `(null)` series. The top series are chosen by row count of the filtered
    table. A value that is literally `(other)` or `(null)` cannot be told from
    the fold.
30. **Bucket grid.** Time buckets are aligned to the origin of DuckDB's
    `time_bucket` (epoch for sub-day widths, a Monday for 7 days) and labelled
    by their start; the 30, 90 and 365 day widths are fixed day counts, not
    calendar months or years. A temporal x is shown as naive UTC
    (`TIMESTAMP WITH TIME ZONE` is converted, `DATE` becomes midnight).
31. **Plot cache.** Plot data is cached by the Streamlit cache (16 entries),
    keyed by the file's current size and mtime and the spec; a file that
    changes without a new size or mtime is not noticed.
