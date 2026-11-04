from __future__ import annotations

from duckfleet_core.tags import DEFAULT_TAG_PATTERNS, extract_tags


def tags(rel_path: str) -> dict[str, str]:
    return extract_tags(rel_path, DEFAULT_TAG_PATTERNS)


def test_underscore_tokens() -> None:
    assert tags("exp_12/run_03/data.duckdb") == {"exp": "12", "run": "03"}


def test_key_value_tokens() -> None:
    assert tags("model=resnet/seed=7/x.duckdb") == {"model": "resnet", "seed": "7"}


def test_dash_tokens() -> None:
    assert tags("run-03/x.duckdb") == {"run": "03"}


def test_no_false_positive_on_plain_names() -> None:
    assert tags("data.duckdb") == {}
    assert tags("metrics/data.duckdb") == {}
    assert tags("2026-01-05/a.duckdb") == {}


def test_extension_of_last_component_is_dropped() -> None:
    assert tags("a/run_7.duckdb") == {"run": "7"}
    assert tags("a/b.v2.duckdb") == {}


def test_later_components_override_earlier_ones() -> None:
    assert tags("run_1/sub/run_2/x.duckdb") == {"run": "2"}


def test_keys_are_lower_cased() -> None:
    assert tags("EXP_01/Run_02/x.duckdb") == {"exp": "01", "run": "02"}


def test_custom_patterns() -> None:
    patterns = [r"(?P<key>batch)(?P<value>[A-Z]\d+)"]
    assert extract_tags("batchA12/x.duckdb", patterns) == {"batch": "A12"}
    assert extract_tags("exp_12/x.duckdb", patterns) == {}


def test_demo_fleet_paths() -> None:
    assert tags("exp_01/run_01/metrics.duckdb") == {"exp": "01", "run": "01"}
    assert tags("legacy/old.db") == {}
