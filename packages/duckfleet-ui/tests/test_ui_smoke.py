"""Smoke tests of the Overview page with Streamlit's AppTest.

The page runs through the wrapper script ``_overview_app.py`` (no
``st.navigation``) against a demo catalog; the environment variables
``DUCKFLEET_ROOT`` and ``DUCKFLEET_CACHE_DIR`` point the page at it.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

import duckfleet_ui
from duckfleet_core.config import MAX_POOL_WORKERS, ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_ui.scan_runner import ScanRunResult

APP = str(Path(__file__).with_name("_overview_app.py"))
EXPECTED_METRICS = {
    "Candidates": "13",
    "OK": "10",
    "Failed": "3",
    "Rejected": "2",
    "Strict families": "4",
    "Loose families": "3",
    "Total rows": "8,128",
    "Fleet time span": "2025-06-01 to 2026-03-01",
}


@pytest.fixture(scope="module")
def demo_catalog(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("ui")
    fleet = make_demo_fleet(base / "demo fleet é")
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


@pytest.fixture
def pointed_at(demo_catalog: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    fleet, cache = demo_catalog
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    return fleet, cache


def run_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=60).run()


def test_package_imports() -> None:
    assert duckfleet_ui.__version__


def test_overview_renders_every_section(pointed_at) -> None:
    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    assert {m.label: m.value for m in at.metric} == EXPECTED_METRICS
    assert [s.value for s in at.subheader] == [
        "Fleet",
        "Schema families",
        "Table variants",
        "Compare",
        "Time windows",
        "Health and errors",
        "Rejected files",
    ]
    assert at.sidebar.button[0].label == "Scan"
    assert [b.label for b in at.sidebar.button] == ["Scan", "Load existing catalog"]
    assert len(at.dataframe) >= 8  # fleet, families, matrix, variants, 3 diff tables, ...


def test_sidebar_defaults(pointed_at) -> None:
    fleet, cache = pointed_at
    at = run_app()
    assert at.sidebar.text_input[0].value == str(fleet)
    expected_workers = ScanConfig().resolved_workers()
    assert at.sidebar.number_input[0].value == expected_workers
    assert at.sidebar.number_input[1].value == 1000
    assert [c.value for c in at.sidebar.checkbox] == [False, False]
    assert str(fleet.resolve()) in at.sidebar.selectbox[0].options  # recent roots


def test_fleet_selection_is_kept_in_session_state(pointed_at) -> None:
    at = run_app()
    chosen = "exp_02/run_01/metrics.duckdb"
    at.selectbox(key="selected_rel_path").select(chosen).run()
    assert not at.exception
    assert at.session_state["selected_rel_path"] == chosen


def test_family_level_toggle_and_diff(pointed_at) -> None:
    at = run_app()
    at.radio(key="family_level").set_value("loose").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = at.selectbox(key="diff_a").options
    assert any(label.startswith("strict family F1") for label in labels)
    left = next(label for label in labels if label.startswith("database exp_01/run_01"))
    right = next(label for label in labels if label.startswith("database exp_02/run_01"))
    at.selectbox(key="diff_a").select(left)
    at.selectbox(key="diff_b").select(right)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    shown = " ".join(str(c.value) for c in at.caption)
    assert "A = exp_01/run_01/metrics.duckdb" in shown
    assert "B = exp_02/run_01/metrics.duckdb" in shown


def test_nothing_loaded_without_a_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKFLEET_ROOT", str(tmp_path / "never scanned"))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "empty cache"))
    at = run_app()
    assert not at.exception
    assert not at.metric
    assert any("press Scan" in info.value for info in at.info)


def test_load_existing_catalog_button(
    demo_catalog: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet, cache = demo_catalog
    monkeypatch.delenv("DUCKFLEET_ROOT", raising=False)
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    at = run_app()
    assert not at.metric  # no env root: nothing is loaded automatically
    at.sidebar.text_input(key="root_input").set_value(str(fleet))
    at.sidebar.button[1].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert {m.label: m.value for m in at.metric} == EXPECTED_METRICS


def test_load_button_without_a_catalog_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DUCKFLEET_ROOT", raising=False)
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    at = run_app()
    at.sidebar.text_input(key="root_input").set_value(str(tmp_path))
    at.sidebar.button[1].click().run()
    assert not at.exception
    assert any("No catalog" in w.value for w in at.warning)


def test_scan_with_a_missing_root_shows_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DUCKFLEET_ROOT", raising=False)
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    at = run_app()
    at.sidebar.text_input(key="root_input").set_value(str(tmp_path / "nope"))
    at.sidebar.button[0].click().run()
    assert not at.exception
    assert any("Root folder not found" in e.value for e in at.error)


@pytest.mark.timeout(180)
def test_scan_button_scans_then_shows_the_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole flow: Scan -> subprocess with progress -> rerun -> overview; then a full rescan."""
    fleet = make_demo_fleet(tmp_path / "fleet with space é")
    monkeypatch.delenv("DUCKFLEET_ROOT", raising=False)
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    at = run_app()
    assert not at.metric
    at.sidebar.text_input(key="root_input").set_value(str(fleet))
    at.sidebar.number_input(key="workers_input").set_value(2)
    at.sidebar.button[0].click()
    at.run(timeout=120)
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    assert {m.label: m.value for m in at.metric} == EXPECTED_METRICS
    assert any("Scan finished: 13 candidates, 13 profiled, 0 reused, 3 failed, 2 workers" in s.value
               for s in at.success)  # fmt: skip

    at.sidebar.checkbox(key="full_input").check()
    at.sidebar.button[0].click()
    at.run(timeout=120)
    assert not at.exception, [e.value for e in at.exception]
    assert any("13 profiled, 0 reused" in s.value for s in at.success)
    assert {m.label: m.value for m in at.metric} == EXPECTED_METRICS

    at.sidebar.checkbox(key="full_input").uncheck()
    at.sidebar.button[0].click()
    at.run(timeout=120)
    assert any("3 profiled, 10 reused" in s.value for s in at.success)  # failed files retried


def test_the_page_renders_on_a_machine_with_72_cpus(
    pointed_at, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-04: the default worker count used to exceed the input's maximum of 61."""
    monkeypatch.setattr(os, "cpu_count", lambda: 72)
    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.number_input(key="workers_input").value == MAX_POOL_WORKERS == 61
    assert {m.label: m.value for m in at.metric} == EXPECTED_METRICS


def test_a_scan_that_exits_without_a_finished_event_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-09: exit code 0 and no summary; stdout noise is shown next to the log."""
    from duckfleet_ui.views import overview

    def fake_scan(root, options, on_event):
        return ScanRunResult(
            returncode=0, stderr="log line", stray_stdout=["print() left in core", "{oops"]
        )

    monkeypatch.setattr(overview, "run_scan_subprocess", fake_scan)
    monkeypatch.delenv("DUCKFLEET_ROOT", raising=False)
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    at = run_app()
    at.sidebar.text_input(key="root_input").set_value(str(tmp_path))
    at.sidebar.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("without a finished event" in e.value for e in at.error)
    assert not any("exit code" in e.value for e in at.error)
    code_blocks = [c.value for c in at.code]
    assert "log line" in code_blocks
    assert "print() left in core\n{oops" in code_blocks


def test_single_instant_windows_get_a_minimum_width(tmp_path: Path) -> None:
    """R-08: the helper pads the bar and names the database; others stay untouched."""
    import pandas as pd

    from duckfleet_ui.views.overview import _plot_windows

    timeline = pd.DataFrame(
        {
            "rel_path": ["a", "b", "c"],
            "family_strict": ["F1", "F1", "(none)"],
            "time_min": pd.to_datetime(
                ["2026-01-01 00:00", "2026-01-01 10:00", "2026-03-01 00:00"]
            ),
            "time_max": pd.to_datetime(
                ["2026-01-08 00:00", "2026-01-01 10:00", "2026-03-01 00:00"]
            ),
        }
    )
    plot, instants = _plot_windows(timeline)
    assert instants == ["b", "c"]
    assert plot["time_max"].tolist() == [
        pd.Timestamp("2026-01-08 00:00"),
        pd.Timestamp("2026-01-01 10:00") + pd.Timedelta(hours=7, minutes=4, seconds=48),
        pd.Timestamp("2026-03-01 00:00") + pd.Timedelta(hours=7, minutes=4, seconds=48),
    ]  # 0.5 % of the 59-day fleet span
    assert plot["window"].tolist() == [
        "2026-01-01 00:00:00 to 2026-01-08 00:00:00",
        "single instant 2026-01-01 10:00:00",
        "single instant 2026-03-01 00:00:00",
    ]
    assert timeline["time_max"][1] == pd.Timestamp("2026-01-01 10:00")  # the input is not modified

    tiny = timeline.iloc[[1]].reset_index(drop=True)  # a one-row fleet: span 0, so one hour
    plot, instants = _plot_windows(tiny)
    assert instants == ["b"]
    assert plot.loc[0, "time_max"] - plot.loc[0, "time_min"] == pd.Timedelta(hours=1)


def test_the_overview_lists_single_instant_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = make_demo_fleet(tmp_path / "fleet")
    (fleet / "one").mkdir()
    con = duckdb.connect(str(fleet / "one" / "single.duckdb"))
    con.execute("CREATE TABLE e (ts TIMESTAMP, v INTEGER)")
    con.execute("INSERT INTO e VALUES ('2026-02-01 12:00:00', 1)")
    con.close()
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(tmp_path / "cache")))
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Single-instant windows are drawn with a minimum width: one/single.duckdb" in captions
    assert len(at.get("plotly_chart")) == 1
