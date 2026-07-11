"""Tests for site/builders/board.py + site/templates/board.html -- the
Frontier Board "observatory status wall" (Phase 4, build-plan section 5).

Loaded by explicit file path (matching site/tests/test_build.py's own
convention for site/generate.py, and site/tests/test_linkify.py /
site/tests/test_svg_sparkline.py's convention for site/lib modules) rather
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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOARD_PATH = REPO_ROOT / "site" / "builders" / "board.py"
FRONTIER_BOARD_CONTENT_PATH = REPO_ROOT / "content" / "frontier_board.json"
LEXICON_CONTENT_PATH = REPO_ROOT / "content" / "lexicon.json"


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
    # site/tests/test_linkify.py / site/tests/test_svg_sparkline.py.
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


def test_is_pulse_eligible_false_for_malformed_last_verified_without_raising():
    # A `last_verified` that isn't a parseable ISO date (schema validation
    # should catch this upstream, but this function must not assume that
    # happened -- see its own docstring) must degrade to "not eligible",
    # never raise.
    assert board.is_pulse_eligible("not-a-date", today=date(2026, 7, 9)) is False


def test_is_pulse_eligible_false_for_missing_last_verified_without_raising():
    assert board.is_pulse_eligible("", today=date(2026, 7, 9)) is False


def test_build_regions_does_not_crash_on_a_row_with_malformed_last_verified():
    raw_row = {
        "lab": "Synthlab",
        "region": "US",
        "model": "Synthbench",
        "release_date": "2026-01-01",
        "modality": ["text"],
        "access": "api",
        "significance": "sig",
        "source_url": "https://example.com",
        "last_verified": "not-a-date",
    }
    regions = board.build_regions([raw_row], today=date(2026, 7, 9))
    assert regions[0].rows[0].pulse_eligible is False


def test_build_regions_does_not_crash_on_a_row_with_missing_last_verified():
    raw_row = {
        "lab": "Synthlab",
        "region": "US",
        "model": "Synthbench",
        "release_date": "2026-01-01",
        "modality": ["text"],
        "access": "api",
        "significance": "sig",
        "source_url": "https://example.com",
        # last_verified deliberately absent
    }
    regions = board.build_regions([raw_row], today=date(2026, 7, 9))
    assert regions[0].rows[0].pulse_eligible is False


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


def test_render_board_page_against_real_content_has_exactly_one_row_list_per_region():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # Table->details redesign (see IMPROVEMENT_BACKLOG.md): one
    # <ul class="board-list"> per present region, replacing the old
    # one-<table>-per-region markup.
    assert html.count('<ul class="board-list"') == 3


def test_render_board_page_headings_precede_and_are_associated_with_their_row_list():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    for key, heading_text in (
        ("US", "US"),
        ("China", "China"),
        ("open-weights", "Open Weights"),
    ):
        slug = key.lower().replace(" ", "-")
        heading_id = f"board-region-{slug}-heading"

        heading_marker = f'<h2 id="{heading_id}">{heading_text}</h2>'
        list_marker = f'<ul class="board-list" aria-labelledby="{heading_id}">'

        assert heading_marker in html
        # The row-list (a <ul>) is associated with the heading via
        # aria-labelledby, for screen readers.
        assert list_marker in html

        heading_pos = html.index(heading_marker)
        list_pos = html.index(list_marker)
        assert heading_pos < list_pos, (
            f"heading for region {key!r} must appear before its row list"
        )


def test_render_board_page_region_tables_appear_in_fixed_order():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    us_pos = html.index('id="board-region-us-heading"')
    china_pos = html.index('id="board-region-china-heading"')
    open_weights_pos = html.index('id="board-region-open-weights-heading"')
    assert us_pos < china_pos < open_weights_pos


def test_render_board_page_facts_dt_labels_present_once_per_row():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # The old 8-column header row (Lab/Model/Released/Modality/Context/
    # Access/Significance/Source) is gone; the three facts that still
    # need an explicit label in the new per-row <dl> are Modality,
    # Context window, and Source -- Lab/Model/Released/Access are
    # self-evident from their position/styling in the summary line.
    for label in ("Modality", "Context window", "Source"):
        assert html.count(f"<dt>{label}</dt>") == 13


def test_render_board_page_row_count_matches_real_content():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # One <details class="board-row"> per Board row, across all regions
    # combined.
    assert html.count('<details class="board-row">') == 13


def test_render_board_page_never_uses_the_old_monospace_table_styling():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # The old `.data` (whole-table JetBrains Mono) styling is gone
    # entirely -- monospace is now scoped only to the genuinely tabular
    # Released/Context values via `.font-data`.
    assert "board-table data" not in html
    assert html.count('class="board-row__released font-data"') == 13
    assert html.count('class="font-data"') == 13  # the Context window <dd>s
    assert 'class="board-row__significance"' in html
    assert 'board-row__significance font-data' not in html


def test_render_board_page_handles_zero_rows_gracefully():
    html = board.render_board_page([], today=REAL_CONTENT_TODAY)
    assert "<details" not in html
    assert "<table" not in html
    assert "No frontier-model rows are tracked yet" in html
    assert 'id="main-content"' in html


# ---------------------------------------------------------------------------
# Source links are real <a href="..."> link elements
# ---------------------------------------------------------------------------


def test_render_board_page_source_cells_are_real_link_elements():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    for row in rows:
        url = row["source_url"]
        # A real anchor element for the Source fact, not just the bare
        # URL string appearing somewhere as plain text.
        assert f'<a href="{url}">' in html, (
            f"expected a real <a href> link element for {url!r}"
        )


def test_render_board_page_source_link_count_matches_row_count():
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # Scoped to each row's Source fact (`<dd><a href="...">`) so the
    # shared shell's own nav links (Board/Lexicon/Primer/... in
    # base.html, present on every page) don't inflate the count.
    hrefs = re.findall(r'<dd><a href="[^"]+">', html)
    assert len(hrefs) == 13


# ---------------------------------------------------------------------------
# Significance prose auto-linking against content/lexicon.json
# ---------------------------------------------------------------------------


def _load_real_lexicon_entries() -> list[dict]:
    with LEXICON_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _significance_paragraphs(html: str) -> list[str]:
    return re.findall(
        r'<p class="board-row__significance">(.*?)</p>', html, re.DOTALL
    )


def test_render_board_page_with_real_lexicon_linkifies_significance_prose():
    rows = _load_real_board_rows()
    lexicon_entries = _load_real_lexicon_entries()
    html = board.render_board_page(
        rows, today=REAL_CONTENT_TODAY, lexicon_entries=lexicon_entries
    )
    paragraphs = _significance_paragraphs(html)
    assert len(paragraphs) == 13
    # The real seed data genuinely contains literal lexicon-term matches
    # (e.g. "context window" appears in 8 of the 13 rows' significance
    # prose) -- at least one significance paragraph must contain a real
    # lexicon anchor.
    assert any('<a href="/lexicon/' in p for p in paragraphs)


def test_render_board_page_without_lexicon_entries_has_raw_text_and_no_lexicon_anchors():
    # Back-compat: every pre-existing call site that doesn't pass
    # lexicon_entries at all must keep rendering the same raw (escaped)
    # significance prose, with zero lexicon anchors.
    rows = _load_real_board_rows()
    html = board.render_board_page(rows, today=REAL_CONTENT_TODAY)
    # Scoped to significance paragraphs -- base.html's own shared nav
    # always links to "/lexicon/" (the Lexicon index page) on every page,
    # so the absence check must not be a whole-page substring search.
    paragraphs = _significance_paragraphs(html)
    assert len(paragraphs) == 13
    assert not any("/lexicon/" in p for p in paragraphs)
    # A verbatim (HTML-special-character-free) fragment of the DeepSeek
    # row's real significance prose survives untouched.
    assert "extends default context to 1 million tokens" in html


def test_long_significance_prose_stays_in_body_font_never_monospace():
    # Regression test: a 100+-word significance string must render
    # inside the plain-prose `.board-row__significance` element, never
    # tagged `font-data`/`data` (the bug this redesign fixes), regardless
    # of length.
    long_significance = " ".join(["Alpha", "beta", "gamma", "delta", "epsilon"] * 22)
    assert len(long_significance.split()) >= 100
    row = _synthetic_row(significance=long_significance)
    html = board.render_board_page([row], today=date(2026, 7, 9))

    match = re.search(
        r'<p class="([^"]*)">' + re.escape(long_significance) + r"</p>", html
    )
    assert match is not None, "expected the full significance prose in its own <p>"
    classes = match.group(1)
    assert classes == "board-row__significance"
    assert "font-data" not in classes
    assert "data" not in classes


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

    # The dot must sit in the recent row's <details> row-card, not the
    # old row's.
    recent_idx = html.index("Synthbench Recent")
    old_idx = html.index("Synthbench Old")
    dot_idx = re.search(r'<span[^>]*class="board-pulse-dot"[^>]*>', html).start()
    recent_row_end = html.index("</details>", recent_idx)
    old_row_end = html.index("</details>", old_idx)
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
