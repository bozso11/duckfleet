"""Tags extracted from path tokens (exp_12, run-03, model=resnet).

The convention of the real flock is unknown at this stage, so the rule is a
documented tuning point: ``TUNING(tag-patterns)`` in ``docs/TUNING.md``.
"""

from __future__ import annotations

import functools
import os
import re

# TUNING(tag-patterns): the default patterns; replace via ScanConfig.tag_patterns.
# Each pattern needs the named groups ``key`` and ``value``.
DEFAULT_TAG_PATTERNS: list[str] = [
    r"(?P<key>[A-Za-z][A-Za-z0-9]*?)[_=-](?P<value>\d+)",  # exp_12, run-03, seed=7
    r"(?P<key>[A-Za-z][A-Za-z0-9]*)=(?P<value>[^/=\\]+)",  # model=resnet
]


@functools.lru_cache(maxsize=32)
def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


def extract_tags(rel_path: str, patterns: list[str]) -> dict[str, str]:
    """Return ``{key: value}`` tags found in the components of ``rel_path``.

    The path is split on ``/``, the extension of the last component is dropped,
    every pattern is applied with ``finditer`` to each component from the root
    to the leaf, later components override earlier ones, keys are lower-cased.
    """
    # TUNING(tag-patterns)
    compiled = _compile(tuple(patterns))
    parts = [part for part in rel_path.split("/") if part]
    if parts:
        parts[-1] = os.path.splitext(parts[-1])[0]
    tags: dict[str, str] = {}
    for part in parts:
        for regex in compiled:
            for match in regex.finditer(part):
                groups = match.groupdict()
                key = groups.get("key")
                value = groups.get("value")
                if key and value is not None:
                    tags[key.lower()] = value
    return tags
