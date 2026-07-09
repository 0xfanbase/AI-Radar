"""Tests for site/builders/about.py + site/templates/about.html -- the
static About page (Phase 4, build-plan section 5).

About is intentionally the one Phase 4 page with no `build_*_context()`
computing anything from loaded content/data -- it's a fixed page whose
content is the anonymity/disclaimer/licensing language from CLAUDE.md
section 1, reworded for a reader. These tests assert that static content
is actually present in the rendered output (not just that the page
"renders something"), plus the shared landmarks every other page also
carries.

Loaded by explicit file path (matching `tests/test_board_builder.py`'s
own convention), since `site/` is deliberately not an importable package
-- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ABOUT_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "about.py"


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


about = _load_module_by_path("frontier_wire_site_builders_about", ABOUT_BUILDER_PATH)


def test_render_about_page_has_one_h1_and_main_landmark():
    html = about.render_about_page()
    assert html.count("<h1") == 1
    assert "About" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html


def test_render_about_page_is_deterministic_and_takes_no_arguments():
    first = about.render_about_page()
    second = about.render_about_page()
    assert first == second


# ---------------------------------------------------------------------------
# Anonymity mechanics (CLAUDE.md hard rule 6)
# ---------------------------------------------------------------------------


def test_about_page_names_the_bot_identity():
    html = about.render_about_page()
    assert "frontier-wire-bot" in html


def test_about_page_states_no_personal_identifiers():
    html = about.render_about_page()
    assert "anonymous" in html.lower() or "anonymously" in html.lower()
    assert "personal" in html.lower()


# ---------------------------------------------------------------------------
# Non-commercial / auto-published framing
# ---------------------------------------------------------------------------


def test_about_page_states_non_commercial():
    html = about.render_about_page()
    assert "no ads" in html.lower()
    assert "non-commercial" in html.lower() or "no paid placement" in html.lower()


def test_about_page_states_auto_published_with_no_human_editor():
    html = about.render_about_page()
    assert "automatically" in html.lower()
    assert "Method" in html  # links to the Method page


# ---------------------------------------------------------------------------
# Disclaimer (CLAUDE.md hard rule 5)
# ---------------------------------------------------------------------------


def test_about_page_carries_the_standard_disclaimer_language():
    html = about.render_about_page()
    assert "AI-curated and AI-written" in html
    assert "primary sources" in html.lower()
    assert 'href="/corrections/"' in html


# ---------------------------------------------------------------------------
# Licensing (CLAUDE.md hard rule 7)
# ---------------------------------------------------------------------------


def test_about_page_states_mit_license_for_code():
    html = about.render_about_page()
    assert "MIT" in html


def test_about_page_states_cc_by_4_0_for_editorial_output():
    html = about.render_about_page()
    assert "CC BY" in html
    assert "4.0" in html


def test_about_page_mentions_frontier_board_and_lexicon():
    html = about.render_about_page()
    assert "Frontier Board" in html
    assert "Lexicon" in html
