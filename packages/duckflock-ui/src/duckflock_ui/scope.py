"""The scope control of the SQL and Plot pages: one database, or several at once (plan v0.4).

In mode ``one`` the page works on the database of the shared database selectbox, as before. In
mode ``many`` it works on a flock connection of the chosen databases (``duckflock_core.flock``):
one union view per table with a ``database`` column, plus the per-file aliases. The choice is
kept in the session state (``scope_mode``, ``scope_rel_paths``, ``persist_state="session"``) and
shared by both pages. The selection starts as the strict family of the selected database, or the
selected database alone when it has none, and is capped at L-12 (50) databases.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd
import streamlit as st

from duckflock_core.flock import MAX_MEMBERS

MODES = {"one": "One database", "many": "Several databases"}


@dataclass
class Scope:
    """What a page works on: one database, or several."""

    mode: Literal["one", "many"]
    rel_paths: list[str]  # one entry in mode "one"

    @property
    def key(self) -> str:
        """A short text that changes when the scope does: the rel_path, or a hash of the list."""
        if self.mode == "one":
            return self.rel_paths[0] if self.rel_paths else ""
        joined = "\n".join(self.rel_paths)
        return f"many:{len(self.rel_paths)}:{hashlib.sha1(joined.encode('utf-8')).hexdigest()[:10]}"


def _members(families: pd.DataFrame) -> dict[str, list[str]]:
    """``{family_id: [rel_path, ...]}`` of a ``catalog_access.families`` frame."""
    return {
        str(family): [str(member) for member in members]
        for family, members in zip(families["family_id"], families["members"], strict=True)
    }


def default_selection(
    selected: str | None, dbs: pd.DataFrame, families: pd.DataFrame
) -> tuple[list[str], str | None]:
    """``(members, note)``: the readable databases of the strict family of ``selected``.

    Only databases with status ``ok`` count. A database without a family is its own default. When
    the family is larger than L-12 the first 50 (by rel_path) are kept and ``note`` says so.
    """
    if selected is None:
        return [], None
    readable = set(dbs.loc[dbs["status"] == "ok", "rel_path"])
    family = dbs.loc[dbs["rel_path"] == selected, "family_strict"]
    family_id = family.iloc[0] if len(family) else None
    if family_id is None or pd.isna(family_id):
        return ([selected] if selected in readable else []), None
    members = sorted(m for m in _members(families).get(str(family_id), []) if m in readable)
    return _capped(members, f"Family {family_id}")


def _capped(members: list[str], label: str) -> tuple[list[str], str | None]:
    if len(members) <= MAX_MEMBERS:
        return members, None
    note = (
        f"{label} has {len(members)} readable databases; the first {MAX_MEMBERS} are selected "
        f"(limit L-12). Remove some, or choose others, to look at the rest."
    )
    return members[:MAX_MEMBERS], note


def _fill_from_family(by_family: dict[str, list[str]], readable: set[str]) -> None:
    """Callback of the Family selectbox: put the family's readable members into the selection."""
    chosen = st.session_state.get("scope_family")
    if chosen:
        members = sorted(m for m in by_family.get(chosen, []) if m in readable)
        selection, note = _capped(members, f"Family {chosen}")
        st.session_state["scope_rel_paths"] = selection
        st.session_state["scope_note"] = note
    st.session_state["scope_family"] = None  # the same family can be chosen again


def _forget_note() -> None:
    st.session_state["scope_note"] = None


def scope_control(dbs: pd.DataFrame, families: pd.DataFrame) -> Scope:
    """Draw the scope control and return the scope.

    ``dbs`` is the ``catalog_access.databases`` frame, ``families`` the strict
    ``catalog_access.families`` frame. The selected database is ``selected_rel_path`` of the
    session state (the shared database selectbox).
    """
    state = st.session_state
    selected = state.get("selected_rel_path")
    state.setdefault("scope_mode", "one")
    mode = st.radio(
        "Scope",
        list(MODES),
        format_func=MODES.__getitem__,
        key="scope_mode",
        horizontal=True,
        persist_state="session",
        help="One database, or several at once: queries and plots then span the files of the "
        "selection through one union view per table.",
    )
    if mode == "one":
        return Scope("one", [selected] if selected else [])

    readable = [str(rel) for rel in dbs.loc[dbs["status"] == "ok", "rel_path"]]
    by_family = _members(families)
    # The selection starts as the family of the selected database and follows it when another
    # database is chosen; edits of the multiselect stay while the database stays.
    if "scope_rel_paths" not in state or state.get("scope_for") != selected:
        members, note = default_selection(selected, dbs, families)
        state["scope_rel_paths"] = members
        state["scope_note"] = note
        state["scope_for"] = selected
    state["scope_rel_paths"] = [rel for rel in state["scope_rel_paths"] if rel in readable]

    left, right = st.columns([3, 1])
    left.multiselect(
        f"Databases (at most {MAX_MEMBERS})",
        readable,
        key="scope_rel_paths",
        persist_state="session",
        max_selections=MAX_MEMBERS,
        on_change=_forget_note,
        placeholder="Choose databases",
        help="Only databases that could be read in the last scan are offered.",
    )
    right.selectbox(
        "Family",
        list(by_family),
        index=None,
        key="scope_family",
        on_change=_fill_from_family,
        args=(by_family, set(readable)),
        placeholder="Fill from a family",
        format_func=lambda family: f"{family} ({len(by_family[family])})",
        help="Replaces the selection with the readable members of a strict family.",
    )
    note = state.get("scope_note")
    if note:
        st.caption(note)
    chosen = list(state["scope_rel_paths"])
    st.caption(f"{len(chosen)} of {len(readable)} readable databases selected.")
    return Scope("many", chosen)


def attach_warning(failures: list[Any]) -> str | None:
    """The text of the warning for members that could not be attached, or ``None``."""
    if not failures:
        return None
    lines = [f"- {f['rel_path']} ({f['kind']}): {f['message']}" for f in failures]
    one = len(failures) == 1
    head = (
        f"{len(failures)} database could not be attached and is left out:"
        if one
        else (f"{len(failures)} databases could not be attached and are left out:")
    )
    return head + "\n" + "\n".join(lines)


def view_warning(view_failures: list[Any]) -> str | None:
    """The text of the warning for views that could not be used, or ``None`` (review X-08).

    ``view_failures`` are ``(schema.table, message)`` pairs of ``FlockSummary``: a member view
    that does not bind (that member is left out of the union view) or a union view that could not
    be built (the table is missing from the list).
    """
    if not view_failures:
        return None
    lines = [f"- {name}: {' '.join(str(message).split())[:300]}" for name, message in view_failures]
    head = (
        "1 view could not be used; the rows it would add are left out:"
        if len(view_failures) == 1
        else f"{len(view_failures)} views could not be used; the rows they would add are left out:"
    )
    return head + "\n" + "\n".join(lines)
