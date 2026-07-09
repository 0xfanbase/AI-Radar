"""Tests for site/builders/primer.py + site/templates/primer.html -- the
fixed, dependency-ordered ten-term reading sequence (Phase 4, build-plan
section 5).

Exercises the REAL, committed `content/primer.json` (10 ordered slugs,
per Phase 3) against the REAL, committed `content/lexicon.json` (30
seeded terms) throughout: every primer slug must resolve to a real
generated lexicon page, in the fixed order, reusing each term's own
`one_liner` verbatim rather than inventing new copy.

Loaded by explicit file path (matching `tests/test_board_builder.py`'s /
`tests/test_lexicon_builder.py`'s own convention), since `site/` is
deliberately not an importable package -- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from markupsafe import escape

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIMER_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "primer.py"
LEXICON_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "lexicon.py"
PRIMER_CONTENT_PATH = REPO_ROOT / "content" / "primer.json"
LEXICON_CONTENT_PATH = REPO_ROOT / "content" / "lexicon.json"


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module: both modules' dataclasses (combined
    # with `from __future__ import annotations`) need their own module
    # registered under `cls.__module__` for dataclasses' internal
    # annotation resolution to find it -- same requirement documented in
    # tests/test_linkify.py / tests/test_lexicon_builder.py.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


primer = _load_module_by_path("frontier_wire_site_builders_primer", PRIMER_BUILDER_PATH)
lexicon = _load_module_by_path("frontier_wire_site_builders_lexicon", LEXICON_BUILDER_PATH)


def _load_real_primer() -> dict:
    with PRIMER_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_real_lexicon() -> list[dict]:
    with LEXICON_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


REAL_PRIMER = _load_real_primer()
REAL_LEXICON = _load_real_lexicon()

EXPECTED_ORDER = [
    "foundation-model",
    "transformer",
    "attention",
    "parameter-count",
    "context-window",
    "pretraining",
    "fine-tuning",
    "rlhf",
    "hallucination",
    "open-weights",
]


# ---------------------------------------------------------------------------
# Real content sanity
# ---------------------------------------------------------------------------


def test_real_primer_has_exactly_10_terms():
    assert len(REAL_PRIMER["terms"]) == 10


def test_real_primer_matches_the_expected_dependency_order():
    assert REAL_PRIMER["terms"] == EXPECTED_ORDER


# ---------------------------------------------------------------------------
# build_slug_to_entry / build_steps against REAL content
# ---------------------------------------------------------------------------


def test_build_slug_to_entry_real_content_has_all_30_terms():
    slug_to_entry = primer.build_slug_to_entry(REAL_LEXICON)
    assert len(slug_to_entry) == 30
    for slug in EXPECTED_ORDER:
        assert slug in slug_to_entry


def test_build_steps_real_content_all_10_slugs_resolve_in_order():
    steps = primer.build_steps(REAL_PRIMER["terms"], REAL_LEXICON)
    assert [s.slug for s in steps] == EXPECTED_ORDER
    assert [s.step for s in steps] == list(range(1, 11))
    assert all(s.total_steps == 10 for s in steps)


def test_build_steps_reuses_one_liner_verbatim_no_invented_copy():
    steps = primer.build_steps(REAL_PRIMER["terms"], REAL_LEXICON)
    slug_to_entry = primer.build_slug_to_entry(REAL_LEXICON)
    for step in steps:
        real_one_liner = slug_to_entry[step.slug]["one_liner"]
        assert step.one_liner == real_one_liner


def test_build_steps_term_display_names_match_real_lexicon_terms():
    steps = primer.build_steps(REAL_PRIMER["terms"], REAL_LEXICON)
    slug_to_entry = primer.build_slug_to_entry(REAL_LEXICON)
    for step in steps:
        assert step.term == slug_to_entry[step.slug]["term"]


def test_build_steps_raises_key_error_for_an_unresolvable_slug():
    with pytest.raises(KeyError):
        primer.build_steps(["not-a-real-slug"], REAL_LEXICON)


# ---------------------------------------------------------------------------
# build_primer_context against REAL content
# ---------------------------------------------------------------------------


def test_build_primer_context_real_content():
    context = primer.build_primer_context(REAL_PRIMER, REAL_LEXICON)
    assert context["total_steps"] == 10
    assert [s.slug for s in context["steps"]] == EXPECTED_ORDER
    assert context["generated_at"] == REAL_PRIMER["generated_at"]


# ---------------------------------------------------------------------------
# render_primer_page -- HTML output against REAL content
# ---------------------------------------------------------------------------


def test_render_primer_page_has_one_h1_and_main_landmark():
    html = primer.render_primer_page(REAL_PRIMER, REAL_LEXICON)
    assert html.count("<h1") == 1
    assert "Primer" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html


def test_render_primer_page_links_to_every_step_in_order():
    html = primer.render_primer_page(REAL_PRIMER, REAL_LEXICON)
    positions = [html.index(f'href="/lexicon/{slug}/"') for slug in EXPECTED_ORDER]
    assert positions == sorted(positions), "primer steps must appear in the fixed reading order"


def test_render_primer_page_shows_each_terms_real_one_liner():
    html = primer.render_primer_page(REAL_PRIMER, REAL_LEXICON)
    slug_to_entry = primer.build_slug_to_entry(REAL_LEXICON)
    for slug in EXPECTED_ORDER:
        # Autoescaped by Jinja same as any other plain-text field (an
        # apostrophe becomes `&#39;` etc.) -- compare against the escaped
        # form rather than the raw one_liner string.
        assert str(escape(slug_to_entry[slug]["one_liner"])) in html


def test_render_primer_page_numbers_every_step_1_through_10():
    html = primer.render_primer_page(REAL_PRIMER, REAL_LEXICON)
    for n in range(1, 11):
        assert f"Step {n} of 10" in html


def test_render_primer_page_empty_terms_does_not_crash():
    html = primer.render_primer_page({"generated_at": "2026-07-09", "terms": []}, REAL_LEXICON)
    assert html.count("<h1") == 1
    assert "<ol" not in html


# ---------------------------------------------------------------------------
# Full-build integration: every primer slug resolves to a REAL generated
# lexicon page.
# ---------------------------------------------------------------------------


def test_all_10_primer_slugs_resolve_to_real_generated_lexicon_pages(tmp_path):
    env = lexicon.build_jinja_env()
    lexicon.write_lexicon_pages(env, REAL_LEXICON, tmp_path)
    primer.write_primer_page(env, REAL_PRIMER, REAL_LEXICON, tmp_path)

    assert (tmp_path / "primer" / "index.html").is_file()
    for slug in REAL_PRIMER["terms"]:
        page = tmp_path / "lexicon" / slug / "index.html"
        assert page.is_file(), f"primer slug {slug!r} has no generated lexicon page"


def test_primer_page_links_resolve_to_files_written_by_lexicon_builder(tmp_path):
    env = lexicon.build_jinja_env()
    written_lexicon_pages = {p.relative_to(tmp_path) for p in lexicon.write_lexicon_pages(env, REAL_LEXICON, tmp_path)}
    for slug in REAL_PRIMER["terms"]:
        assert Path("lexicon", slug, "index.html") in written_lexicon_pages
