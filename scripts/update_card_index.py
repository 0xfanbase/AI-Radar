#!/usr/bin/env python3
"""Card index regenerator (``scripts/update_card_index.py``).

Per the approved build plan's Phase 2 section: after the ANALYST/VERIFIER
steps have written (or dropped) this run's ``content/cards/<id>.json``
files, this script regenerates ``content/cards/index.json`` from scratch by
globbing every currently-existing ``content/cards/*.json`` file and
extracting the flat manifest subset of fields ``schemas/card_index.
schema.json`` defines: ``{id, date, headline, topics, lexicon_terms,
status}``. Full prose/citations stay in the per-card file; the index exists
so the frontend/analyst never has to open every card just to list or link
them.

Two edge cases this turn's scope calls out explicitly:

- **A "dropped" card** (one that was published on a previous run but whose
  file has since been deleted -- e.g. a correction that retracted it, or
  simply a file removed by hand) is handled with no special-case code at
  all: :func:`iter_card_paths` only ever globs *currently-existing* files,
  so a deleted card is simply absent from that glob result and therefore
  absent from the regenerated index. There is nothing to "remove" -- the
  index is rebuilt from the current filesystem state every time, never
  patched incrementally.
- **An empty (or not-yet-created) ``content/cards/`` directory** (no cards
  published yet -- true of this repo today, before the Phase 2 analyst has
  ever run) produces a valid, schema-conformant *empty* index --
  ``{"version": 1, "cards": []}`` -- not an error and not a missing file.

This module is imported directly by ``scripts/reconcile_run.py`` (never
re-implemented there) for the "regenerate content/cards/index.json" step of
its own post-run reconciliation.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Allow running as `python scripts/update_card_index.py` (no package
# install / no `-m` needed) -- same sys.path trick every other script in
# this repo uses (scripts/plan_run.py, scripts/run_watcher_live.py, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watcher.config import REPO_ROOT  # noqa: E402
from watcher.schema_validate import validate  # noqa: E402

logger = logging.getLogger(__name__)

__all__ = [
    "CARDS_DIR",
    "CARD_INDEX_PATH",
    "CARD_INDEX_VERSION",
    "INDEX_FIELDS",
    "iter_card_paths",
    "build_card_index",
    "load_card_index",
    "save_card_index",
    "write_card_index",
    "main",
]

CARDS_DIR = REPO_ROOT / "content" / "cards"
CARD_INDEX_PATH = CARDS_DIR / "index.json"
CARD_INDEX_VERSION = 1

# The subset of card.schema.json's fields schemas/card_index.schema.json's
# own per-entry shape carries -- everything an index/listing view needs
# (id/date/headline/topics/lexicon_terms/status), deliberately excluding
# the full prose/citations that stay in the per-card file.
INDEX_FIELDS = ("id", "date", "headline", "topics", "lexicon_terms", "status")


# --------------------------------------------------------------------------
# glob content/cards/*.json -- the sole source of truth for "which cards
# currently exist" (never a diff against a previous index, never a
# separately-tracked "dropped" list).
# --------------------------------------------------------------------------


def iter_card_paths(cards_dir: Path | str = CARDS_DIR) -> list[Path]:
    """Every ``content/cards/<id>.json`` file currently on disk under
    ``cards_dir``, sorted by filename for a deterministic read order
    (final index ordering is re-sorted by date/id in
    :func:`build_card_index` regardless).

    Excludes ``index.json`` itself -- that file is this module's own
    *output*, never one of its own inputs.

    Returns an empty list if ``cards_dir`` doesn't exist yet at all (no
    cards ever published) or exists but is empty (every card since
    dropped, or simply none published yet) -- neither is an error.
    """
    cards_dir = Path(cards_dir)
    if not cards_dir.is_dir():
        return []
    return sorted(p for p in cards_dir.glob("*.json") if p.name != "index.json")


def _index_entry(card: dict[str, Any]) -> dict[str, Any]:
    return {field: card[field] for field in INDEX_FIELDS}


def build_card_index(cards_dir: Path | str = CARDS_DIR) -> dict[str, Any]:
    """Regenerate the ``card_index.schema.json``-shaped payload from
    scratch: every currently-existing ``content/cards/<id>.json`` under
    ``cards_dir``, each schema-validated against ``card.schema.json`` (a
    malformed on-disk card is a real, loud bug -- this deliberately raises
    rather than silently skipping it, matching this pipeline's "validate
    before it's ever trusted" convention; a card that was ever
    schema-valid enough to be committed in the first place should never
    fail this) and reduced to :data:`INDEX_FIELDS`.

    Cards are sorted most-recent-first (``date`` descending, then ``id``
    descending as a deterministic tie-break for same-day cards) --
    matches ``card_index.schema.json``'s own "most-recent-first by
    convention" description.

    Does not itself validate or write the *returned* payload -- see
    :func:`save_card_index`/:func:`write_card_index` for that.
    """
    cards = []
    for path in iter_card_paths(cards_dir):
        with path.open("r", encoding="utf-8") as f:
            card = json.load(f)
        validate(card, "card")
        cards.append(_index_entry(card))

    cards.sort(key=lambda c: (c["date"], c["id"]), reverse=True)
    return {"version": CARD_INDEX_VERSION, "cards": cards}


# --------------------------------------------------------------------------
# load / save -- schema-valid round trip (same pattern as
# watcher/ledger.py's save_ledger/load_ledger).
# --------------------------------------------------------------------------


def load_card_index(path: Path | str = CARD_INDEX_PATH) -> dict[str, Any]:
    """Load and schema-validate ``content/cards/index.json`` at ``path``.

    A missing file returns the empty-index shape (``{"version": 1,
    "cards": []}``) -- matches ``watcher/queue_writer.py``'s/``watcher/
    velocity.py``'s own "not generated yet is not an error" convention for
    a same-shaped committed artifact.
    """
    path = Path(path)
    if not path.is_file():
        return {"version": CARD_INDEX_VERSION, "cards": []}
    with path.open("r", encoding="utf-8") as f:
        index = json.load(f)
    validate(index, "card_index")
    return index


def save_card_index(index: dict[str, Any], path: Path | str = CARD_INDEX_PATH) -> None:
    """Schema-validate then write ``index`` to ``path`` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- same committed-artifact formatting every other writer in
    this pipeline uses (``watcher/ledger.py``'s ``save_ledger`` etc.).
    Validating *before* writing means a malformed index is never
    persisted. Creates ``path``'s parent directory (``content/cards/``)
    if it doesn't exist yet -- true the very first time this runs against
    a fresh checkout with no cards published at all.
    """
    validate(index, "card_index")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)
        f.write("\n")


def write_card_index(
    cards_dir: Path | str = CARDS_DIR, path: Path | str = CARD_INDEX_PATH
) -> dict[str, Any]:
    """Compose :func:`build_card_index` + :func:`save_card_index`: the
    single call ``scripts/reconcile_run.py`` (and this module's own
    :func:`main`) makes to regenerate, validate, and persist
    ``content/cards/index.json`` for one run. Returns the written
    payload.
    """
    index = build_card_index(cards_dir)
    save_card_index(index, path)
    return index


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    index = write_card_index()
    print(f"content/cards/index.json written: {len(index['cards'])} cards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
