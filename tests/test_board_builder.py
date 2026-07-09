"""Tests for site/builders/board.py + site/templates/board.html -- the
Frontier Board "observatory status wall" (Phase 4, build-plan section 5).

Loaded by explicit file path (matching site/tests/test_build.py's own
convention for site/generate.py, and tests/test_linkify.py /
tests/test_svg_sparkline.py's convention for site/lib modules) rather
than an `import site.builders.board` package import, since `site` is also
a stdlib module name and this directory is deliberately not turned into
an importable package -- see IMPROVEMENT_BACKLOG.md.

Exercises the real, committed ``content/frontier_board.json`` (13 seeded
rows spanning US/China/open-weights, per Phase 3) for the
region/row-count/source-link assertions, and small synthetic row dicts
for the pulse-eligibility assertions -- the real file's 13 rows all share
today's own `last_verified` date, so a synthetic recent-vs-old pair is
the only way to exercise both branches of the 7-day pulse window
deterministically.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOARD_PATH = REPO_ROOT / "site" / "builders" / "board.py"
FRONTIER_BOARD_CONTENT_PATH = REPO_ROOT / "content" / "frontier_board.json"


def _load_board_module():
    spec = importlib.util.spec_from_file_location(
        "frontier_wire_site_builders_board", BOARD_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules *before* exec_module: board.py's dataclasses
    # (combined with `from __future__ import annotations`) need their own
    # module registered under `cls.__module__` for dataclasses' internal
    # annotation resolution to find it -- same requirement documented in
    # tests/test_linkify.py / tests/test_svg_sparkline.py.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


board = _load_board_module()


def _load_real_board_rows() -> list[dict]:
    with FRONTIER_BOARD_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# A fixed "today" that matches the real seed content's own last_verified
# date, so the region/row-count/source-link assertions below don't
# accidentally depend on wall-clock time.
REAL_CONTENT_TODAY = date(2026, 7, 9)


# ---------------------------------------------------------------------------
# is_pulse_eligible -- pure function, unit-level
# ---------------------------------------------------------------------------


def test_is_pulse_eligible_true_for_same_day_verification():
    assert board.is_pulse_eligible("2026-07-09", today=date(2026, 7, 9)) is True


def test_is_pulse_eligible_true_at_exactly_the_7_day_boundary():
    assert board.is_pulse_eligible("2026-07-02", today=date(2026, 7, 9)) is True


def test_is_pulse_eligible_false_one_day_past_the_boundary():
    assert board.is_pulse_eligible("2026-07-01", today=date(2026, 7, 9)) is False


def test_is_pulse_eligible_false_for_old_date():
    assert board.is_pulse_eligible("2026-06-01", today=date(2026, 7, 9)) is False


def test_is_pulse_eligible_false_for_a_future_last_verified_date():
    # Clock skew / bad seed data should never light the pulse dot.
    assert board.is_pulse_eligible("2026-07-20", today=date(2026, 7, 9)) is False


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_format_context_window_none_is_not_disclosed():
    assert board.format_context_window(None) == "not disclosed"


def test_format_context_window_formats_with_thousands_separators():
    assert board.format_context_window(1000000) == "1,000,000"


def test_format_modality_joins_with_comma_space():
    assert board.format_modality(["text", "image"]) == "text, image"


def test_source_host_strips_scheme_and_www():
    assert board.source_host("https://www.example.com/a/b") == "example.com"


def test_source_host_keeps_subdomain_when_not_www():
    assert (
        board.source_host("https://platform.claude.com/docs/x")
        == "platform.claude.com"
    )


# ---------------------------------------------------------------------------
# build_regions / build_context against the REAL content/frontier_board.json
# ---------------------------------------------------------------------------


def test_build_regions_against_real_content_yields_one_table_per_present_region():
    rows = _load_real_board_rows()
    regions = board.build_regions(rows, today=REAL_CONTENT_TODAY)
    region_keys = [r.key for r in regions]
    # The real seed content spans exactly these three regions (Phase 3's
    # acceptance bar: "spanning US/China/open-weights").
    assert region_keys == ["US", "China", "open-weights"]


def test_build_regions_against_real_content_has_correct_total_row_count():
    rows = _load_real_board_rows()
    regions = board.build_regions(rows, today=REAL_CONTENT_TODAY)
    total = sum(len(region.rows) for region in regions)
    assert total == len(rows) == 13


def test_build_regions_against_real_content_has_correct_per_region_counts():
    rows = _load_real_board_rows()
    regions = board.build_regions(rows, today=REAL_CONTENT_TODAY)
    counts = {region.key: len(region.rows) for region in regions}
    expected = {
        row["region"]: sum(1 for r in rows if r["region"] == row["region"])
        for row in rows
    }
    assert counts == expected


def test_build_regions_order_is_fixed_regardless_of_input_row_order():
    rows = _load_real_board_rows()
    shuffled = list(reversed(rows))
    regions = board.build_regions(shuffled, today=REAL_CONTENT_TODAY)
    assert [r.key for r in regions] == ["US", "China", "open-weights"]


def test_build_regions_omits_a_region_with_zero_rows():
    rows = [r for r in _load_real_board_rows() if r["region"] != "open-weights"]
    regions = board.build_regions(rows, today=REAL_CONTENT_TODAY)
    assert [r.key for r in regions] == ["US", "China"]


def test_build_regions_handles_empty_input_gracefully():
    # content/cards/ isn't the only thing that can legitimately be empty
    # at this build stage -- board.py must not crash on a zero-row board
    # either.
    assert board.build_regions([], today=REAL_CONTENT_TODAY) == []


def test_build_context_reports_total_rows_and_pulse_window():
    rows = _load_real_board_rows()
    context = board.build_context(rows, today=REAL_CONTENT_TODAY)
    assert context["total_rows"] == 13
    assert context["pulse_window_days"] == board.PULSE_WINDOW_DAYS
    assert context["today"] == "2026-07-09"


# ---------------------------------------------------------------------------
# render_board_page against the REAL content/frontier_board.json
# ---------------------------------------------------------------------------


def test_render_board_page_against_real_content_has_one_h1_and_page_title():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    assert html.count("<h1") == 1
    assert "Frontier Board" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html


def test_render_board_page_against_real_content_has_exactly_one_table_per_region():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    assert html.count("<table") == 3


def test_render_board_page_headings_precede_and_are_associated_with_their_table():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    for key, heading_text in (
        ("US", "US"),
        ("China", "China"),
        ("open-weights", "Open Weights"),
    ):
        slug = key.lower().replace(" ", "-")
        heading_id = f"board-region-{slug}-heading"
        table_id = f"board-region-{slug}-table"

        heading_marker = f'<h2 id="{heading_id}">{heading_text}</h2>'
        table_marker = f'id="{table_id}"'
        aria_marker = f'aria-labelledby="{heading_id}"'

        assert heading_marker in html
        assert table_marker in html
        # The table (identified by its own table_id) is associated with
        # the heading via aria-labelledby, for screen readers.
        assert aria_marker in html

        heading_pos = html.index(heading_marker)
        table_pos = html.index(table_marker)
        assert heading_pos < table_pos, (
            f"heading for region {key!r} must appear before its table"
        )


def test_render_board_page_region_tables_appear_in_fixed_order():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    us_pos = html.index('id="board-region-us-heading"')
    china_pos = html.index('id="board-region-china-heading"')
    open_weights_pos = html.index('id="board-region-open-weights-heading"')
    assert us_pos < china_pos < open_weights_pos


def test_render_board_page_columns_present_in_each_table_header():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    for column in (
        "Lab",
        "Model",
        "Released",
        "Modality",
        "Context",
        "Access",
        "Significance",
        "Source",
    ):
        assert f"<th scope=\"col\">{column}</th>" in html


def test_render_board_page_row_count_matches_real_content():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # One <th scope="row"> per Board row (the Lab cell), across all
    # tables combined.
    assert html.count('<th scope="row">') == 13


def test_render_board_page_uses_jetbrains_mono_data_styling_with_tabular_figures():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # Reuses components.css's existing `.data` utility class (JetBrains
    # Mono + tabular-nums font-feature), applied to every board table --
    # see components.css's own header comment for what `.data` renders.
    assert html.count('class="board-table data"') == 3


def test_render_board_page_handles_zero_rows_gracefully():
    html = board.render_board_page([], today=REAL_CONTENT_TODAY)
    assert "<table" not in html
    assert "No frontier-model rows are tracked yet" in html
    assert 'id="main-content"' in html


# ---------------------------------------------------------------------------
# Source cells are real <a href="..."> link elements
# ---------------------------------------------------------------------------


def test_render_board_page_source_cells_are_real_link_elements():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    for row in rows:
        url = row["source_url"]
        # Exact structural match against this template's own markup: a
        # real anchor element inside the Source <td>, not just the bare
        # URL string appearing somewhere as plain text.
        assert f'<td><a href="{url}">' in html, (
            f"expected a real <a href> link element for {url!r}"
        )


def test_render_board_page_source_link_count_matches_row_count():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    hrefs = re.findall(r'<td><a href="[^"]+">', html)
    assert len(hrefs) == 13


# ---------------------------------------------------------------------------
# Pulse dot -- synthetic recent-vs-old last_verified pair
# ---------------------------------------------------------------------------


def _synthetic_row(**overrides) -> dict:
    base = {
        "lab": "Synthetic Lab",
        "region": "US",
        "model": "Synthbench One",
        "release_date": "2026-06-01",
        "modality": ["text"],
        "context_window": 128000,
        "access": "api",
        "significance": "A synthetic row used only for pulse-dot tests.",
        "source_url": "https://example.com/synthbench-one",
        "last_verified": "2026-07-09",
    }
    base.update(overrides)
    return base


def test_pulse_eligible_row_has_dot_and_ineligible_row_does_not():
    today = date(2026, 7, 9)
    recent_row = _synthetic_row(
        model="Synthbench Recent", last_verified="2026-07-05"  # 4 days ago
    )
    old_row = _synthetic_row(
        model="Synthbench Old", last_verified="2026-06-01"  # well over 30 days ago
    )
    regions = board.build_regions([recent_row, old_row], today=today)
    assert len(regions) == 1
    rows_by_model = {row.model: row for row in regions[0].rows}
    assert rows_by_model["Synthbench Recent"].pulse_eligible is True
    assert rows_by_model["Synthbench Old"].pulse_eligible is False


def test_rendered_pulse_dot_present_only_for_the_recent_synthetic_row():
    today = date(2026, 7, 9)
    recent_row = _synthetic_row(
        model="Synthbench Recent", last_verified="2026-07-05"
    )
    old_row = _synthetic_row(model="Synthbench Old", last_verified="2026-06-01")
    html = board.render_board_page([recent_row, old_row], today=today)

    # Count actual <span class="board-pulse-dot"> elements, not the CSS
    # class name (which also appears in the page's own <style> rules).
    dot_spans = re.findall(r'<span[^>]*class="board-pulse-dot"[^>]*>', html)
    assert len(dot_spans) == 1

    # The dot must sit in the recent row's Model cell, not the old row's.
    recent_idx = html.index("Synthbench Recent")
    old_idx = html.index("Synthbench Old")
    dot_idx = re.search(r'<span[^>]*class="board-pulse-dot"[^>]*>', html).start()
    recent_row_end = html.index("</tr>", recent_idx)
    old_row_end = html.index("</tr>", old_idx)
    assert recent_idx < dot_idx < recent_row_end
    assert not (old_idx < dot_idx < old_row_end)


def test_rendered_pulse_dot_is_aria_hidden():
    today = date(2026, 7, 9)
    recent_row = _synthetic_row(last_verified="2026-07-09")
    html = board.render_board_page([recent_row], today=today)
    span_match = re.search(r"<span[^>]*class=\"board-pulse-dot\"[^>]*>", html)
    assert span_match is not None, "expected a <span class=\"board-pulse-dot\"> element"
    assert 'aria-hidden="true"' in span_match.group(0)


def test_pulse_animation_keyframes_only_inside_reduced_motion_media_query():
    today = date(2026, 7, 9)
    html = board.render_board_page([_synthetic_row()], today=today)
    media_pos = html.index("@media (prefers-reduced-motion: no-preference)")
    keyframes_pos = html.index("@keyframes board-pulse")
    assert media_pos < keyframes_pos
    # No unguarded `.board-pulse-dot { animation` rule exists outside the
    # media query -- the only "animation:" declaration in the whole
    # document appears after the media query opens.
    animation_pos = html.index("animation:")
    assert media_pos < animation_pos
