"""Tests for site/builders/moving.py + site/templates/moving.html + the
thin masthead sparkline-strip partial (`_masthead_moving_strip.html`),
which `site/templates/base.html` renders only on the Wire home page
(Phase 4 build-plan section 5, scope narrowed by the nav-condense pass --
see IMPROVEMENT_BACKLOG.md).

Exercises the REAL, committed `data/whats_moving.json` (the pure-code,
no-AI 7-day topic-velocity snapshot -- 9 fixed topics, per
`schemas/whats_moving.schema.json`) throughout: every topic gets its own
inline SVG sparkline in `/moving/`'s own main list, the masthead strip's
own `build_masthead_sparklines` caps itself to the top
`MASTHEAD_TOPIC_LIMIT` topics by 7-day mention total, and the masthead
strip itself is absent from both `/moving/` and a sibling page (e.g.
`/board/`) -- it is the Wire home page's own opt-in now, exercised in
`site/tests/test_wire_builder.py`, not this module's.

Loaded by explicit file path (matching `site/tests/test_board_builder.py`'s /
`site/tests/test_primer_builder.py`'s own convention), since `site/` is
deliberately not an importable package -- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MOVING_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "moving.py"
BOARD_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "board.py"
WHATS_MOVING_CONTENT_PATH = REPO_ROOT / "data" / "whats_moving.json"
FRONTIER_BOARD_CONTENT_PATH = REPO_ROOT / "content" / "frontier_board.json"


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module: moving.py's dataclasses (combined
    # with `from __future__ import annotations`) need their own module
    # registered under `cls.__module__` for dataclasses' internal
    # annotation resolution to find it -- same requirement documented in
    # site/tests/test_board_builder.py / site/tests/test_linkify.py.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


moving = _load_module_by_path("frontier_wire_site_builders_moving", MOVING_BUILDER_PATH)
board = _load_module_by_path("frontier_wire_site_builders_board", BOARD_BUILDER_PATH)


def _load_real_whats_moving() -> dict:
    with WHATS_MOVING_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


REAL_WHATS_MOVING = _load_real_whats_moving()
REAL_TOPICS = REAL_WHATS_MOVING["topics"]


# ---------------------------------------------------------------------------
# load_whats_moving / real-content sanity
# ---------------------------------------------------------------------------


def test_load_whats_moving_loads_the_real_committed_file():
    data = moving.load_whats_moving()
    assert data == REAL_WHATS_MOVING


def test_real_whats_moving_has_all_nine_canonical_topics():
    # schemas/whats_moving.schema.json's fixed nine-topic enum, always
    # present (zero-filled where there's no HN activity) per
    # IMPROVEMENT_BACKLOG.md's own logged decision.
    assert len(REAL_TOPICS) == 9


# ---------------------------------------------------------------------------
# build_topic_rows -- one sparkline per topic, against REAL content
# ---------------------------------------------------------------------------


def test_build_topic_rows_against_real_content_yields_one_row_per_topic():
    rows = moving.build_topic_rows(REAL_TOPICS)
    assert len(rows) == len(REAL_TOPICS)
    assert [r.topic for r in rows] == [t["topic"] for t in REAL_TOPICS]


def test_build_topic_rows_every_row_has_a_real_labeled_sparkline():
    rows = moving.build_topic_rows(REAL_TOPICS)
    for row in rows:
        svg = str(row.sparkline_svg)
        assert svg.startswith("<svg")
        assert 'role="img"' in svg
        assert "aria-label=" in svg
        assert "<title>" in svg
        # Accessibility rule: the trend is always visible as text, never
        # color/slope alone.
        assert row.trend_label in ("Accelerating", "Cooling", "Flat")


def test_build_topic_rows_preserves_daily_counts_and_totals():
    rows = moving.build_topic_rows(REAL_TOPICS)
    by_topic = {r.topic: r for r in rows}
    for raw in REAL_TOPICS:
        row = by_topic[raw["topic"]]
        assert row.daily_counts == tuple(raw["daily_counts"])
        assert row.total_mentions == sum(raw["daily_counts"])
        assert row.trend == raw["trend"]


def test_build_topic_row_mentions_label_singular_for_a_total_of_one():
    raw = {
        "topic": "products",
        "daily_counts": [0, 0, 0, 0, 0, 0, 1],
        "trend": "flat",
    }
    row = moving.build_topic_row(raw)
    assert row.total_mentions == 1
    assert row.mentions_label == "1 mention / 7d"


def test_build_topic_row_mentions_label_plural_for_totals_other_than_one():
    raw_multiple = {
        "topic": "products",
        "daily_counts": [1, 1, 0, 0, 0, 0, 1],
        "trend": "flat",
    }
    row = moving.build_topic_row(raw_multiple)
    assert row.total_mentions == 3
    assert row.mentions_label == "3 mentions / 7d"

    raw_zero = {
        "topic": "products",
        "daily_counts": [0, 0, 0, 0, 0, 0, 0],
        "trend": "flat",
    }
    row_zero = moving.build_topic_row(raw_zero)
    assert row_zero.total_mentions == 0
    assert row_zero.mentions_label == "0 mentions / 7d"


def test_build_topic_rows_display_names_cover_every_real_topic():
    rows = moving.build_topic_rows(REAL_TOPICS)
    for row in rows:
        assert row.display_name  # never empty
        # A raw enum value that isn't already display-ready (e.g.
        # "chips/compute") must have been given a friendlier display name.
        if row.topic in ("chips/compute", "open-source"):
            assert row.display_name != row.topic


def test_build_topic_rows_handles_empty_topics_gracefully():
    assert moving.build_topic_rows([]) == []


# ---------------------------------------------------------------------------
# build_moving_context / render_moving_page -- against REAL content
# ---------------------------------------------------------------------------


def test_render_moving_page_has_one_h1_and_main_landmark():
    html = moving.render_moving_page(REAL_WHATS_MOVING)
    assert html.count("<h1") == 1
    assert "What's Moving" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html


def test_render_moving_page_lists_every_topic_display_name():
    html = moving.render_moving_page(REAL_WHATS_MOVING)
    for raw in REAL_TOPICS:
        display_name = moving._display_name(raw["topic"])
        assert display_name in html


def test_render_moving_page_has_one_sparkline_per_topic_in_the_main_list_only():
    html = moving.render_moving_page(REAL_WHATS_MOVING)
    # build_moving_context no longer carries masthead_sparklines (the
    # strip is scoped to the Wire home page only -- see this module's
    # top-of-file docstring), so /moving/ renders exactly one <svg> per
    # topic, from its own main list, and no masthead-strip copy on top.
    assert html.count("<svg") == len(REAL_TOPICS)


def test_render_moving_page_mentions_label_pluralizes_correctly():
    # data/whats_moving.json's 7-day totals shift daily as the watcher
    # runs, so derive every topic's expected label from the real, current
    # data rather than a point-in-time snapshot of which topic happened to
    # total exactly 1. The live bug this test guards against was a total
    # of 1 rendering the ungrammatical "1 mentions / 7d".
    html = moving.render_moving_page(REAL_WHATS_MOVING)
    assert "1 mentions / 7d" not in html
    for raw in REAL_TOPICS:
        total = sum(int(c) for c in raw["daily_counts"])
        expected = f"{total} mention{'s' if total != 1 else ''} / 7d"
        assert expected in html


def test_render_moving_page_empty_topics_shows_honest_message_not_a_crash():
    html = moving.render_moving_page({"topics": [], "window_days": 7})
    assert html.count("<h1") == 1
    assert moving.EMPTY_MOVING_MESSAGE in html
    assert "<svg" not in html


# ---------------------------------------------------------------------------
# Masthead sparkline strip -- the thin, site-wide partial
# ---------------------------------------------------------------------------


def test_build_masthead_sparklines_against_real_content_capped_to_the_limit():
    views = moving.build_masthead_sparklines(REAL_TOPICS)
    assert len(views) == moving.MASTHEAD_TOPIC_LIMIT
    for view in views:
        svg = str(view.sparkline_svg)
        assert svg.startswith("<svg")
        assert 'role="img"' in svg


def test_build_masthead_sparklines_returns_exactly_five_ordered_by_descending_total():
    # data/whats_moving.json's 7-day totals shift daily as the watcher
    # runs, so derive the expected top-5 ordering from the real, current
    # data rather than a point-in-time snapshot. Ties keep the topics
    # list's own original relative order -- Python's sorted(...,
    # reverse=True) is guaranteed stable, and so is this expectation
    # (built via a plain, unreversed sort by descending total).
    views = moving.build_masthead_sparklines(REAL_TOPICS)
    assert len(views) == 5

    totals = {t["topic"]: sum(int(c) for c in t["daily_counts"]) for t in REAL_TOPICS}
    original_order = [t["topic"] for t in REAL_TOPICS]
    expected_order = sorted(original_order, key=lambda topic: -totals[topic])[:5]

    assert [v.topic for v in views] == expected_order
    ranked_totals = [totals[v.topic] for v in views]
    assert ranked_totals == sorted(ranked_totals, reverse=True)


def test_masthead_sparklines_render_smaller_than_the_full_page_sparklines():
    full_svg = str(moving.build_topic_rows(REAL_TOPICS)[0].sparkline_svg)
    masthead_svg = str(moving.build_masthead_sparklines(REAL_TOPICS)[0].sparkline_svg)
    full_width_match = re.search(r'width="(\d+)"', full_svg)
    masthead_width_match = re.search(r'width="(\d+)"', masthead_svg)
    assert full_width_match and masthead_width_match
    assert int(masthead_width_match.group(1)) < int(full_width_match.group(1))


def test_render_masthead_strip_standalone_links_to_moving_page():
    html = moving.render_masthead_strip(REAL_WHATS_MOVING)
    assert 'href="/moving/"' in html
    # Capped to MASTHEAD_TOPIC_LIMIT, not one per real topic -- see
    # build_masthead_sparklines.
    assert html.count("<svg") == moving.MASTHEAD_TOPIC_LIMIT


def test_render_masthead_strip_standalone_handles_empty_topics_gracefully():
    html = moving.render_masthead_strip({"topics": []})
    assert "<svg" not in html
    assert 'href="/moving/"' in html


# ---------------------------------------------------------------------------
# base.html wiring: the masthead strip is scoped to the Wire home page
# only now -- /moving/ (this module's own page) no longer opts in, same
# as every other non-home page.
# ---------------------------------------------------------------------------


def test_masthead_strip_absent_from_the_moving_page():
    html = moving.render_moving_page(REAL_WHATS_MOVING)
    assert "masthead-strip" not in html


def test_masthead_strip_absent_from_a_sibling_page_that_does_not_opt_in():
    from datetime import date

    with FRONTIER_BOARD_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        board_rows = json.load(fh)
    html = board.render_board_page(board_rows, today=date(2026, 7, 9))
    assert "masthead-strip" not in html
    # Sanity: board.html's own content is unaffected -- still exactly one
    # <h1>, matching site/tests/test_board_builder.py's own assertion.
    assert html.count("<h1") == 1
