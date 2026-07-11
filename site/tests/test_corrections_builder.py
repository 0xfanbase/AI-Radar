"""Tests for site/builders/corrections.py + site/templates/corrections.html
-- the public Corrections log (Phase 4, build-plan section 5).

The important case this file exists to prove: the REAL, committed
`content/corrections.json` is `[]` today (no card has ever needed a
correction, since no analyst run has happened for real in this
environment yet), and site/builders/corrections.py must render an honest
"no corrections have been needed yet" empty state for that -- never a
crash or a broken-looking page. A synthetic multi-entry fixture covers
the non-empty list-rendering/sort-order path the real, current-state data
doesn't happen to exercise.

Loaded by explicit file path (matching `site/tests/test_board_builder.py`'s
own convention), since `site/` is deliberately not an importable package
-- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from markupsafe import escape

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORRECTIONS_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "corrections.py"
CORRECTIONS_CONTENT_PATH = REPO_ROOT / "content" / "corrections.json"


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module: corrections.py's dataclasses (combined
    # with `from __future__ import annotations`) need their own module
    # registered under `cls.__module__` for dataclasses' internal
    # annotation resolution to find it -- same requirement documented in
    # site/tests/test_board_builder.py / site/tests/test_linkify.py.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


corrections = _load_module_by_path(
    "frontier_wire_site_builders_corrections", CORRECTIONS_BUILDER_PATH
)


def _load_real_corrections() -> list[dict]:
    with CORRECTIONS_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


REAL_CORRECTIONS = _load_real_corrections()


# ---------------------------------------------------------------------------
# THE important case: the REAL content/corrections.json is empty today
# ---------------------------------------------------------------------------


def test_real_corrections_content_is_currently_empty():
    # Precondition the rest of this section's empty-state assertions rest
    # on -- if this ever fails because a real correction has since been
    # published, that's good news (the pipeline worked), but the
    # empty-state path below stops being exercised against real content
    # and this test (plus the module's own docstring) needs an update.
    assert REAL_CORRECTIONS == []


def test_load_corrections_loads_the_real_committed_file():
    assert corrections.load_corrections() == []


def test_build_corrections_context_for_the_real_empty_file():
    context = corrections.build_corrections_context(REAL_CORRECTIONS)
    assert context["corrections"] == []
    assert context["total_corrections"] == 0
    assert context["empty_message"] == corrections.EMPTY_CORRECTIONS_MESSAGE


def test_render_corrections_page_against_the_real_empty_file_shows_honest_empty_state():
    html = corrections.render_corrections_page(REAL_CORRECTIONS)
    assert html.count("<h1") == 1
    assert "Corrections" in html
    assert corrections.EMPTY_CORRECTIONS_MESSAGE in html
    assert "No corrections have been needed yet" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html
    # No correction-card *element* should appear for an empty list (the
    # class name itself still appears once, in this page's own scoped
    # <style> block, regardless of whether any cards are rendered).
    assert '<li class="correction-card">' not in html


def test_full_real_environment_render_end_to_end():
    loaded = corrections.load_corrections()
    html = corrections.render_corrections_page(loaded)
    assert "No corrections have been needed yet" in html


# ---------------------------------------------------------------------------
# A synthetic non-empty corrections list -- the real, current-state data
# doesn't exercise this path yet, so a fixture covers it.
# ---------------------------------------------------------------------------


SYNTHETIC_CORRECTIONS = [
    {
        "id": "corr-2026-07-01-a",
        "card_id": "2026-06-30-example-card",
        "original_claim": "The model has a 10 million token context window.",
        "corrected_claim": "The model has a 1 million token context window.",
        "reason": "Misread the primary source's own published spec.",
        "source_url": "https://example.com/spec",
        "corrected_at": "2026-07-01T12:00:00Z",
    },
    {
        "id": "corr-2026-07-05-b",
        "card_id": "2026-07-04-another-card",
        "original_claim": "The lab is based in Paris.",
        "corrected_claim": "The lab is based in London.",
        "reason": "Outlet source had the wrong city; corrected against the lab's own about page.",
        "source_url": "https://example.com/about",
        "corrected_at": "2026-07-05T09:30:00Z",
    },
]


def test_build_corrections_context_sorts_newest_first():
    context = corrections.build_corrections_context(SYNTHETIC_CORRECTIONS)
    assert [c.id for c in context["corrections"]] == [
        "corr-2026-07-05-b",
        "corr-2026-07-01-a",
    ]
    assert context["total_corrections"] == 2


def test_render_corrections_page_shows_every_synthetic_correction():
    html = corrections.render_corrections_page(SYNTHETIC_CORRECTIONS)
    assert "No corrections have been needed yet" not in html
    for entry in SYNTHETIC_CORRECTIONS:
        # Autoescaped by Jinja same as any other plain-text field (an
        # apostrophe becomes `&#39;` etc.) -- compare against the escaped
        # form, matching site/tests/test_primer_builder.py's own convention.
        assert str(escape(entry["original_claim"])) in html
        assert str(escape(entry["corrected_claim"])) in html
        assert str(escape(entry["reason"])) in html
        assert f'href="{entry["source_url"]}"' in html
    assert html.count('<li class="correction-card">') == 2


def test_render_corrections_page_orders_newest_correction_first_in_markup():
    html = corrections.render_corrections_page(SYNTHETIC_CORRECTIONS)
    newer_pos = html.index("The lab is based in Paris.")
    older_pos = html.index("The model has a 10 million token context window.")
    assert newer_pos < older_pos


# ---------------------------------------------------------------------------
# card_href -- cross-link back to the original card (T6, CLAUDE.md Hard
# Rule 5: corrections must link back to the card they correct).
# ---------------------------------------------------------------------------


def test_card_href_for_computes_month_archive_anchor():
    assert (
        corrections.card_href_for("2026-07-09-example-story")
        == "/wire/2026-07/#card-2026-07-09-example-story"
    )


def test_build_correction_view_sets_card_href():
    view = corrections.build_correction_view(
        {
            "id": "corr-x",
            "card_id": "2026-07-09-example-story",
            "original_claim": "a",
            "corrected_claim": "b",
            "reason": "c",
            "source_url": "https://example.com",
            "corrected_at": "2026-07-09T00:00:00Z",
        }
    )
    assert view.card_href == "/wire/2026-07/#card-2026-07-09-example-story"


def test_render_corrections_page_links_back_to_the_original_card():
    fixture = [
        {
            "id": "corr-2026-07-09-a",
            "card_id": "2026-07-09-example-story",
            "original_claim": "The model has 10B parameters.",
            "corrected_claim": "The model has 100B parameters.",
            "reason": "Misread the primary source.",
            "source_url": "https://example.com/spec",
            "corrected_at": "2026-07-09T12:00:00Z",
        }
    ]
    html = corrections.render_corrections_page(fixture)
    assert 'href="/wire/2026-07/#card-2026-07-09-example-story">read the original story</a>' in html
    # The raw card_id slug is never surfaced to readers as visible text.
    assert ">2026-07-09-example-story<" not in html
