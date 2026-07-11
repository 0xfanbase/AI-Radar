"""Tests for auditor/missed_story.py -- the weekly missed-story check (see
CLAUDE.md's "audit.yml -- weekly" bullet / the approved plan's Phase 5
section: 'missed-story check (HN top-20 AI stories of the week vs.
published + ledger-dropped clusters -- genuine misses vs.
correctly-declined-per-corroboration-rule are distinguished, both logged
as *findings, not failures*, per spec)').

`content/cards/` is currently empty and `data/ledger.json` has no
`"dropped"`/`"published"` entries yet (no analyst/verifier run has
happened for real), so -- matching `tests/test_auditor_duplicates.py`'s
own established pattern -- most tests here exercise the pure
`story_matches_card`/`story_matches_ledger_entry`/`classify_story`/
`audit_missed_stories` functions directly against small fixture
cards/ledger entries/HN items, rather than the real (mostly-empty,
all-`"queued"`) `content/cards/`/`data/ledger.json`.

`tests/conftest.py`'s autouse fixture blocks any real network call by
default; every test that exercises `fetch_weekly_top_hn_stories` or
`audit_missed_stories`'s own live-HN-fetch default path here uses either
`requests_mock` (this project's established deterministic HTTP test tool
-- see `tests/test_hn_fetch.py`) or a direct `monkeypatch` of
`watcher.sources.hn.fetch_hn_items` itself, so no test in this file ever
needs a real network call to pass.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import watcher.clustering as clustering_mod
import watcher.config as config_mod
import watcher.models as models_mod
import watcher.sources.hn as hn_mod
from auditor import missed_story as mod
from auditor.missed_story import (
    MISSED_STORY_HN_LOOKBACK_HOURS,
    MISSED_STORY_JACCARD_THRESHOLD,
    MISSED_STORY_TOP_N,
    StoryClassification,
    audit_missed_stories,
    classify_story,
    fetch_weekly_top_hn_stories,
    load_ledger,
    story_matches_card,
    story_matches_ledger_entry,
)
from watcher import http
from watcher.models import Item

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "hn_algolia_response.json"

# Same fixed reference "now" `tests/test_hn_fetch.py` uses for its own
# fixture -- a couple of hours after the real fixture snapshot was
# captured, so ages/velocities are deterministic regardless of when the
# suite actually runs.
FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _load_fixture_text() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _mock_robots_allow(requests_mock) -> None:
    requests_mock.get("https://hn.algolia.com/robots.txt", status_code=404)


def _hn_item(title: str, url: str, *, points: int = 100) -> Item:
    return Item(
        source_type="hn",
        source_name="hn",
        title=title,
        url=url,
        published_at="2026-07-05T00:00:00Z",
        points=points,
        num_comments=10,
        extra={},
    )


def _card(card_id: str, headline: str, *, citations: list[dict] | None = None) -> dict:
    card = {"id": card_id, "date": "2026-07-05", "headline": headline}
    if citations is not None:
        card["citations"] = citations
    return card


def _ledger_entry(
    status: str,
    member_urls: list[str],
    *,
    card_id: str | None = None,
    dropped_reason: str | None = None,
) -> dict:
    entry: dict = {
        "card_id": card_id,
        "status": status,
        "first_seen": "2026-07-01",
        "last_seen": "2026-07-01",
        "member_urls": member_urls,
    }
    if dropped_reason is not None:
        entry["verifier_outcome"] = {
            "last_attempted_at": "2026-07-01T00:00:00Z",
            "dropped_reason": dropped_reason,
        }
    return entry


# ---------------------------------------------------------------------------
# Reuse, not reimplementation -- the core instruction this turn's task gave.
# ---------------------------------------------------------------------------


def test_reuses_watcher_sources_hn_module_by_identity():
    """`missed_story.hn` must be the exact `watcher.sources.hn` module
    object -- proof HN fetching is imported and called through, never
    reimplemented locally."""
    assert mod.hn is hn_mod


def test_reuses_watcher_clustering_jaccard_by_identity():
    assert mod._jaccard is clustering_mod._jaccard


def test_reuses_watcher_models_tokenize_title_by_identity():
    assert mod.tokenize_title is models_mod.tokenize_title


def test_reuses_watcher_models_normalize_url_by_identity():
    assert mod.normalize_url is models_mod.normalize_url


def test_threshold_constant_is_the_real_config_value_not_a_new_one():
    assert MISSED_STORY_JACCARD_THRESHOLD == config_mod.JACCARD_SIMILARITY_THRESHOLD == 0.35


def test_lookback_and_top_n_constants_match_claude_md_spec():
    # CLAUDE.md's audit.yml bullet, verbatim: "top-20 HN AI stories of the
    # week" -- 7 days, top 20.
    assert MISSED_STORY_HN_LOOKBACK_HOURS == 24 * 7 == 168
    assert MISSED_STORY_TOP_N == 20


# ---------------------------------------------------------------------------
# fetch_weekly_top_hn_stories -- widened window/count, no reimplementation
# ---------------------------------------------------------------------------


def test_fetch_weekly_top_hn_stories_widens_window_and_slices_top_n(monkeypatch):
    """Proves the widened-window/top-N behavior via a monkeypatched
    `hn.fetch_hn_items` stub, capturing exactly what it was called with --
    the call must use a 168h lookback (not the daily watcher's own 48h
    default) and the result must be sliced to `top_n` from whatever
    `fetch_hn_items` itself already returned (pre-sorted by points
    descending, per its own contract)."""
    calls = []

    def fake_fetch_hn_items(session, **kwargs):
        calls.append(kwargs)
        # 25 fake items, already sorted by points descending -- exactly
        # the contract fetch_hn_items itself guarantees.
        return [
            _hn_item(f"Story {i}", f"https://example.test/{i}", points=100 - i)
            for i in range(25)
        ]

    monkeypatch.setattr(mod.hn, "fetch_hn_items", fake_fetch_hn_items)

    items = fetch_weekly_top_hn_stories(object(), now=FIXED_NOW)

    assert calls[0]["lookback_hours"] == 168
    assert calls[0]["now"] == FIXED_NOW
    assert len(items) == 20
    assert [item.title for item in items] == [f"Story {i}" for i in range(20)]


def test_fetch_weekly_top_hn_stories_respects_custom_top_n(monkeypatch):
    def fake_fetch_hn_items(session, **kwargs):
        return [_hn_item(f"Story {i}", f"https://example.test/{i}", points=100 - i) for i in range(5)]

    monkeypatch.setattr(mod.hn, "fetch_hn_items", fake_fetch_hn_items)

    items = fetch_weekly_top_hn_stories(object(), now=FIXED_NOW, top_n=2)

    assert [item.title for item in items] == ["Story 0", "Story 1"]


def test_fetch_weekly_top_hn_stories_reuses_real_fetch_hn_items_against_real_fixture(
    requests_mock, tmp_path
):
    """Genuine end-to-end proof (not just a monkeypatched stub): the real
    `hn.fetch_hn_items` pipeline, run against the same real captured
    Algolia fixture `tests/test_hn_fetch.py` uses for its own 48h/
    4-window daily-watcher test, produces the same three AI-relevant
    stories here too -- with the lookback widened to 168h (7 days), which
    means 14 non-overlapping 12h windows get queried instead of 4.
    """
    _mock_robots_allow(requests_mock)
    requests_mock.get(hn_mod.SEARCH_BY_DATE_URL, text=_load_fixture_text())

    session = http.build_session()
    items = fetch_weekly_top_hn_stories(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert [item.title for item in items] == [
        "I Think I Have LLM Burnout",
        "We made Grok 4.5, GPT-5.5, and Claude build the same apps",
        "Suspecting AI cheating, Ivy League prof ordered in-person final; scores fell 50%",
    ]
    search_calls = [
        req for req in requests_mock.request_history
        if req.url.startswith(hn_mod.SEARCH_BY_DATE_URL)
    ]
    # 168h / 12h window = 14 windows -- widened from the daily watcher's
    # own 48h / 4-window default (asserted directly in test_hn_fetch.py).
    assert len(search_calls) == 14


# ---------------------------------------------------------------------------
# load_ledger
# ---------------------------------------------------------------------------


def test_load_ledger_returns_empty_shape_when_file_missing(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    assert load_ledger(missing) == {"version": 1, "entries": {}}


def test_load_ledger_loads_real_file(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    payload = {"version": 1, "entries": {"abc123": _ledger_entry("dropped", ["https://x.test/1"])}}
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_ledger(ledger_path) == payload


# ---------------------------------------------------------------------------
# story_matches_card
# ---------------------------------------------------------------------------


def test_story_matches_card_via_exact_normalized_citation_url():
    # Trailing slash + "www." prefix differ from the card's citation URL --
    # only a genuine normalize_url()-based comparison, not a raw string
    # equality check, would catch this as the same URL.
    item = _hn_item(
        "Totally unrelated headline text here",
        "https://www.example.test/story-a/",
    )
    card = _card(
        "card-1",
        "A completely different headline about something else",
        citations=[{"url": "https://example.test/story-a", "outlet": "Test", "quote": "q"}],
    )
    assert story_matches_card(item, card) is True


def test_story_matches_card_via_headline_jaccard():
    item = _hn_item(
        "OpenAI releases GPT-5.5 with major upgrades",
        "https://unrelated.test/no-citation-overlap",
    )
    card = _card("card-2", "OpenAI ships GPT-5.5 with major upgrades")
    assert story_matches_card(item, card) is True


def test_story_matches_card_false_when_neither_tier_matches():
    item = _hn_item(
        "DeepSeek launches new open-weights model",
        "https://unrelated.test/x",
    )
    card = _card(
        "card-3",
        "Cloud storage costs keep rising this quarter",
        citations=[{"url": "https://other.test/y", "outlet": "Test", "quote": "q"}],
    )
    assert story_matches_card(item, card) is False


def test_story_matches_card_handles_missing_citations_key_gracefully():
    item = _hn_item("Some unrelated story", "https://unrelated.test/z")
    card = {"id": "card-4", "headline": "A totally different topic"}
    assert story_matches_card(item, card) is False


def test_story_matches_card_threshold_boundary_is_inclusive():
    # Same exactly-representable 0.5 Jaccard pair auditor/duplicates.py's
    # own boundary test uses (3 shared / 6 union tokens).
    item = _hn_item("Anthropic ships Claude Fable 5 today", "https://unrelated.test/a")
    card = _card("card-5", "Anthropic launches Claude Fable 5 update")

    similarity = clustering_mod._jaccard(
        models_mod.tokenize_title(item.title), models_mod.tokenize_title(card["headline"])
    )
    assert similarity == 0.5

    assert story_matches_card(item, card, threshold=0.5) is True
    assert story_matches_card(item, card, threshold=0.51) is False


# ---------------------------------------------------------------------------
# story_matches_ledger_entry
# ---------------------------------------------------------------------------


def test_story_matches_ledger_entry_via_exact_normalized_url():
    item = _hn_item("Some story", "https://www.example.test/lab-post/")
    entry = _ledger_entry("dropped", ["https://example.test/lab-post"])
    assert story_matches_ledger_entry(item, entry) is True


def test_story_matches_ledger_entry_false_when_no_overlap():
    item = _hn_item("Some story", "https://unrelated.test/x")
    entry = _ledger_entry("dropped", ["https://example.test/lab-post"])
    assert story_matches_ledger_entry(item, entry) is False


def test_story_matches_ledger_entry_handles_missing_member_urls_gracefully():
    item = _hn_item("Some story", "https://unrelated.test/x")
    entry = {"card_id": None, "status": "dropped", "first_seen": "d", "last_seen": "d"}
    assert story_matches_ledger_entry(item, entry) is False


# ---------------------------------------------------------------------------
# classify_story
# ---------------------------------------------------------------------------


def test_classify_story_card_match_is_covered():
    item = _hn_item("OpenAI releases GPT-5.5 with major upgrades", "https://unrelated.test/a", points=77)
    card = _card("card-1", "OpenAI ships GPT-5.5 with major upgrades")

    result = classify_story(item, cards=[card], ledger_entries={})

    assert result == StoryClassification(
        title=item.title,
        url=item.url,
        points=77,
        classification="covered",
        matched_card_id="card-1",
    )


def test_classify_story_dropped_only_ledger_match_is_seen_but_dropped():
    item = _hn_item("Some rumor-only story", "https://example.test/rumor", points=42)
    entry = _ledger_entry(
        "dropped",
        ["https://example.test/rumor"],
        dropped_reason="anonymous-source-only claim, no primary confirmation",
    )

    result = classify_story(item, cards=[], ledger_entries={"hash-1": entry})

    assert result.classification == "seen_but_dropped"
    assert result.matched_cluster_hash == "hash-1"
    assert result.dropped_reason == "anonymous-source-only claim, no primary confirmation"
    assert result.matched_card_id is None


def test_classify_story_queued_ledger_match_is_covered_not_seen_but_dropped():
    item = _hn_item("Still being processed story", "https://example.test/queued", points=60)
    entry = _ledger_entry("queued", ["https://example.test/queued"])

    result = classify_story(item, cards=[], ledger_entries={"hash-2": entry})

    assert result.classification == "covered"
    assert result.matched_cluster_hash == "hash-2"


def test_classify_story_published_ledger_match_is_covered_with_card_id():
    item = _hn_item("Already published story", "https://example.test/published", points=60)
    entry = _ledger_entry("published", ["https://example.test/published"], card_id="2026-07-05-some-card")

    result = classify_story(item, cards=[], ledger_entries={"hash-3": entry})

    assert result.classification == "covered"
    assert result.matched_card_id == "2026-07-05-some-card"


def test_classify_story_no_match_anywhere_is_missed():
    item = _hn_item("A genuine gap in coverage", "https://example.test/gap", points=90)

    result = classify_story(item, cards=[], ledger_entries={})

    assert result.classification == "missed"
    assert result.matched_card_id is None
    assert result.matched_cluster_hash is None
    assert result.dropped_reason is None


def test_classify_story_card_match_wins_over_a_separate_dropped_ledger_match():
    """A story can, in principle, match a published card via one URL/title
    AND separately match an unrelated dropped ledger entry (e.g. an older
    cluster_hash for a since-superseded claim). A card match is the
    stronger signal and must win -- the story is `covered`, not
    `seen_but_dropped`."""
    item = _hn_item(
        "OpenAI releases GPT-5.5 with major upgrades",
        "https://example.test/story-x",
        points=88,
    )
    card = _card("card-1", "OpenAI ships GPT-5.5 with major upgrades")
    # A dropped entry that happens to also match this same story's URL --
    # must not flip the outcome to seen_but_dropped.
    dropped_entry = _ledger_entry(
        "dropped", ["https://example.test/story-x"], dropped_reason="stale rumor"
    )

    result = classify_story(item, cards=[card], ledger_entries={"hash-4": dropped_entry})

    assert result.classification == "covered"
    assert result.matched_card_id == "card-1"
    assert result.dropped_reason is None


def test_classify_story_covered_wins_regardless_of_dropped_entry_iteration_order():
    """No card in play here -- only ledger entries. Whether the matching
    dropped entry is stored before or after the matching non-dropped
    (queued) entry in the ledger dict, the non-dropped match must still
    win: classify_story must not return early on the first dropped match
    it happens to encounter."""
    item = _hn_item("A story with two ledger hits", "https://example.test/two-hits", points=55)
    dropped_entry = _ledger_entry("dropped", ["https://example.test/two-hits"], dropped_reason="x")
    queued_entry = _ledger_entry("queued", ["https://example.test/two-hits"])

    dropped_first = classify_story(
        item,
        cards=[],
        ledger_entries={"hash-dropped": dropped_entry, "hash-queued": queued_entry},
    )
    queued_first = classify_story(
        item,
        cards=[],
        ledger_entries={"hash-queued": queued_entry, "hash-dropped": dropped_entry},
    )

    assert dropped_first.classification == "covered"
    assert queued_first.classification == "covered"


def test_classify_story_finds_matching_entry_among_several_non_matching_ones():
    item = _hn_item("The real match", "https://example.test/real-match", points=61)
    entries = {
        "hash-a": _ledger_entry("dropped", ["https://example.test/other-a"]),
        "hash-b": _ledger_entry("queued", ["https://example.test/other-b"]),
        "hash-c": _ledger_entry(
            "dropped", ["https://example.test/real-match"], dropped_reason="benchmark leak, no artifact"
        ),
    }

    result = classify_story(item, cards=[], ledger_entries=entries)

    assert result.classification == "seen_but_dropped"
    assert result.matched_cluster_hash == "hash-c"
    assert result.dropped_reason == "benchmark leak, no artifact"


# ---------------------------------------------------------------------------
# audit_missed_stories
# ---------------------------------------------------------------------------


def test_audit_missed_stories_with_explicit_data_classifies_and_counts_correctly():
    covered_item = _hn_item(
        "OpenAI releases GPT-5.5 with major upgrades", "https://example.test/covered", points=100
    )
    dropped_item = _hn_item("A rumor with no legs", "https://example.test/dropped", points=80)
    missed_item = _hn_item("A genuine uncovered story", "https://example.test/missed", points=60)

    cards = [_card("card-1", "OpenAI ships GPT-5.5 with major upgrades")]
    ledger = {
        "version": 1,
        "entries": {
            "hash-1": _ledger_entry(
                "dropped", ["https://example.test/dropped"], dropped_reason="anonymous source only"
            )
        },
    }

    report = audit_missed_stories(
        hn_items=[covered_item, dropped_item, missed_item], cards=cards, ledger=ledger
    )

    assert report["total_checked"] == 3
    assert report["counts"] == {"covered": 1, "seen_but_dropped": 1, "missed": 1}
    assert [r["url"] for r in report["results"]] == [
        "https://example.test/covered",
        "https://example.test/dropped",
        "https://example.test/missed",
    ]
    assert len(report["missed_stories"]) == 1
    assert report["missed_stories"][0]["url"] == "https://example.test/missed"
    assert len(report["seen_but_dropped_stories"]) == 1
    assert report["seen_but_dropped_stories"][0]["url"] == "https://example.test/dropped"
    assert report["window_hours"] == MISSED_STORY_HN_LOOKBACK_HOURS
    assert report["top_n"] == MISSED_STORY_TOP_N
    assert "checked_at" in report


def test_audit_missed_stories_empty_hn_items_is_a_clean_zero_report():
    report = audit_missed_stories(hn_items=[], cards=[], ledger={"version": 1, "entries": {}})

    assert report["total_checked"] == 0
    assert report["counts"] == {"covered": 0, "seen_but_dropped": 0, "missed": 0}
    assert report["missed_stories"] == []
    assert report["seen_but_dropped_stories"] == []
    assert report["results"] == []


def test_audit_missed_stories_defaults_to_loading_cards_via_auditor_linkrot_load_cards(
    monkeypatch,
):
    """`cards=None` must load through `auditor.linkrot.load_cards` --
    reused directly, matching `auditor.duplicates.audit_duplicates`'s own
    established convention -- proven by monkeypatching the exact bound
    name `missed_story.load_cards`."""
    card = _card("card-1", "OpenAI ships GPT-5.5 with major upgrades")
    monkeypatch.setattr(mod, "load_cards", lambda: [card])

    item = _hn_item("OpenAI releases GPT-5.5 with major upgrades", "https://example.test/x")
    report = audit_missed_stories(hn_items=[item], ledger={"version": 1, "entries": {}})

    assert report["counts"]["covered"] == 1


def test_audit_missed_stories_defaults_to_loading_ledger_via_load_ledger(monkeypatch):
    entry = _ledger_entry("dropped", ["https://example.test/y"], dropped_reason="no artifact")
    monkeypatch.setattr(
        mod, "load_ledger", lambda ledger_path=mod.LEDGER_PATH: {"version": 1, "entries": {"h": entry}}
    )

    item = _hn_item("Some rumor", "https://example.test/y")
    report = audit_missed_stories(hn_items=[item], cards=[])

    assert report["counts"]["seen_but_dropped"] == 1


def test_audit_missed_stories_defaults_hn_items_to_live_fetch_against_real_disk_state(
    requests_mock, tmp_path
):
    """Integration-flavored smoke test: with `hn_items` omitted, the real
    default wiring (`fetch_weekly_top_hn_stories` -> `hn.fetch_hn_items`,
    via a freshly built `watcher.http.build_session()`) is exercised
    end-to-end against a mocked HN endpoint, while `cards`/`ledger` load
    from this repo's real (currently empty / all-`"queued"`)
    `content/cards/` and `data/ledger.json` -- proving those disk-default
    paths work without needing a live network call for either.
    """
    _mock_robots_allow(requests_mock)
    hit = {
        "objectID": "9999999",
        "title": "A completely fictitious AI research breakthrough for testing",
        "url": "https://example.test/fictitious-ai-breakthrough-for-testing",
        "points": 200,
        "num_comments": 10,
        "author": "tester",
        "created_at": "2026-07-09T03:00:00Z",
    }
    requests_mock.get(hn_mod.SEARCH_BY_DATE_URL, text=json.dumps({"hits": [hit]}))

    report = audit_missed_stories(now=FIXED_NOW, cache_dir=tmp_path)

    assert report["total_checked"] == 1
    # Real content/cards/ is empty and this fictitious URL cannot appear in
    # the real data/ledger.json -- so this must classify as "missed".
    assert report["counts"]["missed"] == 1
    assert report["missed_stories"][0]["url"] == hit["url"]


def test_audit_missed_stories_builds_its_own_session_when_none_passed(requests_mock, tmp_path):
    _mock_robots_allow(requests_mock)
    requests_mock.get(hn_mod.SEARCH_BY_DATE_URL, text=json.dumps({"hits": []}))

    report = audit_missed_stories(
        cards=[], ledger={"version": 1, "entries": {}}, now=FIXED_NOW, cache_dir=tmp_path
    )

    assert report["total_checked"] == 0
