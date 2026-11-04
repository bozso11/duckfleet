# Tuning points

Heuristics that were set without knowing the real data layout. Each has a
`# TUNING(<id>)` comment at the place in the code that implements it. When
more is known about the real fleet (folder conventions, metadata tables,
time columns), change the rule, update this table and add a
`docs/DECISIONS.md` entry if the change is a design choice.

Last updated: 2026-11-04 (Phase 0; module names are where the plan puts them).

| ID | Where | What it decides | Current rule | How to tune |
|---|---|---|---|---|
| tag-patterns | `tags.py` `extract_tags` | Which path tokens become run/experiment tags | Two regexes: `key_12`, `key-12`, `key=12` and `key=value`; applied to each path component root to leaf; later components override; keys lower-cased | Replace or extend `ScanConfig.tag_patterns`; add a config-file option when conventions are known (open: answer to proposal question 7 was "unknown at this stage") |
| temporal-types | `profiler.py` | Which column types count as temporal for time windows | `DATE`, `TIMESTAMP`, `TIMESTAMP WITH TIME ZONE`, `TIMESTAMP_S`, `TIMESTAMP_MS`, `TIMESTAMP_NS` | Add integer epoch columns by name pattern (`*_ts`, `epoch*`) or VARCHAR dates parsed on a sample |
| timezones | `profiler.py` time window merge | How aware and naive timestamps compare | Aware values converted to UTC and made naive before min/max | If the fleet is single-timezone, keep; if mixed, store offsets |
| stats-column-types | `profiler.py` stats query | Which columns get min/max and approx distinct | min/max: numeric and temporal; approx distinct: all except BLOB, STRUCT, LIST, MAP, UNION, ARRAY | Add VARCHAR min/max if useful and cheap enough |
| type-normalization | `fingerprint.py` `normalize_type` | Whether type aliases count as different in strict fingerprints | Identity: `INTEGER` and `INT4` are the same string from DuckDB already, but `DECIMAL(18,3)` and `DECIMAL(18,4)` differ | Map precision/scale away, or treat all integer widths as one, if real fleets differ only in such details |
| views-in-fingerprint | `fingerprint.py` / `ScanConfig.include_views_in_fingerprint` | Whether views are part of the structure | Excluded | Set true if views carry meaning in the fleet |
| stats-threshold | `ScanConfig.stats_row_threshold`, `stats_sample_rows` | When stats switch to sampling | 20 M rows, 1 M sample | Raise or lower after timing a real scan |
| worker-memory | `ScanConfig.worker_memory_limit_mb` | Memory per worker | `min(2 GB, RAM/2/workers)`, floor 256 MB | Adjust if real files are large or the machine is shared |
| family-labels | `fingerprint.py` `assign_families` | How families are numbered | By member count descending, then fingerprint | Stable naming across scans (persist labels) is an open option |
| time-window-scope | `profiler.py` database `time_min`/`time_max` | Which tables contribute to a file's time span | All temporal columns of all tables | Restrict to named tables or columns once the main time series table is known |
