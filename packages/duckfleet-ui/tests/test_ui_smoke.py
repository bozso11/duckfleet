"""Smoke tests of the Overview page with Streamlit's AppTest.

The page runs through the wrapper script ``_overview_app.py`` (no
``st.navigation``) against a demo catalog; the environment variables
``DUCKFLEET_ROOT`` and ``DUCKFLEET_CACHE_DIR`` point the page at it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import duckfleet_ui
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet

APP = str(Path(__file__).with_name("_overview_app.py"))
EXPECTED_METRICS = {
    "Candidates": "11",
    "OK": "10",
    "Failed": "1",
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
    assert any("Scan finished: 11 candidates, 11 profiled, 0 reused, 1 failed, 2 workers" in s.value
               for s in at.success)  # fmt: skip

    at.sidebar.checkbox(key="full_input").check()
    at.sidebar.button[0].click()
    at.run(timeout=120)
    assert not at.exception, [e.value for e in at.exception]
    assert any("11 profiled, 0 reused" in s.value for s in at.success)
    assert {m.label: m.value for m in at.metric} == EXPECTED_METRICS

    at.sidebar.checkbox(key="full_input").uncheck()
    at.sidebar.button[0].click()
    at.run(timeout=120)
    assert any("1 profiled, 10 reused" in s.value for s in at.success)  # failed file retried
