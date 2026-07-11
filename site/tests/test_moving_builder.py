"""Tests for site/builders/moving.py + site/templates/moving.html + the
thin masthead sparkline-strip partial (`_masthead_moving_strip.html`),
now wired into `site/templates/base.html` (Phase 4, build-plan section
5).

Exercises the REAL, committed `data/whats_moving.json` (the pure-code,
no-AI 7-day topic-velocity snapshot -- 9 fixed topics, per
`schemas/whats_moving.schema.json`) throughout: every topic gets its own
inline SVG sparkline, and the masthead strip partial renders for a page
whose builder opts in (this module's own `/moving/` page) while staying
completely absent from a sibling page's render (e.g. `/board/`), proving
the `base.html` edit is backward-compatible with every already-committed
builder.

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


def test_render_moving_page_has_one_sparkline_per_topic_in_the_main_list_plus_the_masthead_strip():
    html = moving.render_moving_page(REAL_WHATS_MOVING)
    # This page's own render context also carries masthead_sparklines
    # (see build_moving_context), so base.html's masthead strip renders
    # too -- one <svg> per topic there, on top of one per topic in the
    # main /moving/ list itself.
    assert html.count("<svg") == 2 * len(REAL_TOPICS)


def test_render_moving_page_empty_topics_shows_honest_message_not_a_crash():
    html = moving.render_moving_page({"topics": [], "window_days": 7})
    assert html.count("<h1") == 1
    assert moving.EMPTY_MOVING_MESSAGE in html
    assert "<svg" not in html


# ---------------------------------------------------------------------------
# Masthead sparkline strip -- the thin, site-wide partial
# ---------------------------------------------------------------------------


def test_build_masthead_sparklines_against_real_content_one_per_topic():
    views = moving.build_masthead_sparklines(REAL_TOPICS)
    assert len(views) == len(REAL_TOPICS)
    for view in views:
        svg = str(view.sparkline_svg)
        assert svg.startswith("<svg")
        assert 'role="img"' in svg


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
    assert html.count("<svg") == len(REAL_TOPICS)


def test_render_masthead_strip_standalone_handles_empty_topics_gracefully():
    html = moving.render_masthead_strip({"topics": []})
    assert "<svg" not in html
    assert 'href="/moving/"' in html


# ---------------------------------------------------------------------------
# base.html wiring: the masthead strip only appears on a page whose own
# builder opts in -- proving this turn's base.html edit doesn't change
# any already-committed sibling page's rendered output.
# ---------------------------------------------------------------------------


def test_masthead_strip_present_on_the_moving_page():
    html = moving.render_moving_page(REAL_WHATS_MOVING)
    assert "masthead-strip" in html


def test_masthead_strip_absent_from_a_sibling_page_that_does_not_opt_in():
    from datetime import date

    with FRONTIER_BOARD_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        board_rows = json.load(fh)
    html = board.render_board_page(board_rows, today=date(2026, 7, 9))
    assert "masthead-strip" not in html
    # Sanity: board.html's own content is unaffected -- still exactly one
    # <h1>, matching site/tests/test_board_builder.py's own assertion.
    assert html.count("<h1") == 1
