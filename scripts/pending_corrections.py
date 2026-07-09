#!/usr/bin/env python3
"""Pending-corrections intake helper (``scripts/pending_corrections.py``).

Per CLAUDE.md's "Corrections workflow (``data/pending_corrections.json``)"
section and ``analyze.yml``'s ANALYST prompt ("Step 0 -- drain corrections
FIRST, before touching any new cluster"): the actual *draining* of this
queue -- fetching each pending entry's ``evidence_url``, weighing it
against the targeted card's current text, appending a
``content/corrections.json`` entry (and setting the card's ``status:
"corrected"`` + ``correction_note``) when the evidence confirms an error,
and removing the pending entry from ``pending[]`` either way -- is carried
out by the ANALYST LLM step itself, directly via its own ``Edit``/``Write``
tools, following the prose procedure in CLAUDE.md and the workflow prompt.
**This module performs none of that fetching or judgment, and is not
imported by ``analyze.yml`` or invoked as a workflow step at all.**

What this module *is* for is the pure-code bookkeeping around that queue
that doesn't need an LLM in the loop: a schema-validated load/save round
trip (the same shape every other queue-shaped artifact in this pipeline
already uses -- ``data/verifier_stats.json`` via
``scripts/reconcile_run.py``, ``data/run_plan.json`` via
``scripts/plan_run.py``), an :func:`append_pending_correction` a future
programmatic intake source (e.g. a later ``audit.yml`` finding-writer, or
an owner-run one-off script) could call instead of hand-authoring JSON, and
a :func:`drain_pending_correction` that removes one already-resolved entry
from ``pending[]`` by id -- exactly the bookkeeping step CLAUDE.md
describes as "either way, the entry is removed from
``data/pending_corrections.json``'s ``pending[]`` once actioned." this
file is a queue, not a permanent record; ``content/corrections.json`` (see
``schemas/corrections.schema.json``) is the permanent public one and is
never touched by this module.

Follows the same three-layer shape (pure compute -> validate-then-write)
already established by ``scripts/reconcile_run.py`` and
``scripts/plan_run.py``: :func:`append_pending_correction` and
:func:`drain_pending_correction` are pure functions that return a *new*
dict and never mutate their input in place (matching ``watcher/ledger.py``'s
own upsert convention); :func:`load_pending_corrections`/
:func:`save_pending_corrections` are the schema-valid disk round trip, with
validation happening at load and at save (never silently inside the pure
in-memory helpers), the same division of responsibility
``scripts/reconcile_run.py``'s ``append_verifier_stats_row``/
``save_verifier_stats`` pair already uses.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from watcher.config import REPO_ROOT
from watcher.schema_validate import validate

__all__ = [
    "PENDING_CORRECTIONS_PATH",
    "PENDING_CORRECTIONS_VERSION",
    "empty_pending_corrections",
    "load_pending_corrections",
    "save_pending_corrections",
    "find_pending_correction",
    "append_pending_correction",
    "drain_pending_correction",
]

PENDING_CORRECTIONS_PATH = REPO_ROOT / "data" / "pending_corrections.json"
PENDING_CORRECTIONS_VERSION = 1


def empty_pending_corrections() -> dict[str, Any]:
    """The seed shape Phase 2 ships as ``data/pending_corrections.json``:
    version 1, nothing pending yet."""
    return {"version": PENDING_CORRECTIONS_VERSION, "pending": []}


# --------------------------------------------------------------------------
# load / save -- schema-valid round trip (same pattern as
# watcher/ledger.py's save_ledger/load_ledger, scripts/reconcile_run.py's
# load_verifier_stats/save_verifier_stats).
# --------------------------------------------------------------------------


def load_pending_corrections(
    path: Path | str = PENDING_CORRECTIONS_PATH,
) -> dict[str, Any]:
    """Load and schema-validate ``data/pending_corrections.json`` at
    ``path``.

    A missing file returns :func:`empty_pending_corrections` -- matches
    ``scripts/reconcile_run.py``'s ``load_verifier_stats`` convention for a
    same-shaped "not yet generated" queue artifact; not an error.
    """
    path = Path(path)
    if not path.is_file():
        return empty_pending_corrections()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    validate(data, "pending_corrections")
    return data


def save_pending_corrections(
    data: dict[str, Any], path: Path | str = PENDING_CORRECTIONS_PATH
) -> None:
    """Schema-validate then write ``data`` to ``path`` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- same committed-artifact formatting every other writer in
    this pipeline uses. Validating *before* writing means a malformed
    queue is never persisted.
    """
    validate(data, "pending_corrections")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------
# pure in-memory helpers -- never touch disk, never validate themselves
# (validation happens at load/save, matching append_verifier_stats_row's
# own division of responsibility in scripts/reconcile_run.py); a caller
# that wants to fail fast per-call may pass the result straight to
# watcher.schema_validate.validate(..., "pending_corrections") itself.
# --------------------------------------------------------------------------


def find_pending_correction(
    data: dict[str, Any], entry_id: str
) -> dict[str, Any] | None:
    """The ``pending[]`` entry whose ``id`` == ``entry_id``, or ``None`` if
    no such entry exists (already drained, or never present)."""
    for entry in data.get("pending", []):
        if entry["id"] == entry_id:
            return entry
    return None


def append_pending_correction(
    data: dict[str, Any], entry: dict[str, Any]
) -> dict[str, Any]:
    """Return a *new* pending_corrections dict with ``entry`` appended to
    ``pending[]`` -- never mutates ``data`` in place, matching
    ``watcher/ledger.py``'s own upsert convention.

    Raises ``ValueError`` if ``entry["id"]`` already exists in
    ``pending[]``: an id collision here would silently shadow one pending
    item behind another rather than adding a second, distinct one; the
    caller (a future intake source) should mint a fresh id instead, the
    same discipline ``scripts/plan_run.py``'s ``compute_proposed_card_id``
    already applies to card ids.
    """
    pending = list(data.get("pending", []))
    if any(existing["id"] == entry["id"] for existing in pending):
        raise ValueError(
            f"pending correction id {entry['id']!r} already exists in pending[]"
        )
    pending.append(entry)
    return {
        "version": data.get("version", PENDING_CORRECTIONS_VERSION),
        "pending": pending,
    }


def drain_pending_correction(
    data: dict[str, Any], entry_id: str
) -> dict[str, Any]:
    """Return a *new* pending_corrections dict with the ``pending[]``
    entry whose ``id`` == ``entry_id`` removed.

    This is the pure-code half of CLAUDE.md's "either way, the entry is
    removed from ``data/pending_corrections.json``'s ``pending[]`` once
    actioned" rule -- the LLM analyst does the fetch-and-judge half itself
    (confirming or rejecting the candidate correction), elsewhere, per the
    corrections workflow's own prose procedure; this function is only the
    bookkeeping step of removing an entry once that judgment has already
    been made.

    Idempotent by design: draining an ``entry_id`` that is already absent
    (already drained by an earlier call, or never present at all) is a
    no-op -- returns an equivalent dict rather than raising -- matching
    this pipeline's general preference for idempotent re-application over
    erroring on an already-settled state (cf. ``watcher/ledger.py``'s own
    upsert, which re-affirms rather than errors on a hash already seen).
    """
    pending = [entry for entry in data.get("pending", []) if entry["id"] != entry_id]
    return {
        "version": data.get("version", PENDING_CORRECTIONS_VERSION),
        "pending": pending,
    }
