"""Small SQL helpers shared by the profiler and the query layer."""

from __future__ import annotations


def quote_ident(name: str) -> str:
    """Quote an SQL identifier: wrap in ``"`` and double embedded ``"``."""
    return '"' + name.replace('"', '""') + '"'


def qualified_name(*parts: str) -> str:
    """``"a"."b"."c"`` from the parts, each quoted."""
    return ".".join(quote_ident(part) for part in parts)
