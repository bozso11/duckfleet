from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from duckfleet_core import catalog
from duckfleet_core.catalog import CatalogReader, build_catalog
from duckfleet_core.config import ScanConfig
from duckfleet_core.discovery import RejectedFile, discover
from duckfleet_core.fingerprint import TableDiff, TypeChange
from duckfleet_core.models import DatabaseProfile, ScanSummary
from duckfleet_core.profiler import profile_database
from duckfleet_core.tempdir import file_temp_dir, process_temp_dir

RUN1 = "exp_01/run_01/metrics.duckdb"
RUN2 = "exp_02/run_01/metrics.duckdb"


@pytest.fixture(scope="module")
def demo_profiles(demo_fleet: Path) -> list[DatabaseProfile]:
    config = ScanConfig().model_dump(mode="json")
    return [
        DatabaseProfile.model_validate(profile_database(str(c.abs_path), c.rel_path, config))
        for c in discover(demo_fleet, ScanConfig()).candidates
    ]


def make_summary(config: ScanConfig | None = None) -> ScanSummary:
    return ScanSummary(
        run_id="run-1",
        root="/data",
        started_at="2026-01-01T10:00:00+00:00",
        finished_at="2026-01-01T10:00:05+00:00",
        duckfleet_version="0.1.0",
        duckdb_version=duckdb.__version__,
        workers=3,
        files_walked=16,
        files_candidates=13,
        files_rejected=2,
        files_profiled=13,
        files_reused=0,
        files_failed=3,
        truncated=False,
        catalog_path="x",
        config=config or ScanConfig(),
    )


REJECTED = [
    RejectedFile("legacy/sqlite_lookalike.db", "not_duckdb_header"),
    RejectedFile("broken/truncated.duckdb", "not_duckdb_header"),
]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, demo_profiles) -> CatalogReader:
    folder = tmp_path_factory.mktemp("catalog")
    build_catalog(
        demo_profiles, make_summary(), REJECTED, folder / "catalog.duckdb", folder / "c.tmp"
    )
    return CatalogReader(folder / "catalog.duckdb")


def count(reader: CatalogReader, table: str) -> int:
    return reader._rows(f"SELECT count(*) AS n FROM {table}")[0]["n"]


def test_table_row_counts(built: CatalogReader) -> None:
    assert count(built, "scan_runs") == 1
    assert count(built, "databases") == 13
    # events x8, config x8, notes x2, samples x1; the corrupt, encrypted and empty files have none
    assert count(built, "tables") == 19
    assert count(built, "families") == 4 + 3
    assert count(built, "table_variants") == 6  # events x3, config, notes, samples
    assert count(built, "rejected") == 2


def test_column_row_count_matches_the_profiles(built: CatalogReader, demo_profiles) -> None:
    expected = sum(len(t.columns) for p in demo_profiles for t in p.tables)
    assert count(built, "columns") == expected > 0


def test_schema_types(built: CatalogReader) -> None:
    types = {
        (r["table_name"], r["column_name"]): r["data_type"]
        for r in built._rows(
            "SELECT table_name, column_name, data_type FROM duckdb_columns() WHERE NOT internal"
        )
    }
    assert types[("databases", "time_min")] == "TIMESTAMP"
    assert types[("databases", "size_bytes")] == "BIGINT"
    assert types[("databases", "storage_version")] == "UBIGINT"  # R-01
    assert types[("databases", "profile_seconds")] == "DOUBLE"
    assert types[("scan_runs", "truncated")] == "BOOLEAN"
    assert types[("columns", "sampled")] == "BOOLEAN"
    assert types[("tables", "row_count")] == "BIGINT"
    assert types[("families", "n_members")] == "INTEGER"


def test_scan_run_row(built: CatalogReader) -> None:
    run = built.scan_run()
    assert run is not None
    assert (run["run_id"], run["workers"], run["files_candidates"], run["files_failed"]) == (
        "run-1",
        3,
        13,
        3,
    )
    assert run["started_at"] == datetime(2026, 1, 1, 10, 0, 0)  # naive UTC
    assert json.loads(run["config_json"])["max_files"] == 1000


def test_databases_rows(built: CatalogReader) -> None:
    rows = {r["rel_path"]: r for r in built.databases()}
    assert len(rows) == 13
    run1 = rows[RUN1]
    assert run1["status"] == "ok" and run1["has_wal"] is True
    assert run1["tags"] == {"exp": "01", "run": "01"}
    assert run1["health"] == ["wal_present"]
    assert (run1["n_tables"], run1["n_views"], run1["n_schemas"]) == (2, 0, 1)
    assert run1["total_rows"] == 1003
    assert run1["family_strict"] and run1["family_loose"]
    assert isinstance(run1["time_min"], datetime) and run1["time_min"] < run1["time_max"]
    assert isinstance(run1["profiled_at"], datetime)
    assert "tags_json" not in run1 and "health_json" not in run1

    corrupt = rows["broken/corrupt.duckdb"]
    assert corrupt["status"] == "error" and corrupt["error_type"] == "corrupt"
    assert corrupt["error_message"]
    assert corrupt["fp_strict"] is None and corrupt["family_strict"] is None
    assert corrupt["time_min"] is None and corrupt["n_tables"] == 0

    highbit = rows["broken/highbit.duckdb"]  # R-01: magic, then a garbage storage version
    assert highbit["status"] == "error" and highbit["error_type"] == "corrupt"
    assert highbit["storage_version"] is None
    encrypted = rows["secure/encrypted.duckdb"]  # R-11
    assert encrypted["status"] == "error" and encrypted["error_type"] == "encrypted"
    assert encrypted["storage_version"] is not None and encrypted["n_tables"] == 0

    empty = rows["empty/empty.duckdb"]
    assert empty["status"] == "ok" and empty["health"] == ["empty_database"]
    assert empty["fp_strict"] is None and empty["family_loose"] is None
    assert empty["n_tables"] == 0 and empty["total_rows"] == 0


def test_tables_and_columns(built: CatalogReader) -> None:
    tables = built.tables(RUN1)
    assert [(t["table_name"], t["kind"], t["row_count"], t["n_columns"]) for t in tables] == [
        ("config", "table", 3, 2),
        ("events", "table", 1000, 3),
    ]
    assert len(built.tables()) == 19
    events = built.columns(RUN1, "main", "events")
    assert [(c["ordinal"], c["column_name"], c["data_type"]) for c in events] == [
        (1, "ts", "TIMESTAMP"),
        (2, "value", "DOUBLE"),
        (3, "run_id", "INTEGER"),
    ]
    ts = events[0]
    assert ts["is_temporal"] is True and ts["is_numeric"] is False
    assert ts["non_null_count"] == 1000 and ts["min_value"] == "2026-01-05T00:00:00"
    assert ts["sampled"] is False and ts["nullable"] is True
    assert len(built.columns(RUN1)) == 5
    assert built.columns("nope") == []


def test_families(built: CatalogReader) -> None:
    strict = built.families("strict")
    assert [f["n_members"] for f in strict] == [3, 3, 2, 1]
    assert [f["family_id"] for f in strict] == ["F1", "F2", "F3", "F4"]
    assert all(len(f["members"]) == f["n_members"] for f in strict)
    assert strict[0]["canonical"][0][:2] in (["main", "config"], ["main", "events"])
    assert strict[0]["n_tables"] == 2 and strict[0]["level"] == "strict"
    loose = built.families("loose")
    assert [(f["family_id"], f["n_members"]) for f in loose] == [("L1", 6), ("L2", 2), ("L3", 1)]
    assert len(loose[0]["members"]) == 6

    members = built.family_members("strict", strict[2]["family_id"])
    assert sorted(m["rel_path"] for m in members) == [
        "exp_03/run_01/metrics.duckdb",
        "exp_03/run_02/metrics.duckdb",
    ]
    assert built.family_members("loose", "L9") == []


def test_family_table_matrix(built: CatalogReader) -> None:
    strict = built.family_table_matrix("strict")
    cells = {(r["family_id"], r["table_name"]): r["n_members_with_table"] for r in strict}
    families = {f["family_id"]: f for f in built.families("strict")}
    for family_id, family in families.items():
        for _schema, table, _columns in family["canonical"]:
            assert cells[(family_id, table)] == family["n_members"]
    assert {t for (_f, t) in cells} == {"events", "config", "notes", "samples"}
    assert len(cells) == 2 + 2 + 3 + 1  # F1, F2: events+config; F3: +notes; F4: samples
    loose = built.family_table_matrix("loose")
    assert {(r["family_id"], r["table_name"]): r["n_members_with_table"] for r in loose}[
        ("L1", "events")
    ] == 6


def test_table_variants(built: CatalogReader) -> None:
    variants = built.table_variants()
    events = [v for v in variants if v["table_name"] == "events"]
    assert sorted(v["n_members"] for v in events) == [2, 3, 3]
    assert all(v["n_columns"] == len(v["columns"]) for v in variants)
    assert [v["n_members"] for v in variants if v["table_name"] == "config"] == [8]


def test_timeline(built: CatalogReader) -> None:
    rows = built.timeline()
    assert len(rows) == 9  # 8 runs + legacy; the empty and the corrupt file have no window
    assert rows[0]["rel_path"] == "legacy/old.db"
    starts = [r["time_min"] for r in rows]
    assert starts == sorted(starts)
    assert [r["rel_path"] for r in rows[1:]] == [
        f"exp_0{e}/run_0{r}/metrics.duckdb"
        for e, r in [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (3, 1), (3, 2)]
    ]
    assert all(r["time_min"] and r["time_max"] for r in rows)


def test_health_and_rejected(built: CatalogReader) -> None:
    health = {h["rel_path"]: h for h in built.health()}
    assert set(health) == {
        RUN1,
        "empty/empty.duckdb",
        "broken/corrupt.duckdb",
        "broken/highbit.duckdb",
        "secure/encrypted.duckdb",
    }
    assert health[RUN1]["health"] == ["wal_present"]
    assert health["broken/corrupt.duckdb"]["error_type"] == "corrupt"
    assert health["broken/highbit.duckdb"]["error_type"] == "corrupt"
    assert health["secure/encrypted.duckdb"]["error_type"] == "encrypted"
    assert health["empty/empty.duckdb"]["health"] == ["empty_database"]
    assert {(r["rel_path"], r["reason"]) for r in built.rejected()} == {
        ("legacy/sqlite_lookalike.db", "not_duckdb_header"),
        ("broken/truncated.duckdb", "not_duckdb_header"),
    }


def test_diff(built: CatalogReader) -> None:
    diff = built.diff(RUN1, RUN2)
    assert diff.changed == [
        TableDiff("main.events", [], [], [TypeChange("value", "DOUBLE", "FLOAT")])
    ]
    assert diff.only_in_a == [] and diff.only_in_b == []
    assert built.diff(RUN1, RUN1).identical
    three = built.diff(RUN1, "exp_03/run_01/metrics.duckdb")
    assert three.only_in_b == ["main.notes"]
    assert three.changed[0].columns_only_in_b == ["quality"]


def test_df_returns_a_dataframe(built: CatalogReader) -> None:
    pandas = pytest.importorskip("pandas")
    frame = built.df("SELECT rel_path, size_bytes FROM databases WHERE status = ?", ["ok"])
    assert isinstance(frame, pandas.DataFrame)
    assert len(frame) == 10


def test_catalog_keeps_a_storage_version_above_the_signed_range(
    tmp_path: Path, demo_profiles
) -> None:
    """R-01: a profile with a uint64 storage version above 2**63 must not break the build."""
    profile = demo_profiles[0].model_copy(update={"storage_version": 2**64 - 1})
    target = tmp_path / "c.duckdb"
    build_catalog([profile], make_summary(), [], target, tmp_path / "c.tmp")
    (row,) = CatalogReader(target).databases()
    assert row["storage_version"] == 2**64 - 1


def test_reader_never_keeps_the_file_open(built: CatalogReader, tmp_path: Path) -> None:
    copy = tmp_path / "copy.duckdb"
    copy.write_bytes(built.path.read_bytes())
    reader = CatalogReader(copy)
    reader.databases()
    reader.families("strict")
    copy.unlink()  # would fail on Windows if a handle were still open
    assert not copy.exists()


def test_missing_catalog_is_reported(tmp_path: Path) -> None:
    reader = CatalogReader(tmp_path / "none.duckdb")
    assert not reader.exists()
    with pytest.raises(duckdb.Error):
        reader.databases()


def test_rebuild_replaces_the_catalog_and_cleans_up(tmp_path: Path, demo_profiles) -> None:
    target, tmp = tmp_path / "catalog.duckdb", tmp_path / "catalog.duckdb.tmp"
    build_catalog(demo_profiles, make_summary(), REJECTED, target, tmp)
    assert target.exists() and not tmp.exists()
    tmp.write_bytes(b"stale build target from a crashed scan")
    tmp.with_name(tmp.name + ".wal").write_bytes(b"stale")
    build_catalog(demo_profiles[:3], make_summary(), [], target, tmp)  # os.replace over a catalog
    assert CatalogReader(target).databases().__len__() == 3
    assert CatalogReader(target).rejected() == []
    assert not tmp.exists() and not tmp.with_name(tmp.name + ".wal").exists()


def test_empty_fleet_builds_an_empty_catalog(tmp_path: Path) -> None:
    target = tmp_path / "c.duckdb"
    build_catalog([], make_summary(), [], target, tmp_path / "c.tmp")
    reader = CatalogReader(target)
    assert reader.databases() == [] and reader.families("strict") == []
    assert reader.timeline() == [] and reader.health() == []
    assert reader.family_table_matrix("loose") == []
    assert reader.scan_run() is not None


def test_replace_is_retried_then_raises(
    tmp_path: Path, demo_profiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog, "REPLACE_DELAY_S", 0.0)
    calls = []

    def always_denied(src, dst):
        calls.append(1)
        raise PermissionError("held open")

    monkeypatch.setattr(catalog.os, "replace", always_denied)
    target = tmp_path / "catalog.duckdb"
    with pytest.raises(PermissionError, match="catalog.duckdb"):
        build_catalog(demo_profiles[:1], make_summary(), [], target, tmp_path / "c.tmp")
    assert len(calls) == catalog.REPLACE_RETRIES + 1 == 6


def test_replace_succeeds_after_transient_failures(
    tmp_path: Path, demo_profiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog, "REPLACE_DELAY_S", 0.0)
    real_replace = catalog.os.replace
    attempts = []

    def flaky(src, dst):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("held open")
        real_replace(src, dst)

    monkeypatch.setattr(catalog.os, "replace", flaky)
    target = tmp_path / "catalog.duckdb"
    build_catalog(demo_profiles[:2], make_summary(), [], target, tmp_path / "c.tmp")
    assert len(attempts) == 3
    assert len(CatalogReader(target).databases()) == 2


def test_catalog_held_open_by_a_reader(
    tmp_path: Path, demo_profiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows an open handle blocks the swap; on Linux the swap just works."""
    monkeypatch.setattr(catalog, "REPLACE_DELAY_S", 0.01)
    target = tmp_path / "catalog.duckdb"
    build_catalog(demo_profiles[:1], make_summary(), [], target, tmp_path / "c.tmp")
    holder = duckdb.connect(str(target), read_only=True)
    try:
        if sys.platform == "win32":
            with pytest.raises(PermissionError, match="held open by another process"):
                build_catalog(demo_profiles[:2], make_summary(), [], target, tmp_path / "c.tmp")
        else:
            build_catalog(demo_profiles[:2], make_summary(), [], target, tmp_path / "c.tmp")
    finally:
        holder.close()
    build_catalog(demo_profiles[:2], make_summary(), [], target, tmp_path / "c.tmp")
    assert len(CatalogReader(target).databases()) == 2


def test_non_ascii_values_survive(tmp_path: Path, demo_profiles) -> None:
    profile = demo_profiles[0].model_copy(
        update={"rel_path": "données/été 1/ünï.duckdb", "tags": {"clé": "valeur é"}}
    )
    target = tmp_path / "c.duckdb"
    build_catalog(
        [profile], make_summary(), [RejectedFile("ç/ñ.db", "unreadable")], target, tmp_path / "t"
    )
    reader = CatalogReader(target)
    (row,) = reader.databases()
    assert row["rel_path"] == "données/été 1/ünï.duckdb"
    assert row["tags"] == {"clé": "valeur é"}
    assert reader.rejected() == [{"rel_path": "ç/ñ.db", "reason": "unreadable"}]


def test_the_catalog_writer_connects_with_limits_and_a_cache_temp_directory(
    tmp_path: Path, demo_profiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-07 and V-01: threads, memory and the temp directory of the writer connection."""
    configs: list[dict] = []
    real_connect = duckdb.connect

    def spy(*args, **kwargs):
        configs.append(kwargs.get("config", {}))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(catalog.duckdb, "connect", spy)
    cache = tmp_path / "cache"
    build_catalog(
        demo_profiles,
        make_summary(ScanConfig(cache_dir=str(cache))),
        REJECTED,
        tmp_path / "catalog.duckdb",
        tmp_path / "catalog.duckdb.tmp",
    )
    assert configs == [
        {
            "threads": 2,
            "memory_limit": "1GB",
            "temp_directory": str(
                file_temp_dir(process_temp_dir(cache), tmp_path / "catalog.duckdb.tmp")
            ),
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        }
    ]


# ---------------------------------------------------------------- review P-13: reader temp dir


def test_the_reader_connects_with_a_cache_temp_directory_of_its_own(
    tmp_path: Path, demo_profiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P-13: a spilling catalog query would create ``catalog.duckdb.tmp`` beside the catalog,
    the name of the writer's build target; the reader gets the catalog's leaf under the cache."""
    cache = tmp_path / "cache"
    target = cache / "roots" / "abc123" / "catalog.duckdb"
    build_catalog(
        demo_profiles,
        make_summary(ScanConfig(cache_dir=str(cache))),
        REJECTED,
        target,
        target.with_name("catalog.duckdb.tmp"),
    )
    configs: list[dict] = []
    real_connect = duckdb.connect

    def spy(*args, **kwargs):
        configs.append(kwargs.get("config", {}))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(catalog.duckdb, "connect", spy)
    expected = str(file_temp_dir(process_temp_dir(cache), target))
    reader = CatalogReader(target)
    reader.databases()
    reader.tables()
    assert len(configs) == 2 and configs[0] == configs[1]  # one config per catalog (D-021)
    assert configs[0] == {
        "threads": 2,
        "memory_limit": "1GB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "temp_directory": expected,
    }
    assert Path(expected).is_dir()
    assert CatalogReader(target).temp_root() == process_temp_dir(cache)
    # an explicit root wins, and a catalog outside the standard layout uses the default cache
    other = CatalogReader(target, temp_root=tmp_path / "elsewhere")
    assert other.temp_root() == tmp_path / "elsewhere"
    loose = tmp_path / "loose.duckdb"
    build_catalog(
        demo_profiles,
        make_summary(ScanConfig(cache_dir=str(cache))),
        REJECTED,
        loose,
        loose.with_name("loose.duckdb.tmp"),
    )
    default = catalog.core_config.default_cache_dir()  # the test run's folder (conftest)
    assert CatalogReader(loose).temp_root() == process_temp_dir(default)
    assert not (tmp_path / "catalog.duckdb.tmp").exists() and not list(
        target.parent.glob("*.tmp")
    )  # nothing is written beside the catalog


def test_an_unwritable_cache_does_not_stop_a_read(
    tmp_path: Path, demo_profiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "cache" / "roots" / "k" / "catalog.duckdb"
    build_catalog(
        demo_profiles,
        make_summary(ScanConfig(cache_dir=str(tmp_path / "cache"))),
        REJECTED,
        target,
        target.with_name("catalog.duckdb.tmp"),
    )

    def refuse(*args, **kwargs):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(catalog, "ensure_file_temp_dir", refuse)
    assert len(CatalogReader(target).databases()) == 13
