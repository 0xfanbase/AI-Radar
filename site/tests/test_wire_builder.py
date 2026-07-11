"""Tests for site/builders/wire.py -- the Wire home page + monthly archive
(Phase 4, build-plan section 5).

content/cards/ is genuinely empty in this environment (no analyst run has
happened live yet), so this suite builds against a couple of SYNTHETIC
fixture cards matching card.schema.json's shape, per this turn's explicit
scope -- plus one dedicated test proving the real zero-cards case (an
actually empty `cards` list) renders a clear, honest empty state rather
than crashing or looking broken.

Loaded by explicit file path (matching `site/tests/test_build.py`'s and
`site/tests/test_linkify.py`'s own convention for everything under `site/`),
since `site/` is deliberately never turned into an importable package --
see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import jsonschema
import pytest
from markupsafe import escape

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WIRE_PATH = REPO_ROOT / "site" / "builders" / "wire.py"
CARD_SCHEMA_PATH = REPO_ROOT / "schemas" / "card.schema.json"


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module: wire.py's own linkify import chain
    # (site/lib/linkify.py) uses `@dataclass` + `from __future__ import
    # annotations`, which needs `sys.modules[cls.__module__]` populated
    # during class creation -- same reasoning as test_linkify.py.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wire = _load_module_by_path("frontier_wire_site_builders_wire", WIRE_PATH)


# ---------------------------------------------------------------------------
# Synthetic fixtures -- card.schema.json shape, small isolated lexicon.
# ---------------------------------------------------------------------------

SYNTHETIC_LEXICON = [
    {"term": "transformer", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []},
    {"term": "RLHF", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []},
    {"term": "open weights", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []},
]

TODAY = date(2026, 7, 9)  # matches this session's real "current date"

CARD_CONFIRMED = {
    "id": "2026-07-08-test-alpha",
    "date": "2026-07-08",
    "headline": "Test Lab ships Test Model Alpha",
    "what_happened": "Test Lab announced a new transformer-based model on its own blog.",
    "why_it_matters": "It gives developers a faster, cheaper option for everyday coding tasks.",
    "one_liner": "Test Lab's newest model is faster and cheaper to run.",
    "topics": ["models", "products"],
    "status": "confirmed",
    "citations": [
        {"url": "https://test-lab.example/blog/alpha", "outlet": "Test Lab", "quote": "Our fastest model yet."},
        {"url": "https://test-news.example/2026/07/08/alpha", "outlet": "Test News", "quote": "A notable speed jump."},
    ],
    # "RLHF" never appears in either prose field -> exercises the
    # lexicon-fallback chip with a resolvable slug. "unlisted term" is
    # absent from SYNTHETIC_LEXICON entirely -> exercises the fallback
    # chip's *unresolvable* (no-slug, plain-text) branch.
    "lexicon_terms": ["transformer", "RLHF", "unlisted term"],
    "generated_at": "2026-07-08T07:15:00Z",
    "model": "claude-sonnet-4-5",
    "correction_note": None,
}

CARD_REPORTED = {
    "id": "2026-07-05-test-beta",
    "date": "2026-07-05",
    "headline": "Reported: Test Lab teases Model Beta",
    "what_happened": "Multiple outlets reported Test Lab is preparing a new model, without an official post yet.",
    "why_it_matters": "If confirmed, it would be a notable competitive move in the field.",
    "one_liner": "Word is Test Lab has something new coming.",
    "topics": ["models", "research"],
    "status": "reported",
    "citations": [
        {"url": "https://outlet-one.example/beta", "outlet": "Outlet One", "quote": "Sources say a new model is imminent."},
    ],
    "lexicon_terms": [],
    "generated_at": "2026-07-05T09:00:00Z",
    "model": "claude-sonnet-4-5",
    "correction_note": None,
}

CARD_CORRECTED_OLD_MONTH = {
    "id": "2026-05-01-test-gamma",
    "date": "2026-05-01",
    "headline": "Test Lab open-weights release, corrected",
    "what_happened": "Test Lab released an open weights model, initially reported at the wrong parameter count.",
    "why_it_matters": "Open weights models let anyone inspect, fine-tune, or self-host the model.",
    "one_liner": "Test Lab's new model can be downloaded and run by anyone.",
    "topics": ["open-source", "models"],
    "status": "corrected",
    "citations": [
        {"url": "https://test-lab.example/blog/gamma", "outlet": "Test Lab", "quote": "Weights are available today."},
    ],
    "lexicon_terms": ["open weights"],
    "generated_at": "2026-05-01T12:00:00Z",
    "model": "claude-sonnet-4-5",
    "correction_note": "Parameter count corrected from 8B to 80B; see /corrections/.",
}

ALL_CARDS = [CARD_CONFIRMED, CARD_REPORTED, CARD_CORRECTED_OLD_MONTH]


def test_fixture_cards_validate_against_card_schema():
    # Sanity check: the synthetic fixtures this suite exercises really do
    # match card.schema.json's shape, not just wire.py's assumptions about it.
    schema = json.loads(CARD_SCHEMA_PATH.read_text())
    for card in ALL_CARDS:
        jsonschema.validate(card, schema)


# ---------------------------------------------------------------------------
# Pure-logic helpers: windowing, month grouping, labels.
# ---------------------------------------------------------------------------


def test_cards_in_window_filters_to_last_14_days_and_sorts_newest_first():
    windowed = wire.cards_in_window(ALL_CARDS, window_days=14, today=TODAY)
    # 2026-05-01 is well outside a 14-day window ending 2026-07-09.
    assert [c["id"] for c in windowed] == [CARD_CONFIRMED["id"], CARD_REPORTED["id"]]


def test_cards_in_window_boundary_is_inclusive():
    cutoff_card = {**CARD_REPORTED, "id": "boundary", "date": "2026-06-26"}
    windowed = wire.cards_in_window([cutoff_card], window_days=14, today=TODAY)
    assert [c["id"] for c in windowed] == ["boundary"]

    one_day_too_old = {**CARD_REPORTED, "id": "too-old", "date": "2026-06-25"}
    windowed = wire.cards_in_window([one_day_too_old], window_days=14, today=TODAY)
    assert windowed == []


def test_cards_in_window_handles_empty_input():
    assert wire.cards_in_window([], window_days=14, today=TODAY) == []


def test_available_months_sorted_newest_first():
    assert wire.available_months(ALL_CARDS) == ["2026-07", "2026-05"]


def test_available_months_handles_empty_input():
    assert wire.available_months([]) == []


def test_cards_for_month_filters_and_sorts():
    may_cards = wire.cards_for_month(ALL_CARDS, "2026-05")
    assert [c["id"] for c in may_cards] == [CARD_CORRECTED_OLD_MONTH["id"]]

    july_cards = wire.cards_for_month(ALL_CARDS, "2026-07")
    assert [c["id"] for c in july_cards] == [CARD_CONFIRMED["id"], CARD_REPORTED["id"]]

    assert wire.cards_for_month(ALL_CARDS, "2026-01") == []


def test_month_label_formats_year_month():
    assert wire.month_label("2026-07") == "July 2026"
    assert wire.month_label("2026-05") == "May 2026"


# ---------------------------------------------------------------------------
# prepare_card_view -- the per-card view model.
# ---------------------------------------------------------------------------


def test_prepare_card_view_status_label_and_chip_class():
    slug_map = wire.linkify.build_slug_map(SYNTHETIC_LEXICON)
    view = wire.prepare_card_view(CARD_CONFIRMED, slug_map)
    assert view["status_label"] == "CONFIRMED"
    assert view["status_chip_class"] == "chip chip--confirmed"

    view_reported = wire.prepare_card_view(CARD_REPORTED, slug_map)
    assert view_reported["status_label"] == "REPORTED"
    assert view_reported["status_chip_class"] == "chip chip--reported"

    view_corrected = wire.prepare_card_view(CARD_CORRECTED_OLD_MONTH, slug_map)
    assert view_corrected["status_label"] == "CORRECTED"
    assert view_corrected["status_chip_class"] == "chip chip--corrected"


def test_prepare_card_view_links_term_found_in_prose_and_not_fallback():
    slug_map = wire.linkify.build_slug_map(SYNTHETIC_LEXICON)
    view = wire.prepare_card_view(CARD_CONFIRMED, slug_map)
    assert "<a href=\"/lexicon/transformer/\">" in view["what_happened_html"]
    fallback_terms = [entry["term"] for entry in view["lexicon_fallback_terms"]]
    assert "transformer" not in fallback_terms


def test_prepare_card_view_fallback_terms_for_unmatched_and_unlisted():
    slug_map = wire.linkify.build_slug_map(SYNTHETIC_LEXICON)
    view = wire.prepare_card_view(CARD_CONFIRMED, slug_map)
    fallback_by_term = {entry["term"]: entry["slug"] for entry in view["lexicon_fallback_terms"]}
    # "RLHF" is a real lexicon term but never appears verbatim in either
    # prose field -> fallback chip, still resolvable to a real page.
    assert fallback_by_term["RLHF"] == "rlhf"
    # "unlisted term" isn't in SYNTHETIC_LEXICON at all -> fallback chip,
    # no resolvable slug.
    assert fallback_by_term["unlisted term"] is None


def test_prepare_card_view_no_lexicon_terms_yields_no_fallback():
    slug_map = wire.linkify.build_slug_map(SYNTHETIC_LEXICON)
    view = wire.prepare_card_view(CARD_REPORTED, slug_map)
    assert view["lexicon_fallback_terms"] == []


def test_prepare_card_view_correction_note_passthrough():
    slug_map = wire.linkify.build_slug_map(SYNTHETIC_LEXICON)
    view = wire.prepare_card_view(CARD_CORRECTED_OLD_MONTH, slug_map)
    assert view["correction_note"] == CARD_CORRECTED_OLD_MONTH["correction_note"]
    assert wire.prepare_card_view(CARD_CONFIRMED, slug_map)["correction_note"] is None


def test_prepare_card_view_generated_at_and_model_passthrough():
    # Hard Rule 5 (CLAUDE.md): every card must visibly carry its generated
    # timestamp and model. prepare_card_view must actually copy these two
    # schema-required fields into the view model, not just the date/status.
    slug_map = wire.linkify.build_slug_map(SYNTHETIC_LEXICON)
    view = wire.prepare_card_view(CARD_CONFIRMED, slug_map)
    assert view["generated_at"] == CARD_CONFIRMED["generated_at"]
    assert view["model"] == CARD_CONFIRMED["model"]

    view_reported = wire.prepare_card_view(CARD_REPORTED, slug_map)
    assert view_reported["generated_at"] == CARD_REPORTED["generated_at"]
    assert view_reported["model"] == CARD_REPORTED["model"]


# ---------------------------------------------------------------------------
# Rendered HTML -- wire_index.html / wire_month.html / card.html.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def env():
    return wire.build_env()


def test_render_wire_index_status_chip_text_present_regardless_of_styling(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    # Literal, visible status text -- must be present as plain text content,
    # not merely implied by a CSS class name.
    assert "CONFIRMED" in html
    assert "REPORTED" in html
    # 2026-05-01 (corrected) is outside the 14-day window, so its own
    # CORRECTED chip is not expected on the home page; the fallback CSS
    # class name for the correction-note styling *is* still allowed to
    # appear if within window, but here it should simply not appear at all
    # since that card is excluded.
    assert CARD_CORRECTED_OLD_MONTH["headline"] not in html


def test_render_wire_index_one_liner_has_distinct_styling_class(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    # HTML-escape the expected text (e.g. the apostrophe in "Lab's") the
    # same way Jinja2's autoescape does, rather than assuming plain text.
    assert f'<p class="one-liner">{escape(CARD_CONFIRMED["one_liner"])}</p>' in html
    assert f'<p class="one-liner">{escape(CARD_REPORTED["one_liner"])}</p>' in html


def test_render_wire_index_sources_are_a_real_link_list_with_outlet_names(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    for citation in CARD_CONFIRMED["citations"]:
        assert f'<a href="{citation["url"]}">{citation["outlet"]}</a>' in html


def test_render_wire_index_topic_chips_are_text_labeled(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    for topic in CARD_CONFIRMED["topics"]:
        assert f'<span class="chip">{topic}</span>' in html


def test_render_wire_index_lexicon_fallback_chip_rendered(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    assert "Terms:" in html
    assert '<a href="/lexicon/rlhf/">RLHF</a>' in html
    # Unresolvable fallback term still renders as a (plain, unlinked) chip.
    assert "unlisted term" in html


def test_render_wire_index_generated_at_and_model_disclosed_in_meta_element(env):
    # Hard Rule 5 (CLAUDE.md): generated timestamp + model must be visibly
    # rendered per card, in a dedicated disclosure line -- not merely
    # available in the view model.
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    import re

    meta_matches = re.findall(r'<p class="wire-card__meta[^"]*">.*?</p>', html, flags=re.DOTALL)
    assert meta_matches, "expected at least one wire-card__meta element"
    joined_meta = "\n".join(meta_matches)
    assert CARD_CONFIRMED["model"] in joined_meta
    assert CARD_CONFIRMED["generated_at"] in joined_meta
    assert CARD_REPORTED["model"] in joined_meta
    assert CARD_REPORTED["generated_at"] in joined_meta


def test_render_wire_index_why_it_matters_label_once_per_card(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    # Within the 14-day window there are exactly two cards (CARD_CONFIRMED,
    # CARD_REPORTED) -- the label must appear exactly once per card, as a
    # distinct visual block, not zero or duplicated.
    assert html.count('<p class="wire-card__why-label">Why it matters</p>') == 2


def test_render_wire_index_excludes_cards_outside_the_window(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, window_days=14, today=TODAY)
    assert CARD_CONFIRMED["headline"] in html
    assert CARD_REPORTED["headline"] in html
    assert CARD_CORRECTED_OLD_MONTH["headline"] not in html


def test_render_wire_index_links_archive_months(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    assert '<a href="/wire/2026-07/">July 2026</a>' in html
    assert '<a href="/wire/2026-05/">May 2026</a>' in html


def test_render_wire_index_masthead_sparklines_present_when_passed(env):
    # The masthead sparkline strip is scoped to the Wire home page only
    # (nav-condense pass, see IMPROVEMENT_BACKLOG.md) -- render_wire_index
    # includes it exactly when a caller (site/generate.py, in real use)
    # passes a non-empty masthead_sparklines list.
    from markupsafe import Markup

    fake_sparklines = [
        {"topic": "models", "display_name": "Models", "sparkline_svg": Markup("<svg>fake</svg>")}
    ]
    html = wire.render_wire_index(
        env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY, masthead_sparklines=fake_sparklines
    )
    assert "masthead-strip" in html


def test_render_wire_index_masthead_sparklines_absent_when_not_passed(env):
    html = wire.render_wire_index(env, ALL_CARDS, SYNTHETIC_LEXICON, today=TODAY)
    assert "masthead-strip" not in html


def test_render_wire_index_empty_state_when_no_cards_renders_sensibly(env):
    html = wire.render_wire_index(env, [], SYNTHETIC_LEXICON, today=TODAY)
    assert wire.EMPTY_WIRE_MESSAGE in html
    # Still a well-formed, non-broken page: landmarks from base.html present.
    assert "<html" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html
    assert "<h1" in html
    # No stray card markup when there are no cards (the page's own
    # <style> block legitimately mentions the "wire-card" class name, so
    # this checks for the actual rendered <article> element, not the CSS
    # selector text).
    assert '<article class="wire-card"' not in html
    assert "class=\"one-liner\"" not in html


def test_render_wire_month_renders_only_that_months_cards(env):
    html = wire.render_wire_month(env, ALL_CARDS, SYNTHETIC_LEXICON, "2026-05")
    assert CARD_CORRECTED_OLD_MONTH["headline"] in html
    assert CARD_CONFIRMED["headline"] not in html
    assert CARD_REPORTED["headline"] not in html
    assert "May 2026" in html


def test_render_wire_month_correction_note_visible_as_text(env):
    html = wire.render_wire_month(env, ALL_CARDS, SYNTHETIC_LEXICON, "2026-05")
    assert CARD_CORRECTED_OLD_MONTH["correction_note"] in html


def test_render_wire_month_empty_state_for_month_with_no_cards(env):
    html = wire.render_wire_month(env, ALL_CARDS, SYNTHETIC_LEXICON, "2099-01")
    assert "No cards published for January 2099 yet." in html
    assert "<h1" in html
    assert '<article class="wire-card"' not in html


def test_render_wire_month_links_other_months_not_itself(env):
    html = wire.render_wire_month(env, ALL_CARDS, SYNTHETIC_LEXICON, "2026-07")
    assert '<a href="/wire/2026-05/">May 2026</a>' in html
    assert '<a href="/wire/2026-07/">' not in html


# ---------------------------------------------------------------------------
# write_wire_pages -- end-to-end file output.
# ---------------------------------------------------------------------------


def test_write_wire_pages_writes_index_and_every_month(tmp_path, env):
    written = wire.write_wire_pages(env, ALL_CARDS, SYNTHETIC_LEXICON, tmp_path, today=TODAY)

    index_path = tmp_path / "index.html"
    july_path = tmp_path / "wire" / "2026-07" / "index.html"
    may_path = tmp_path / "wire" / "2026-05" / "index.html"

    assert set(written) == {index_path, july_path, may_path}
    assert index_path.is_file()
    assert july_path.is_file()
    assert may_path.is_file()

    assert CARD_CONFIRMED["headline"] in index_path.read_text(encoding="utf-8")
    assert CARD_CORRECTED_OLD_MONTH["headline"] in may_path.read_text(encoding="utf-8")
    assert CARD_CORRECTED_OLD_MONTH["headline"] not in july_path.read_text(encoding="utf-8")


def test_write_wire_pages_handles_zero_cards(tmp_path, env):
    written = wire.write_wire_pages(env, [], SYNTHETIC_LEXICON, tmp_path, today=TODAY)
    assert written == [tmp_path / "index.html"]
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert wire.EMPTY_WIRE_MESSAGE in html
