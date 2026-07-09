"""Tests for scripts/update_card_index.py -- the content/cards/index.json
regenerator.

Covers, in order: regeneration correctness against a small set of
hand-built card fixtures, the "dropped card" case (a previously-indexed
card file deleted from disk before regeneration -- simply absent from the
next index, no special-case logic needed), the empty-directory and
not-yet-created-directory cases (both produce a valid, schema-conformant
empty index, never an error), the deterministic most-recent-first sort,
index.json itself never being treated as one of its own inputs, the
load/save/write schema-valid round trip, and that a malformed on-disk card
is a loud failure rather than a silent skip.

Every test uses a `tmp_path`-based `cards_dir`/`index_path` -- never the
real `content/cards/` directory -- so this suite has no side effects on
the repo's own (currently nonexistent) card set.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError

from scripts.update_card_index import (
    CARD_INDEX_VERSION,
    INDEX_FIELDS,
    build_card_index,
    iter_card_paths,
    load_card_index,
    save_card_index,
    write_card_index,
)
from watcher.schema_validate import validate


def _card(
    card_id: str,
    *,
    date: str = "2026-07-09",
    headline: str = "A Headline",
    topics: list[str] | None = None,
    lexicon_terms: list[str] | None = None,
    status: str = "confirmed",
) -> dict[str, Any]:
    """A minimal, fully card.schema.json-valid card dict -- only the
    fields this module's own tests care about are parameterized; every
    other required field gets a simple fixed placeholder value.
    """
    return {
        "id": card_id,
        "date": date,
        "headline": headline,
        "what_happened": "Something happened, described in the site's own words.",
        "why_it_matters": "It matters for newcomers trying to follow the space.",
        "one_liner": "A one-sentence summary.",
        "topics": topics if topics is not None else ["models"],
        "status": status,
        "citations": [
            {
                "url": "https://example-lab.test/blog/post",
                "outlet": "Example Lab",
                "quote": "A short supporting quote.",
            }
        ],
        "lexicon_terms": lexicon_terms if lexicon_terms is not None else [],
        "generated_at": "2026-07-09T07:15:00Z",
        "model": "claude-sonnet-4-5",
        "correction_note": None,
    }


def _write_card(cards_dir: Path, card: dict[str, Any]) -> Path:
    cards_dir.mkdir(parents=True, exist_ok=True)
    path = cards_dir / f"{card['id']}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(card, f)
    return path


# --------------------------------------------------------------------------
# empty / nonexistent directory
# --------------------------------------------------------------------------


def test_build_card_index_nonexistent_directory_is_empty_and_valid(tmp_path):
    """content/cards/ not created at all yet (true of this repo today,
    before the analyst has ever run) -> a valid, empty index, not an
    error."""
    cards_dir = tmp_path / "content" / "cards"
    assert not cards_dir.exists()

    index = build_card_index(cards_dir)

    assert index == {"version": CARD_INDEX_VERSION, "cards": []}
    validate(index, "card_index")  # must not raise


def test_build_card_index_empty_directory_is_empty_and_valid(tmp_path):
    """content/cards/ exists but has nothing in it (every card since
    dropped, or simply none published yet) -> also a valid, empty
    index."""
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()

    index = build_card_index(cards_dir)

    assert index == {"version": CARD_INDEX_VERSION, "cards": []}
    validate(index, "card_index")


def test_iter_card_paths_nonexistent_directory_returns_empty_list(tmp_path):
    assert iter_card_paths(tmp_path / "does" / "not" / "exist") == []


# --------------------------------------------------------------------------
# regeneration correctness
# --------------------------------------------------------------------------


def test_build_card_index_extracts_exactly_the_index_fields(tmp_path):
    cards_dir = tmp_path / "cards"
    card = _card(
        "2026-07-09-example-release",
        topics=["models", "products"],
        lexicon_terms=["context window"],
        status="confirmed",
    )
    _write_card(cards_dir, card)

    index = build_card_index(cards_dir)

    assert len(index["cards"]) == 1
    entry = index["cards"][0]
    assert set(entry.keys()) == set(INDEX_FIELDS)
    assert entry["id"] == "2026-07-09-example-release"
    assert entry["date"] == "2026-07-09"
    assert entry["headline"] == card["headline"]
    assert entry["topics"] == ["models", "products"]
    assert entry["lexicon_terms"] == ["context window"]
    assert entry["status"] == "confirmed"
    # Full prose/citations must NOT leak into the index.
    assert "what_happened" not in entry
    assert "citations" not in entry
    validate(index, "card_index")


def test_build_card_index_most_recent_first_by_date(tmp_path):
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, _card("2026-07-05-oldest", date="2026-07-05"))
    _write_card(cards_dir, _card("2026-07-09-newest", date="2026-07-09"))
    _write_card(cards_dir, _card("2026-07-07-middle", date="2026-07-07"))

    index = build_card_index(cards_dir)

    assert [c["id"] for c in index["cards"]] == [
        "2026-07-09-newest",
        "2026-07-07-middle",
        "2026-07-05-oldest",
    ]


def test_build_card_index_same_day_tie_break_is_id_descending(tmp_path):
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, _card("2026-07-09-aaa-card", date="2026-07-09"))
    _write_card(cards_dir, _card("2026-07-09-zzz-card", date="2026-07-09"))

    index = build_card_index(cards_dir)

    assert [c["id"] for c in index["cards"]] == [
        "2026-07-09-zzz-card",
        "2026-07-09-aaa-card",
    ]


def test_index_json_itself_is_never_treated_as_a_card_input(tmp_path):
    """A stale/previous index.json sitting in cards_dir must never be
    re-read as though it were itself a card."""
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, _card("2026-07-09-real-card"))
    # Simulate a previously-written index.json already on disk.
    (cards_dir / "index.json").write_text(
        json.dumps({"version": 1, "cards": []}), encoding="utf-8"
    )

    index = build_card_index(cards_dir)

    assert [c["id"] for c in index["cards"]] == ["2026-07-09-real-card"]


# --------------------------------------------------------------------------
# the "dropped card" case -- a file deleted between regenerations
# --------------------------------------------------------------------------


def test_deleted_card_file_is_simply_absent_from_the_next_index(tmp_path):
    cards_dir = tmp_path / "cards"
    kept_path = _write_card(cards_dir, _card("2026-07-08-kept-card", date="2026-07-08"))
    dropped_path = _write_card(
        cards_dir, _card("2026-07-09-dropped-card", date="2026-07-09")
    )

    # First regeneration: both cards present.
    first_index = build_card_index(cards_dir)
    assert {c["id"] for c in first_index["cards"]} == {
        "2026-07-08-kept-card",
        "2026-07-09-dropped-card",
    }

    # The "dropped" card's file is removed from disk (e.g. a correction
    # retracted it, or it was deleted by hand) -- no ledger/index API call,
    # just a missing file, exactly like a card that was never published.
    dropped_path.unlink()
    assert kept_path.exists()

    second_index = build_card_index(cards_dir)

    assert [c["id"] for c in second_index["cards"]] == ["2026-07-08-kept-card"]
    validate(second_index, "card_index")


def test_regenerating_over_a_previously_written_index_drops_deleted_cards(tmp_path):
    """End-to-end via write_card_index: a card removed from disk between
    two write_card_index() calls disappears from the persisted
    content/cards/index.json, not just the in-memory build."""
    cards_dir = tmp_path / "cards"
    index_path = tmp_path / "index.json"
    _write_card(cards_dir, _card("2026-07-08-kept-card", date="2026-07-08"))
    dropped_path = _write_card(
        cards_dir, _card("2026-07-09-dropped-card", date="2026-07-09")
    )

    write_card_index(cards_dir, index_path)
    on_disk_first = json.loads(index_path.read_text(encoding="utf-8"))
    assert {c["id"] for c in on_disk_first["cards"]} == {
        "2026-07-08-kept-card",
        "2026-07-09-dropped-card",
    }

    dropped_path.unlink()
    write_card_index(cards_dir, index_path)
    on_disk_second = json.loads(index_path.read_text(encoding="utf-8"))
    assert [c["id"] for c in on_disk_second["cards"]] == ["2026-07-08-kept-card"]


# --------------------------------------------------------------------------
# load / save / write round trip
# --------------------------------------------------------------------------


def test_load_card_index_missing_file_returns_empty_index(tmp_path):
    index = load_card_index(tmp_path / "does-not-exist.json")
    assert index == {"version": CARD_INDEX_VERSION, "cards": []}


def test_save_then_load_round_trips(tmp_path):
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, _card("2026-07-09-example"))
    index_path = tmp_path / "nested" / "index.json"

    built = build_card_index(cards_dir)
    save_card_index(built, index_path)

    assert index_path.is_file()
    loaded = load_card_index(index_path)
    assert loaded == built
    validate(loaded, "card_index")


def test_save_card_index_rejects_invalid_payload(tmp_path):
    with pytest.raises(ValidationError):
        save_card_index({"version": 1, "cards": [{"id": "missing-fields"}]}, tmp_path / "index.json")


def test_write_card_index_creates_parent_directory(tmp_path):
    cards_dir = tmp_path / "cards"
    _write_card(cards_dir, _card("2026-07-09-example"))
    index_path = tmp_path / "brand" / "new" / "dir" / "index.json"
    assert not index_path.parent.exists()

    write_card_index(cards_dir, index_path)

    assert index_path.is_file()


# --------------------------------------------------------------------------
# a malformed on-disk card is a loud failure, not a silent skip
# --------------------------------------------------------------------------


def test_build_card_index_raises_on_malformed_card(tmp_path):
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    bad_card = _card("2026-07-09-bad-card")
    del bad_card["headline"]  # required field missing
    _write_card(cards_dir, bad_card)

    with pytest.raises(ValidationError):
        build_card_index(cards_dir)
