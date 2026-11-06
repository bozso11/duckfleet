# Review of v0.2.0 against the plan

Date: 2026-11-06. Reviewed commits: `dbfd7bf` (0.1.1) to `15ea791`
(0.2.0). Reviewer: the planning session (Opus-class, decision D-012), with
two independent read-and-reproduce passes (core; UI and documentation).
Reference: `docs/PLAN_v0.2.md` and `docs/REVIEW_v0.1.md`.

## Verdict

0.1.1 fixes every finding of the v0.1 review, and v0.2.0 implements the
plan's contracts faithfully: the query layer, summaries, CLI commands and
both pages are present as specified, the sandbox holds against every
escape tried, and the acceptance checklist is true on re-check. The review
found 12 confirmed defects. One is a rule violation that affects v0.1 as
well (V-01: DuckDB spills temporary data next to the scanned file), three
are medium, the rest low. All fixes go into Phase 0 of the v0.3 plan and
ship as 0.2.1.

## 1. Verified by the reviewer, not self-reported

1. CI: all seven commits (0.1.1 and Phases 1 to 6) green on `ubuntu-latest`
   and `windows-latest`.
2. 345 tests pass and ruff is clean on Windows (Python 3.11.9, duckdb
   1.5.6, streamlit 1.66.1, pandas 3.1.1).
3. Fresh demo fleet: 16 walked, 13 candidates, 2 rejected, 10 ok, 3 failed
   (2 corrupt, 1 encrypted); rescan reuses 10 and retries 3; `sql` returns
   the count, an `INSERT` and a `COPY TO` exit 3 as `read_only` and
   `blocked`; `summarize` computes then serves from cache; `preview` sorts
   and pages; `report` lists the encrypted file.
4. UI on Windows in a browser: Overview loads the catalog; Database page
   header, tables grid, Columns, Summary (computed on request), Preview;
   Open in SQL lands on the SQL page with the template query; the query
   runs; a Parquet download is prepared; an `INSERT` is refused with the
   read-only hint; a slow query is interrupted at 60 s with the timeout
   message.
5. Conformance read of every new or changed module against the plan.
6. Findings V-01, V-02 and V-03 reproduced by the reviewer; the others by
   a review pass with a script or an AppTest.

## 2. Confirmed findings

Severity: high = a rule violation or a failure a real fleet will hit;
medium = a wrong result or classification in a plausible situation; low =
cosmetic, hygiene or documentation.

| ID | Sev. | Where | Claim | Scenario | Fix |
|---|---|---|---|---|---|
| V-01 | high | `query.connection_config`, `profiler._profile` connect config | DuckDB's `temp_directory` defaults to `<database path>.tmp`, so a query or scan that spills under its memory limit writes a directory next to the scanned file; read-only and external-access-off do not stop it | `memory_limit_mb=64` and a wide `GROUP BY`: `spill.duckdb.tmp` appears beside the file during the query (reproduced); on a real fleet any `SUMMARIZE` or sort above the limit does this, fails on a read-only share, and litters after a crash | Add `"temp_directory": <cache_dir>/tmp` to `connection_config` and to the profiler's connect config (both accepted in the locked dict, verified); resolve it once per process; a test lists the folder during and after a spilling query |
| V-02 | medium | `query._with_timeout`, `classify_error` | A timeout that lands while rows are being fetched is reported as `sql`, not `timeout` | Streaming result with VARCHAR or list columns, `timeout_s=0.3`: DuckDB raises `InvalidInputException: ... INTERRUPT Error: Interrupted!`, which the code does not treat as an interrupt (4 of 4 runs) | After `con.interrupt()`, turn every `duckdb.Error` raised by the worker into `QueryError("timeout")`; also match `interrupt` in `classify_error`; test with a VARCHAR result |
| V-03 | medium | `cli.main` (`reconfigure(errors="replace")`), `cli_query.write_rows` | `--format csv` and `table` silently replace characters outside the console code page with `?` on Windows, and CSV lines end in `\r\n` | Without `PYTHONUTF8`, `select '日本語 é'` piped to a file gives `??? é` as cp1252 bytes (reproduced); the csv writer's `\n` becomes `\r\n` through text-mode stdout | For `csv` and `json` write UTF-8 bytes through `sys.stdout.buffer` with `newline=""` semantics; document `PYTHONUTF8=1` for `table`; test through a subprocess comparing bytes |
| V-04 | medium | `query._with_timeout` | Ctrl-C in the CLI neither interrupts the query nor lets the timeout act: `KeyboardInterrupt` skips the timeout branch and `shutdown(wait=True)` waits for the whole query | A long query with `--timeout 8` and Ctrl-C at 1 s surfaced after 177 s | `except BaseException: con.interrupt(); raise` before the `finally` |
| V-05 | medium | `views/database.py` preview, `query_access.preview_page` | The preview cache is keyed by the catalog row's size and mtime (scan time), not the file's current values, so a file modified after the scan keeps serving the old page | Insert a row into a scanned file: the header says "changed since the last scan", the Summary tab uses the live file, the Preview grid still shows the old rows (reproduced by AppTest) | Key `preview_page` by the file's current `stat` (the header already computes it); document the cache in LIMITS |
| V-06 | low | `query.classify_error` | Substrings are matched before exception types, so a value in the SQL text can set the kind | `select 'read-only mode'::INTEGER` is `read_only` | Check `PermissionException` for `blocked` and the `Cannot execute statement` prefix for `read_only` before substrings |
| V-07 | low | `cli_query.write_rows` | JSON prints `NaN` (not JSON) and BLOBs as Python reprs; CSV prints arrays as tuples | `select 'nan'::DOUBLE, '\x00'::BLOB --format json` | `allow_nan=False` with a `default` mapping NaN to null and bytes to hex; render tuples as lists |
| V-08 | low | `cli_query.write_rows` | `(no result set)` is unreachable: DuckDB returns a `Count` column for statements | `sql ... "create temp table z (i integer)"` prints `Count` | Drop the branch or detect the `Count`-only result |
| V-09 | low | `views/database.py` Summary tab | The button still reads "Compute summary" in the run that computed the summary; "Recompute" appears on the next rerun | Plan Phase 4 item 3; already noted as open in the changelog | `st.rerun()` after a successful compute only (a rerun after a failed compute would hide the error, since error summaries are not cached) |
| V-10 | low | `views/sql.py` Options | A value typed into the Options number inputs can be lost when Run is clicked before the input commits (blur or Enter); reproduced once in a browser, not under AppTest | Timeout typed as 1, Run clicked at once, query ran with 60 s | Put the two inputs and Run in an `st.form`, which commits every input with the click |
| V-11 | low | `common.keep` | Every kept widget value is re-set through the API on every rerun, which marks it as API-set and pushes it back to the browser each time; works, but fragile | Any page | Streamlit 1.66 offers `persist_state="session"` on these widgets (verified in the signatures of `number_input`, `text_area`, `selectbox`); use it and drop `keep()` |
| V-12 | low | `views/sql.py` downloads | Prepare CSV/Parquet exports the editor text while the grid shows the last run; neither is labelled | Edit the query, prepare without running | Disable Prepare when the editor text differs from the shown result's SQL (D-029 documents the current behaviour) |

## 3. Plausible, not reproduced

| ID | Claim | Decision |
|---|---|---|
| P-05 | The profiler's connect config differs from `connection_config` (no external-access and lock settings, other threads), so an in-process scan next to an open page would raise `ConnectionException` on the same file; production never overlaps them (the UI scans in a subprocess) | Adopt with V-01: give the profiler the same settings except `threads` and `memory_limit` where they must differ, and say in ARCHITECTURE that connections to one file in one process share a config |
| P-06 | A phase that never checks the interrupt (a single huge allocation, the Arrow conversion in `export_query`) would delay the timeout; bounded, never a hang in testing | Document under L-14 |
| P-07 | `SummaryStore.get_or_compute` returns an unsaved failure when `stat` fails while the docstring says failures are saved | Fix the docstring |

## 4. Documentation corrections

1. `docs/ARCHITECTURE.md` Section 7: the kept session keys also include
   `sql_max_rows`, `sql_timeout` and the six sidebar keys.
2. `docs/LIMITS.md` 23: the environment overrides are read at the first
   `query_config()` call (first render of the SQL page or Preview tab), not
   when the first query runs.
3. `docs/DECISIONS.md` D-024: the calling thread interrupts; the query runs
   in the executor thread.
4. `views/overview.py` timeline padding (1 hour or 0.5 % of the span) needs
   a `# TUNING(timeline-padding)` marker and a `docs/TUNING.md` row.
5. The preview cache (V-05) and the temp directory (V-01) need LIMITS
   lines; `PYTHONUTF8=1` for the CLI on Windows needs a README line.
6. `CHANGELOG.md` 0.2.0 item 1 ticks CI for the final commit before the run
   existed; the reviewer confirmed it afterwards.

## 5. Phase 0 (0.1.1) completeness

| Finding | Fix | Test |
|---|---|---|
| R-01 storage version overflow | fixed (`UBIGINT`, header guard, demo file) | yes |
| R-02 infinity windows | fixed (`FILTER (WHERE isfinite)` on both paths, sentinel guard) | yes, including TIMESTAMPTZ |
| R-03 tag regex | fixed (validator, `_file_facts` inside the try, `--tag-pattern`) | partial: no 2-worker run with a valid non-matching pattern |
| R-04 64-CPU crash | fixed (cap in core) | yes |
| R-05 out-of-range temporal | fixed (table error, window cleared) | yes |
| R-06 cache dir scanned | fixed (realpath pruning) | yes |
| R-07 catalog writer settings | fixed | no assertion on the writer's config |
| R-08 single-instant bars | fixed | yes |
| R-09 scan failure message | fixed | yes |
| R-10 vacuous assertion | removed | n/a |
| R-11 encrypted fixture | fixed (demo file, docs) | yes |
| P-01 duckdb_version in reuse | fixed | yes |
| P-02 cancel_futures | fixed | partial: the real-pool assertion holds whether or not anything was cancelled |

## 6. Test gaps to close in 0.2.1

1. Timeout during the fetch of a VARCHAR or list result (V-02).
2. Nothing written beside a scanned file under memory pressure (V-01).
3. Non-ASCII CLI output through a real encoding, compared as bytes (V-03).
4. A real cancellation test for P-02 (a worker-side marker or a timing
   bound on `stream.close()`).
5. The catalog writer's config (R-07) and the R-03 pool-level run.
6. Summaries of nested-only and binary-only tables, and of a view whose
   base table was dropped (all behave; pin them).

## 7. Checked and fine

1. Sandbox: writes are `read_only`; `COPY TO`, `ATTACH` of a file,
   `read_csv`, `read_text`, `glob`, `EXPORT`/`IMPORT`, `INSTALL`, `LOAD`,
   secrets, and `SET`/`RESET`/`PRAGMA` of locked options are `blocked`;
   `CHECKPOINT`, `VACUUM`, `ANALYZE` leave the file bytes unchanged; temp
   tables, `USE` and `SET VARIABLE` are per connection.
2. Every open of a scanned file inside the UI or CLI goes through
   `open_readonly` with one config per process; connections close on
   success, error and timeout; a timed-out file reopens cleanly.
3. Multi-statement SQL, the export cap and slice, non-query exports,
   `preview_table` validation and quoting with hostile names, duplicate
   column names, `TIMESTAMP WITH TIME ZONE` through fetch, preview,
   summary and all CLI formats (pytz).
4. Summary cache rule, error summaries never reused, empty table, view,
   sampled flag, prune of summaries with their profile.
5. CLI exit codes 0, 1, 2, 3 and the `DATABASE` resolution through the
   catalog.
6. UI: per-call closed connections, no session-key collisions, no import
   cycle through `nav`, history and clear, result cleared on database
   change, download guarded against a stale query, Phase 0 fixes R-04,
   R-08, R-09 with tests, pandas 3 conversions including HUGEINT, UUID and
   DATE infinity.
7. No module-level pandas, streamlit or plotly in core; `TUNING` markers
   match the table; versions 0.2.0 everywhere; README commands exist.

## 8. Acceptance checklist re-check

| Item | Result |
|---|---|
| 1 CI green for 0.1.1 and every phase | Confirmed for all seven commits |
| 2 Phase 0 demo numbers, infinity test, tag pattern exit 1, 72-CPU AppTest | Confirmed (demo numbers by the reviewer's own run; the rest by the review pass) |
| 3 `sql` count, `INSERT` and `COPY` exit 3 | Confirmed by the reviewer |
| 4 `summarize` computed, cached, `--refresh` | Computed and cached confirmed; `--refresh` by test only |
| 5 `preview` with sort and offset | Confirmed by the reviewer |
| 6 UI pages on Windows, download, read-only error, timeout | Confirmed by the reviewer in a browser (download 730 bytes for 3 rows; timeout at the 60 s default) |
| 7 `TIMESTAMP WITH TIME ZONE` preview and query | Confirmed by the review pass |
| 8 Docs and versions | Confirmed, with the corrections of Section 4 |
