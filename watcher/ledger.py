"""Ledger idempotency (watcher/ledger.py).

Implements Phase 1's "diff vs data/ledger.json" step from CLAUDE.md's
daily-loop diagram (``fetch all feeds -> cluster & rank -> diff vs
data/ledger.json -> ...``): given this run's ranked clusters
(watcher/ranking.py's ``RankedCluster`` instances) and the ledger loaded
from ``data/ledger.json``, decide which clusters are new-or-still-
unpublished (``data/queue.json``'s concern, a later commit) and bring
the ledger's own entries up to date for this run -- without ever losing
or duplicating a ``card_id``.

Two moving parts:

- **Filtering** (:func:`unpublished_clusters`): a cluster already
  carrying a ledger entry whose ``card_id`` is non-null has already been
  published -- drop it. A cluster whose entry has ``status: "dropped"``
  (Phase 2's verifier-drop terminal state -- ``card_id`` null by
  definition there too) is also dropped, so a permanently-rejected
  cluster_hash can never be silently re-queued just because its
  ``card_id`` happens to be null like a genuinely fresh one's. Everything
  else (a hash never seen before, or one seen before but still awaiting a
  Phase 2 card) survives for this run.
- **Upserting** (:func:`upsert_entries`): for each surviving cluster,
  either create a fresh ``queued``/``card_id: null`` entry (``first_seen
  == last_seen == today``) or, if the hash already has an entry, only
  bump ``last_seen`` and refresh ``member_urls`` -- ``card_id``/
  ``status``/``first_seen`` are never touched on an existing entry. This
  is exactly what makes re-running the watcher against unchanged
  upstream data a no-op at the *entry-count* level -- the Phase 1
  acceptance criterion ("a second identical run adds zero new ledger
  keys") -- even though ``last_seen`` legitimately changes every run.

:func:`apply_run` composes both steps: filter, then upsert only the
survivors (an already-published cluster's entry is left completely
untouched, not even ``last_seen``-bumped).

``cluster_hash`` itself is **not** recomputed here -- ``watcher/
clustering.py`` already owns that formula (:func:`~watcher.clustering.
compute_cluster_hash`: the sha256 hex digest of a cluster's sorted,
normalized member URLs, newline-joined) and every ``RankedCluster`` this
module consumes already carries it (``schemas/ledger.schema.json``'s own
description names ``watcher/clustering.py`` as the hash's owner). This
module re-imports and reuses that single implementation -- never a
second, independently-maintained hash formula -- purely to recover it
from a plain list of URLs where useful (e.g. this file's own tests).

NOTE on scope (logged in IMPROVEMENT_BACKLOG.md): the task description
this module was built against also mentions a ``times_seen`` counter and
``first_seen_at``/``last_seen_at`` field names. ``schemas/ledger.
schema.json`` was already fixed in an earlier commit with
``additionalProperties: false`` and no such field -- only ``card_id``/
``status``/``first_seen``/``last_seen``/``member_urls``/
``verifier_outcome`` -- and touching that schema is outside this
commit's scope. ``first_seen``/``last_seen`` (bumped, never duplicated,
on every re-run) already satisfy the actual idempotency guarantee; a
"how many times has this been seen" counter isn't needed to prove it,
and adding an unschemad field would break every schema-valid load/save
round trip this module is required to pass. Logged, not silently
dropped.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from watcher.clustering import compute_cluster_hash
from watcher.config import REPO_ROOT
from watcher.models import normalize_url
from watcher.schema_validate import validate

# Re-exported for callers that only have a plain list of (already
# normalized) member URLs on hand -- e.g. re-deriving a persisted ledger
# entry's key from its own ``member_urls`` -- without needing a live
# Cluster/RankedCluster instance. See the module docstring's note on
# cluster_hash ownership.
__all__ = [
    "LEDGER_PATH",
    "LEDGER_VERSION",
    "compute_cluster_hash",
    "empty_ledger",
    "load_ledger",
    "save_ledger",
    "unpublished_clusters",
    "upsert_entries",
    "apply_run",
]

LEDGER_PATH = REPO_ROOT / "data" / "ledger.json"
LEDGER_VERSION = 1


# --------------------------------------------------------------------------
# member URL recovery
# --------------------------------------------------------------------------


def _member_urls(ranked_cluster: Any) -> list[str]:
    """Sorted, normalized member URLs for one ranked cluster.

    Prefers the underlying cluster's own ``normalized_urls`` (``watcher.
    clustering.Cluster`` exposes this) and falls back to normalizing each
    item's raw ``url`` directly for any minimal duck-typed cluster-like
    that only exposes ``.items`` -- the same defensive-fallback posture
    ``watcher/ranking.py`` uses for its own ``cluster_hash`` tie-break.
    """
    cluster = getattr(ranked_cluster, "cluster", ranked_cluster)
    normalized = getattr(cluster, "normalized_urls", None)
    if normalized is None:
        normalized = {normalize_url(item.url) for item in cluster.items}
    return sorted(normalized)


# --------------------------------------------------------------------------
# load / save -- schema-valid round trip
# --------------------------------------------------------------------------


def empty_ledger() -> dict[str, Any]:
    """The seed shape Phase 1 ships as ``data/ledger.json``: version 1,
    no entries yet."""
    return {"version": LEDGER_VERSION, "entries": {}}


def load_ledger(path: Path | str = LEDGER_PATH) -> dict[str, Any]:
    """Load and schema-validate the ledger at ``path``.

    A missing file returns :func:`empty_ledger` -- the very first
    watcher run against a fresh checkout has no ledger yet, and that is
    not an error.
    """
    path = Path(path)
    if not path.is_file():
        return empty_ledger()
    with path.open("r", encoding="utf-8") as f:
        ledger = json.load(f)
    validate(ledger, "ledger")
    return ledger


def save_ledger(ledger: dict[str, Any], path: Path | str = LEDGER_PATH) -> None:
    """Schema-validate then write ``ledger`` to ``path`` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- committed data artifacts get human-readable diffs, unlike
    the transient ``data/.cache/`` entries ``watcher/http.py`` writes
    compactly. Validating *before* writing means a malformed ledger is
    never persisted, matching every other writer in this pipeline (see
    ``watcher/schema_validate.py``'s own module docstring).
    """
    validate(ledger, "ledger")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------
# filter + upsert
# --------------------------------------------------------------------------


def unpublished_clusters(
    ranked_clusters: Iterable[Any], ledger: dict[str, Any]
) -> list[Any]:
    """Drop clusters whose ledger entry is already finalized -- either
    already published (non-null ``card_id``) or permanently ``"dropped"``
    (Phase 2's verifier-drop terminal state, whose ``card_id`` is itself
    schema-enforced to stay ``null`` -- see ``schemas/ledger.schema.json``)
    -- keeping everything else: a hash never seen before, or one seen
    before but still awaiting a card (``status: "queued"``).

    The explicit ``status == "dropped"`` check is required *in addition
    to* the ``card_id is not None`` check: a dropped entry's ``card_id``
    is null by definition, so relying on ``card_id`` alone would treat a
    permanently-dropped cluster_hash as still "unpublished" and re-queue
    it (into ``data/queue.json`` and then ``data/run_plan.json``) the
    next time its exact member URLs resurface in a fetch -- silently
    reviving a cluster the verifier already permanently rejected, in
    direct contradiction of ``schemas/ledger.schema.json``'s own
    "stays permanently null for clusters the verifier drops" guarantee.
    """
    entries = ledger.get("entries", {})
    survivors = []
    for ranked_cluster in ranked_clusters:
        entry = entries.get(ranked_cluster.cluster_hash)
        if entry is not None and (
            entry.get("card_id") is not None or entry.get("status") == "dropped"
        ):
            continue
        survivors.append(ranked_cluster)
    return survivors


def upsert_entries(
    ranked_clusters: Iterable[Any],
    ledger: dict[str, Any],
    *,
    now: datetime | date | None = None,
) -> dict[str, Any]:
    """Return a *new* ledger dict with one entry upserted per ranked
    cluster.

    A brand-new ``cluster_hash`` gets a fresh entry: ``card_id: null``,
    ``status: "queued"``, ``first_seen == last_seen == today``. An
    existing ``cluster_hash`` only has ``last_seen``/``member_urls``
    refreshed -- ``card_id``/``status``/``first_seen`` are never
    modified here, so a hash that already has a card (or was already
    dropped) keeps that state.

    Does not mutate ``ledger`` in place, so a caller always still has
    the prior state available for comparison (this commit's own
    idempotency tests rely on that).
    """
    if isinstance(now, datetime):
        today = now.date().isoformat()
    elif isinstance(now, date):
        today = now.isoformat()
    else:
        today = datetime.now(timezone.utc).date().isoformat()

    entries = dict(ledger.get("entries", {}))
    for ranked_cluster in ranked_clusters:
        cluster_hash = ranked_cluster.cluster_hash
        member_urls = _member_urls(ranked_cluster)
        existing = entries.get(cluster_hash)
        if existing is None:
            entries[cluster_hash] = {
                "card_id": None,
                "status": "queued",
                "first_seen": today,
                "last_seen": today,
                "member_urls": member_urls,
            }
        else:
            updated = dict(existing)
            updated["last_seen"] = today
            updated["member_urls"] = member_urls
            entries[cluster_hash] = updated

    return {"version": ledger.get("version", LEDGER_VERSION), "entries": entries}


def apply_run(
    ranked_clusters: Iterable[Any],
    ledger: dict[str, Any],
    *,
    now: datetime | date | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """The full per-run flow: drop already-published clusters, then
    upsert ledger entries for the survivors.

    Returns ``(surviving_ranked_clusters, new_ledger)`` -- the survivors
    are what a later ``watcher/queue_writer.py`` (out of this commit's
    scope) writes to ``data/queue.json``; ``new_ledger`` is what gets
    schema-validated and saved back to ``data/ledger.json`` via
    :func:`save_ledger`.
    """
    survivors = unpublished_clusters(ranked_clusters, ledger)
    new_ledger = upsert_entries(survivors, ledger, now=now)
    return survivors, new_ledger
