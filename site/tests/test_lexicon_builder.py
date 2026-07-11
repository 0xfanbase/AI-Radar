"""Tests for site/builders/lexicon.py + site/templates/lexicon_index.html
/ site/templates/lexicon_term.html -- the Lexicon index + one page per
term (Phase 4, build-plan section 5).

Exercises the REAL, committed `content/lexicon.json` (30 seeded,
live-cited terms, per Phase 3) throughout: every term gets a generated
page, every `related[]` reference resolves to another generated page,
and the empty `seen_in[]` case (true for all 30 seed terms today -- no
analyst run has happened for real yet) renders an honest message rather
than a broken empty list. A couple of small synthetic fixtures cover the
non-empty `seen_in[]` path and defensive fallbacks that the real seed
content doesn't happen to exercise.

Loaded by explicit file path (matching `site/tests/test_board_builder.py`'s /
`site/tests/test_wire_builder.py`'s own convention), since `site/` is
deliberately not an importable package -- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import html
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from markupsafe import Markup

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEXICON_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "lexicon.py"
LEXICON_CONTENT_PATH = REPO_ROOT / "content" / "lexicon.json"


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module: lexicon.py's dataclasses (combined
    # with `from __future__ import annotations`) need their own module
    # registered under `cls.__module__` for dataclasses' internal
    # annotation resolution to find it -- same requirement documented in
    # site/tests/test_linkify.py / site/tests/test_board_builder.py.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lexicon = _load_module_by_path("frontier_wire_site_builders_lexicon", LEXICON_BUILDER_PATH)


def _load_real_lexicon() -> list[dict]:
    with LEXICON_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


REAL_ENTRIES = _load_real_lexicon()


# ---------------------------------------------------------------------------
# Real content sanity
# ---------------------------------------------------------------------------


def test_real_lexicon_has_exactly_30_entries():
    assert len(REAL_ENTRIES) == 30


def test_all_real_related_references_resolve_to_another_real_term():
    # Sanity check on the fixture this whole suite depends on -- every
    # related[] name must exactly match some entry's own `term` (Phase 3
    # backfill invariant), otherwise the "every related-term reference
    # resolves to a generated page" assertions below would be vacuous.
    terms = {e["term"] for e in REAL_ENTRIES}
    for entry in REAL_ENTRIES:
        for rel in entry["related"]:
            assert rel in terms, f"{entry['term']!r} has unresolvable related term {rel!r}"


def test_all_real_seen_in_are_empty():
    # This build stage's known state -- no analyst run has happened for
    # real yet, so every seeded term's seen_in[] must still be empty. If
    # this ever fails it means real content changed and the "empty
    # seen_in renders a message, not a crash" tests below should gain a
    # non-empty real-content counterpart.
    assert all(entry["seen_in"] == [] for entry in REAL_ENTRIES)


# ---------------------------------------------------------------------------
# slugify / slug map -- reused verbatim from site/lib/linkify.py
# ---------------------------------------------------------------------------


def test_slugify_matches_linkify_convention():
    assert lexicon.slugify("foundation model") == "foundation-model"
    assert lexicon.slugify("RLHF") == "rlhf"
    assert lexicon.slugify("open weights") == "open-weights"


def test_all_slugs_real_content_yields_30_unique_slugs():
    slugs = lexicon.all_slugs(REAL_ENTRIES)
    assert len(slugs) == 30
    assert len(set(slugs)) == 30


def test_all_slugs_match_slugify_of_each_term():
    slug_map = lexicon.build_slug_map(REAL_ENTRIES)
    for entry in REAL_ENTRIES:
        assert slug_map[entry["term"].lower()] == lexicon.slugify(entry["term"])


# ---------------------------------------------------------------------------
# render_deeper_html -- safe rendering of the one trusted inline anchor
# ---------------------------------------------------------------------------


def test_render_deeper_html_returns_markup():
    result = lexicon.render_deeper_html('plain text with <a href="https://x.test/">a link</a> after.')
    assert isinstance(result, Markup)


def test_render_deeper_html_preserves_the_real_anchor_for_every_real_entry():
    for entry in REAL_ENTRIES:
        rendered = lexicon.render_deeper_html(entry["deeper"])
        # Exactly one real, clickable anchor survives (its href untouched;
        # its visible text HTML-escaped the same way any other text node
        # would be, e.g. an apostrophe in the anchor's title becoming
        # `&#x27;` -- correct HTML, not a broken link).
        original_match = re.search(r'<a href="([^"]*)">([^<]*)</a>', entry["deeper"])
        assert original_match is not None, f"{entry['term']!r} has no anchor in its raw deeper field"
        href, text = original_match.group(1), original_match.group(2)
        assert f'<a href="{href}">{html.escape(text)}</a>' in rendered
        assert rendered.count("<a href=") == 1


def test_render_deeper_html_does_not_double_escape_the_anchor():
    # A naive html.escape() over the whole field would turn the anchor's
    # own tag delimiters into &lt;a href=...&gt;, breaking the link --
    # this must never appear in the output.
    for entry in REAL_ENTRIES:
        rendered = lexicon.render_deeper_html(entry["deeper"])
        assert "&lt;a href" not in rendered
        assert "&lt;/a&gt;" not in rendered


def test_render_deeper_html_escapes_stray_markup_outside_the_anchor():
    malicious = (
        'Some prose with a <script>alert(1)</script> injection attempt, '
        'then the real citation <a href="https://example.test/paper">Paper</a> '
        'and a trailing "quoted" & ampersand.'
    )
    rendered = lexicon.render_deeper_html(malicious)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    # The real anchor is still preserved untouched.
    assert '<a href="https://example.test/paper">Paper</a>' in rendered
    # Stray ampersand outside the anchor is escaped, not left raw.
    assert "&amp; ampersand" in rendered


def test_render_deeper_html_with_no_anchor_falls_back_to_escaped_plain_text():
    rendered = lexicon.render_deeper_html("no citation here, just <b>bold</b> text")
    assert "<b>" not in rendered
    assert "&lt;b&gt;bold&lt;/b&gt;" in rendered


# ---------------------------------------------------------------------------
# related[] resolution
# ---------------------------------------------------------------------------


def test_resolve_related_against_real_content_all_slugs_resolve():
    slug_map = lexicon.build_slug_map(REAL_ENTRIES)
    all_real_slugs = set(lexicon.all_slugs(REAL_ENTRIES))
    for entry in REAL_ENTRIES:
        resolved = lexicon.resolve_related(entry["related"], slug_map)
        assert len(resolved) == len(entry["related"])
        for rel in resolved:
            assert rel.slug is not None, f"{entry['term']!r}'s related term {rel.term!r} did not resolve"
            assert rel.slug in all_real_slugs


def test_resolve_related_handles_an_unresolvable_name_gracefully():
    slug_map = lexicon.build_slug_map(REAL_ENTRIES)
    resolved = lexicon.resolve_related(["not a real term"], slug_map)
    assert resolved[0].term == "not a real term"
    assert resolved[0].slug is None


# ---------------------------------------------------------------------------
# seen_in[] resolution -- empty-by-default (real content) + synthetic
# non-empty coverage
# ---------------------------------------------------------------------------


def test_resolve_seen_in_empty_list_is_empty_tuple():
    assert lexicon.resolve_seen_in([]) == ()


def test_seen_in_href_links_into_the_cards_month_with_a_scroll_anchor():
    href = lexicon.seen_in_href("2026-07-09-gpt-5-5-release")
    assert href == "/wire/2026-07/#card-2026-07-09-gpt-5-5-release-headline"


def test_seen_in_href_falls_back_for_a_non_date_prefixed_id():
    assert lexicon.seen_in_href("not-a-date-id") == "/wire/"


def test_resolve_seen_in_synthetic_non_empty_case():
    resolved = lexicon.resolve_seen_in(["2026-07-09-test-card"])
    assert len(resolved) == 1
    assert resolved[0].card_id == "2026-07-09-test-card"
    assert resolved[0].href == "/wire/2026-07/#card-2026-07-09-test-card-headline"
    # No headline_by_id supplied at all (the default) -- label falls back
    # to the bare card id, matching this function's pre-headline-lookup
    # behavior exactly.
    assert resolved[0].label == "2026-07-09-test-card"


def test_resolve_seen_in_with_headline_mapping_uses_the_real_headline_as_label():
    resolved = lexicon.resolve_seen_in(
        ["2026-07-09-test-card"],
        headline_by_id={"2026-07-09-test-card": "Test Lab ships Test Model Alpha"},
    )
    assert resolved[0].label == "Test Lab ships Test Model Alpha"
    # The raw slug never leaks into the label once a headline resolves.
    assert resolved[0].label != resolved[0].card_id


def test_resolve_seen_in_unresolvable_id_falls_back_to_the_card_id_label():
    # A stale seen_in[] reference to a card id absent from the supplied
    # mapping (e.g. a card that no longer exists) must still render
    # *something* usable rather than a blank label -- falls back to the
    # bare id, exactly the no-mapping-supplied behavior above.
    resolved = lexicon.resolve_seen_in(
        ["2026-07-09-unknown-card"],
        headline_by_id={"2026-07-09-other-card": "Some other headline"},
    )
    assert resolved[0].label == "2026-07-09-unknown-card"


# ---------------------------------------------------------------------------
# build_index_context / build_term_context against REAL content
# ---------------------------------------------------------------------------


def test_build_index_context_real_content_has_30_entries_alphabetical():
    context = lexicon.build_index_context(REAL_ENTRIES)
    assert context["total_terms"] == 30
    terms_lower = [row.term.lower() for row in context["entries"]]
    assert terms_lower == sorted(terms_lower)


def test_build_term_context_real_content_every_slug_resolves():
    for slug in lexicon.all_slugs(REAL_ENTRIES):
        context = lexicon.build_term_context(REAL_ENTRIES, slug)
        assert context["entry"].slug == slug
        assert context["empty_seen_in_message"] == lexicon.EMPTY_SEEN_IN_MESSAGE


def test_build_term_context_real_content_seen_in_is_empty_for_every_term():
    for slug in lexicon.all_slugs(REAL_ENTRIES):
        context = lexicon.build_term_context(REAL_ENTRIES, slug)
        assert context["entry"].seen_in == ()


def test_build_term_context_unknown_slug_raises_key_error():
    with pytest.raises(KeyError):
        lexicon.build_term_context(REAL_ENTRIES, "does-not-exist")


# ---------------------------------------------------------------------------
# render_lexicon_index / render_lexicon_term -- HTML output against REAL
# content
# ---------------------------------------------------------------------------


def test_render_lexicon_index_has_one_h1_and_links_to_every_term():
    html = lexicon.render_lexicon_index(REAL_ENTRIES)
    assert html.count("<h1") == 1
    assert "Lexicon" in html
    assert 'id="main-content"' in html
    for slug in lexicon.all_slugs(REAL_ENTRIES):
        assert f'href="/lexicon/{slug}/"' in html


def test_render_lexicon_index_lists_terms_in_alphabetical_order():
    html = lexicon.render_lexicon_index(REAL_ENTRIES)
    slugs_in_order = lexicon.all_slugs(REAL_ENTRIES)
    positions = [html.index(f'href="/lexicon/{slug}/"') for slug in slugs_in_order]
    assert positions == sorted(positions)


def test_render_lexicon_term_real_content_has_one_h1_matching_the_term():
    for entry in REAL_ENTRIES:
        slug = lexicon.slugify(entry["term"])
        html = lexicon.render_lexicon_term(REAL_ENTRIES, slug)
        assert html.count("<h1") == 1
        assert f"<h1>{entry['term']}</h1>" in html
        assert 'id="main-content"' in html


def test_render_lexicon_term_real_content_empty_seen_in_shows_message_not_empty_list():
    for entry in REAL_ENTRIES:
        slug = lexicon.slugify(entry["term"])
        html = lexicon.render_lexicon_term(REAL_ENTRIES, slug)
        assert lexicon.EMPTY_SEEN_IN_MESSAGE in html
        # No empty <ul></ul> rendered for the seen_in section.
        assert "<ul" not in html.split('id="lexicon-term-seen-in-heading"')[1].split("</section>")[0]


def test_render_lexicon_term_real_content_related_terms_link_to_real_pages():
    all_real_slugs = set(lexicon.all_slugs(REAL_ENTRIES))
    for entry in REAL_ENTRIES:
        slug = lexicon.slugify(entry["term"])
        html = lexicon.render_lexicon_term(REAL_ENTRIES, slug)
        for rel in entry["related"]:
            rel_slug = lexicon.slugify(rel)
            assert rel_slug in all_real_slugs
            assert f'href="/lexicon/{rel_slug}/"' in html


def test_render_lexicon_term_unresolvable_related_term_renders_as_inert_chip():
    # T8: a related-term chip with no matching lexicon entry must be
    # visually (and accessibly) distinguishable from a clickable one --
    # both would otherwise render inside the identical `.chip` pill.
    synthetic = [
        {
            "term": "widget",
            "one_liner": "A widget is a test term.",
            "deeper": "More about widgets.",
            "related": ["not a real term"],
            "seen_in": [],
        }
    ]
    html = lexicon.render_lexicon_term(synthetic, "widget")
    assert (
        '<span class="chip chip--inert" title="Not yet defined in the Lexicon">'
        "not a real term</span>" in html
    )


def test_render_lexicon_term_real_content_anchor_citation_is_a_real_link():
    for entry in REAL_ENTRIES:
        slug = lexicon.slugify(entry["term"])
        html = lexicon.render_lexicon_term(REAL_ENTRIES, slug)
        original_match = re.search(r'<a href="([^"]*)">', entry["deeper"])
        assert original_match is not None
        assert f'<a href="{original_match.group(1)}">' in html


def test_render_lexicon_term_with_synthetic_non_empty_seen_in_renders_links():
    synthetic = [
        {
            "term": "widget",
            "one_liner": "A widget is a test term.",
            "deeper": 'More about widgets, see <a href="https://example.test/widget">the widget paper</a>.',
            "related": [],
            "seen_in": ["2026-07-09-widget-card"],
        }
    ]
    html = lexicon.render_lexicon_term(synthetic, "widget")
    assert 'href="/wire/2026-07/#card-2026-07-09-widget-card-headline"' in html
    # No `cards=` supplied -- link text falls back to the raw card id.
    assert "2026-07-09-widget-card" in html
    assert lexicon.EMPTY_SEEN_IN_MESSAGE not in html


def test_render_lexicon_term_seen_in_resolves_the_cards_real_headline_as_link_text():
    synthetic = [
        {
            "term": "widget",
            "one_liner": "A widget is a test term.",
            "deeper": 'More about widgets, see <a href="https://example.test/widget">the widget paper</a>.',
            "related": [],
            "seen_in": ["2026-07-09-widget-card"],
        }
    ]
    cards = [{"id": "2026-07-09-widget-card", "headline": "Widget Labs ships Widget 2.0"}]
    html = lexicon.render_lexicon_term(synthetic, "widget", cards=cards)
    assert (
        '<a href="/wire/2026-07/#card-2026-07-09-widget-card-headline">'
        "Widget Labs ships Widget 2.0</a>" in html
    )
    # The raw machine slug is never shown as the visible link text once a
    # matching headline is available.
    assert ">2026-07-09-widget-card<" not in html


def test_render_lexicon_term_seen_in_falls_back_to_the_card_id_when_unresolvable():
    synthetic = [
        {
            "term": "widget",
            "one_liner": "A widget is a test term.",
            "deeper": 'More about widgets, see <a href="https://example.test/widget">the widget paper</a>.',
            "related": [],
            "seen_in": ["2026-07-09-widget-card"],
        }
    ]
    # `cards=` supplied but with no matching id -- a stale seen_in[]
    # reference -- still renders a usable link, labeled with the bare id.
    cards = [{"id": "2026-07-09-some-other-card", "headline": "Unrelated headline"}]
    html = lexicon.render_lexicon_term(synthetic, "widget", cards=cards)
    assert (
        '<a href="/wire/2026-07/#card-2026-07-09-widget-card-headline">'
        "2026-07-09-widget-card</a>" in html
    )


# ---------------------------------------------------------------------------
# write_lexicon_pages -- full-build integration against REAL content
# ---------------------------------------------------------------------------


def test_write_lexicon_pages_real_content_generates_all_30_term_pages(tmp_path):
    env = lexicon.build_jinja_env()
    written = lexicon.write_lexicon_pages(env, REAL_ENTRIES, tmp_path)
    assert len(written) == 31  # index + 30 term pages
    assert (tmp_path / "lexicon" / "index.html").is_file()
    for slug in lexicon.all_slugs(REAL_ENTRIES):
        assert (tmp_path / "lexicon" / slug / "index.html").is_file()


def test_write_lexicon_pages_every_related_reference_resolves_to_a_generated_page(tmp_path):
    env = lexicon.build_jinja_env()
    lexicon.write_lexicon_pages(env, REAL_ENTRIES, tmp_path)
    for entry in REAL_ENTRIES:
        for rel in entry["related"]:
            rel_slug = lexicon.slugify(rel)
            page = tmp_path / "lexicon" / rel_slug / "index.html"
            assert page.is_file(), (
                f"{entry['term']!r}'s related term {rel!r} (slug {rel_slug!r}) "
                "has no generated page"
            )


def test_write_lexicon_pages_threads_cards_through_to_seen_in_headline_labels(tmp_path):
    synthetic_entries = [
        {
            "term": "widget",
            "one_liner": "A widget is a test term.",
            "deeper": 'More about widgets, see <a href="https://example.test/widget">the widget paper</a>.',
            "related": [],
            "seen_in": ["2026-07-09-widget-card"],
        }
    ]
    cards = [{"id": "2026-07-09-widget-card", "headline": "Widget Labs ships Widget 2.0"}]
    env = lexicon.build_jinja_env()
    lexicon.write_lexicon_pages(env, synthetic_entries, tmp_path, cards=cards)
    page = tmp_path / "lexicon" / "widget" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert "Widget Labs ships Widget 2.0</a>" in html
    assert ">2026-07-09-widget-card<" not in html
