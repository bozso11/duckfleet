"""Generator of a small demo flock with known properties.

Used by the tests and by the acceptance checklist; never real user data.
After a scan of the demo flock the expected result is:

* 16 files walked (15 databases or look-alikes and the stale WAL), 13 candidates,
  2 rejected (the SQLite look-alike and the truncated file), 10 ok, 3 failed
  (``corrupt`` twice: a garbage body and a garbage storage version; ``encrypted``);
* strict families of 3 (exp_01), 3 (exp_02), 2 (exp_03) and 1 (legacy) members;
  loose families of 6, 2 and 1 members;
* ``events`` has 3 strict variants; the empty database has no family and
  shows ``empty_database`` in health; ``exp_01/run_01`` shows ``wal_present``.
"""

from __future__ import annotations

import random
from pathlib import Path

import duckdb

_START = "2026-01-05 00:00:00"  # fixed, so time windows do not depend on the seed
_WEEK_US = 7 * 24 * 3600 * 1_000_000
_ROWS = 1_000
_BIG_ROWS = 200_000  # make_demo_flock(big=True): one table that needs bucketing
_BIG_SPAN_US = 30 * 24 * 3600 * 1_000_000
_CONFIG_ROWS = [("learning_rate", "0.01"), ("batch_size", "32"), ("optimizer", "adam")]


def _remove_db(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + ".wal")):
        if candidate.exists():
            candidate.unlink()


def _write_run_db(
    path: Path,
    *,
    week_index: int,
    run_id: int,
    salt: int,
    value_type: str,
    with_quality: bool,
    with_notes: bool,
    rows: int = _ROWS,
    span_us: int = _WEEK_US,
) -> None:
    """One ``metrics.duckdb``: ``events`` over a 7-day window plus ``config``."""
    _remove_db(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    step_us = span_us // rows
    offset_us = week_index * _WEEK_US
    con = duckdb.connect(str(path))
    try:
        quality_col = ", quality SMALLINT" if with_quality else ""
        con.execute(
            f"CREATE TABLE events (ts TIMESTAMP, value {value_type}, run_id INTEGER{quality_col})"
        )
        quality_sel = ", CAST(hash(i + 7) % 5 AS SMALLINT)" if with_quality else ""
        con.execute(
            f"""
            INSERT INTO events
            SELECT TIMESTAMP '{_START}'
                       + to_microseconds(CAST({offset_us} + i * {step_us} AS BIGINT)),
                   sin(i / 50.0 + {salt % 17}) + (hash(i + {salt}) % 1000) / 10000.0,
                   {run_id}{quality_sel}
            FROM range({rows}) AS t(i)
            """
        )
        con.execute("CREATE TABLE config (key VARCHAR, value VARCHAR)")
        con.executemany("INSERT INTO config VALUES (?, ?)", _CONFIG_ROWS)
        if with_notes:
            con.execute("CREATE TABLE notes (id INTEGER, text VARCHAR)")
            con.execute("INSERT INTO notes VALUES (1, 'warm start'), (2, 'lr bumped')")
    finally:
        con.close()


def make_demo_flock(dir: Path, seed: int = 0, big: bool = False) -> Path:
    """Create the demo flock under ``dir`` and return ``dir``.

    ``seed`` changes the generated values; time windows and structures are
    fixed. Existing demo files are overwritten. ``big`` adds
    ``exp_04/run_01/metrics.duckdb`` (200,000 ``events`` rows over 30 days, the
    structure of exp_01); without it the flock and its scan numbers are unchanged.
    """
    root = Path(dir)
    root.mkdir(parents=True, exist_ok=True)

    # (experiment, runs, value type, with quality + notes); windows follow one another.
    experiments = [
        ("exp_01", 3, "DOUBLE", False),
        ("exp_02", 3, "FLOAT", False),
        ("exp_03", 2, "DOUBLE", True),
    ]
    week_index = 0
    for experiment, runs, value_type, extended in experiments:
        for run in range(1, runs + 1):
            _write_run_db(
                root / experiment / f"run_{run:02d}" / "metrics.duckdb",
                week_index=week_index,
                run_id=run,
                salt=seed * 1000 + week_index,
                value_type=value_type,
                with_quality=extended,
                with_notes=extended,
            )
            week_index += 1

    if big:
        _write_run_db(
            root / "exp_04" / "run_01" / "metrics.duckdb",
            week_index=week_index,
            run_id=1,
            salt=seed * 1000 + week_index,
            value_type="DOUBLE",
            with_quality=False,
            with_notes=False,
            rows=_BIG_ROWS,
            span_us=_BIG_SPAN_US,
        )

    # Legacy file: older window, different structure, ``.db`` extension.
    legacy = root / "legacy" / "old.db"
    _remove_db(legacy)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(legacy))
    try:
        con.execute("CREATE TABLE samples (t DATE, x DOUBLE)")
        con.execute(
            f"""
            INSERT INTO samples
            SELECT DATE '2025-06-01' + CAST(i AS INTEGER),
                   (hash(i + {seed}) % 1000) / 100.0
            FROM range(100) AS t(i)
            """
        )
    finally:
        con.close()

    # SQLite look-alike: DuckDB would open it, the header sniff must reject it.
    (root / "legacy" / "sqlite_lookalike.db").write_bytes(b"SQLite format 3\x00" + bytes(4080))

    # Empty database: valid file, no tables.
    empty = root / "empty" / "empty.duckdb"
    _remove_db(empty)
    empty.parent.mkdir(parents=True, exist_ok=True)
    duckdb.connect(str(empty)).close()

    # Corrupt file: valid magic in the first 12 bytes, zeros to 4096, then random bytes.
    rng = random.Random(seed)
    real_head = (root / "exp_01" / "run_01" / "metrics.duckdb").read_bytes()[:12]
    broken = root / "broken"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "corrupt.duckdb").write_bytes(
        real_head + bytes(4096 - 12) + bytes(rng.getrandbits(8) for _ in range(8192))
    )
    # Valid magic followed by a storage version with the high bit set (R-01): the header read
    # reports no version, the profile is ``corrupt``.
    (broken / "highbit.duckdb").write_bytes(bytes(8) + b"DUCK" + b"\xff" * 8 + bytes(4096))
    # Truncated file: 100 random bytes without the magic, rejected by the sniff.
    garbage = bytes(rng.getrandbits(8) for _ in range(100))
    while garbage[8:12] == b"DUCK":  # pragma: no cover - vanishingly unlikely
        garbage = bytes(rng.getrandbits(8) for _ in range(100))
    (broken / "truncated.duckdb").write_bytes(garbage)

    # Encrypted database (R-11): the header keeps the magic, opening it without a key fails.
    encrypted = root / "secure" / "encrypted.duckdb"
    _remove_db(encrypted)
    encrypted.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{encrypted.as_posix()}' AS enc (ENCRYPTION_KEY 'demo-flock-key')")
        con.execute("CREATE TABLE enc.secret AS SELECT 1 AS i")
        con.execute("DETACH enc")  # checkpoints; no .wal sibling stays behind
    finally:
        con.close()

    # Stale WAL sibling next to the first run (health flag ``wal_present``).
    (root / "exp_01" / "run_01" / "metrics.duckdb.wal").write_bytes(b"")
    return root
