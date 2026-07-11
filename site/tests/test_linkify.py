"""Tests for site/lib/linkify.py -- lexicon auto-linking (Phase 4).

content/cards/ is empty (no analyst run has happened for real yet), so
these tests exercise linkify.py against small synthetic prose fixtures
built in this file, per this turn's explicit scope -- real integration
against analyst-written card prose happens once the Wire builder runs
later.

Loaded by explicit file path (matching site/tests/test_build.py's own
convention for site/generate.py) rather than an `import site.lib.linkify`
package import, since `site` is also a stdlib module name and this
directory is deliberately not turned into a package -- see
IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from markupsafe import Markup

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LINKIFY_PATH = REPO_ROOT / "site" / "lib" / "linkify.py"


def _load_linkify_module():
    spec = importlib.util.spec_from_file_location(
        "frontier_wire_site_lib_linkify", LINKIFY_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules *before* exec_module: linkify.py's
    # dataclass (combined with `from __future__ import annotations`)
    # needs its own module registered under `cls.__module__` for
    # dataclasses' internal annotation resolution to find it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


linkify_mod = _load_linkify_module()

SYNTHETIC_LEXICON = [
    {"term": "transformer", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []},
    {"term": "RLHF", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []},
    {"term": "context window", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []},
    {"term": "open weights", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []},
]


# ---------------------------------------------------------------------------
# slugify / build_slug_map
# ---------------------------------------------------------------------------


def test_slugify_lowercases_and_hyphenates_spaces():
    assert linkify_mod.slugify("context window") == "context-window"
    assert linkify_mod.slugify("RLHF") == "rlhf"
    assert linkify_mod.slugify("open weights") == "open-weights"


def test_slugify_matches_test_seed_content_convention_for_all_real_lexicon_terms():
    # content/lexicon.json is real seed content (Phase 3) -- confirm this
    # module's slugifier resolves every real term the same way
    # tests/test_seed_content.py's own local _slugify helper does, so
    # primer.json's already-seeded slugs keep resolving.
    import json

    lexicon = json.loads((REPO_ROOT / "content" / "lexicon.json").read_text())
    primer = json.loads((REPO_ROOT / "content" / "primer.json").read_text())
    slug_map = linkify_mod.build_slug_map(lexicon)
    slugs = set(slug_map.values())
    for slug in primer["terms"]:
        assert slug in slugs, f"primer slug {slug!r} not resolvable via linkify.build_slug_map"


def test_build_slug_map_keys_are_lowercased_terms():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    assert slug_map == {
        "transformer": "transformer",
        "rlhf": "rlhf",
        "context window": "context-window",
        "open weights": "open-weights",
    }


# ---------------------------------------------------------------------------
# linkify() -- exact-match linking
# ---------------------------------------------------------------------------


def test_exact_match_splices_anchor_around_first_occurrence():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "The new model relies on a transformer architecture."
    result = linkify_mod.linkify(prose, ["transformer"], slug_map)
    assert result.unmatched_terms == []
    assert '<a href="/lexicon/transformer/">transformer</a>' in str(result.html)
    # surrounding prose is untouched other than the splice
    assert str(result.html) == (
        'The new model relies on a <a href="/lexicon/transformer/">transformer</a> architecture.'
    )


def test_only_first_occurrence_is_linked():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "A transformer is a transformer-based architecture."
    result = linkify_mod.linkify(prose, ["transformer"], slug_map)
    assert result.unmatched_terms == []
    html_str = str(result.html)
    assert html_str.count("<a ") == 1
    assert html_str.startswith('A <a href="/lexicon/transformer/">transformer</a> is a transformer-based')


def test_multi_word_term_is_linked():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "The context window grew this quarter."
    result = linkify_mod.linkify(prose, ["context window"], slug_map)
    assert result.unmatched_terms == []
    assert '<a href="/lexicon/context-window/">context window</a>' in str(result.html)


def test_multiple_terms_each_get_their_own_anchor():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "The transformer was fine-tuned using RLHF before release."
    result = linkify_mod.linkify(prose, ["transformer", "RLHF"], slug_map)
    assert result.unmatched_terms == []
    html_str = str(result.html)
    assert '<a href="/lexicon/transformer/">transformer</a>' in html_str
    assert '<a href="/lexicon/rlhf/">RLHF</a>' in html_str


# ---------------------------------------------------------------------------
# linkify() -- case-insensitive matching
# ---------------------------------------------------------------------------


def test_case_insensitive_match_preserves_casing_found_in_prose():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "Engineers ran rlhf on the base model."
    result = linkify_mod.linkify(prose, ["RLHF"], slug_map)
    assert result.unmatched_terms == []
    # the anchor text preserves the lowercase spelling as it appeared in
    # the prose, not the canonical lexicon-entry casing "RLHF".
    assert '<a href="/lexicon/rlhf/">rlhf</a>' in str(result.html)


def test_case_insensitive_match_uppercase_term_lowercase_lexicon_entry():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "The lab released the weights as OPEN WEIGHTS for research use."
    result = linkify_mod.linkify(prose, ["open weights"], slug_map)
    assert result.unmatched_terms == []
    assert '<a href="/lexicon/open-weights/">OPEN WEIGHTS</a>' in str(result.html)


def test_word_boundary_does_not_match_inside_a_longer_word():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    # "RLHF" must not match inside a longer token like "RLHFX" --
    # word-boundary matching means it only links the later, standalone
    # occurrence of the term.
    prose = "The team used a custom RLHFX variant, unrelated to RLHF proper."
    result = linkify_mod.linkify(prose, ["RLHF"], slug_map)
    assert result.unmatched_terms == []
    html_str = str(result.html)
    # "RLHFX" itself is never wrapped in an anchor...
    assert "RLHFX" in html_str
    assert "<a href=\"/lexicon/rlhf/\">RLHFX" not in html_str
    # ...only the later, standalone "RLHF" is.
    assert '<a href="/lexicon/rlhf/">RLHF</a> proper' in html_str


# ---------------------------------------------------------------------------
# linkify() -- unmatched-term fallback-reporting path
# ---------------------------------------------------------------------------


def test_term_not_present_in_prose_is_reported_unmatched_not_dropped_or_raised():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "This story has nothing to do with any glossary term."
    result = linkify_mod.linkify(prose, ["transformer"], slug_map)
    assert result.unmatched_terms == ["transformer"]
    assert "<a " not in str(result.html)
    # the rest of the prose still renders, escaped and intact
    assert "This story has nothing to do with any glossary term." in str(result.html)


def test_term_not_in_lexicon_at_all_is_reported_unmatched_not_a_crash():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "This uses a made-up glossary term that does not exist."
    result = linkify_mod.linkify(prose, ["totally-not-a-real-term"], slug_map)
    assert result.unmatched_terms == ["totally-not-a-real-term"]
    assert "<a " not in str(result.html)


def test_mixed_matched_and_unmatched_terms_in_one_call():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "The transformer architecture powers this release."
    result = linkify_mod.linkify(prose, ["transformer", "RLHF", "context window"], slug_map)
    assert result.unmatched_terms == ["RLHF", "context window"]
    assert '<a href="/lexicon/transformer/">transformer</a>' in str(result.html)


def test_no_lexicon_terms_at_all_returns_empty_unmatched_and_untouched_prose():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "Plain prose with no lexicon terms referenced."
    result = linkify_mod.linkify(prose, [], slug_map)
    assert result.unmatched_terms == []
    assert str(result.html) == prose


def test_overlapping_terms_do_not_produce_nested_anchors():
    # "open weights" and a hypothetical shorter overlapping term should
    # never produce nested/overlapping <a> tags -- the earlier-listed
    # term in lexicon_terms claims the span; the later one either finds
    # its own separate occurrence or is reported unmatched.
    slug_map = linkify_mod.build_slug_map(
        SYNTHETIC_LEXICON + [{"term": "weights", "one_liner": "...", "deeper": "...", "related": [], "seen_in": []}]
    )
    prose = "The model shipped as open weights for anyone to download."
    result = linkify_mod.linkify(prose, ["open weights", "weights"], slug_map)
    html_str = str(result.html)
    # "open weights" (listed first) claims the only occurrence of
    # "weights" in the prose, so "weights" (listed second) has nothing
    # left to claim non-overlapping and is reported unmatched rather
    # than producing a nested/overlapping anchor.
    assert html_str.count("<a ") == 1
    assert '<a href="/lexicon/open-weights/">open weights</a>' in html_str
    assert result.unmatched_terms == ["weights"]
    # no nested anchor tag inside the one anchor's own contents.
    inner = html_str.split("<a ", 1)[1].split(">", 1)[1].split("</a>")[0]
    assert "<a" not in inner


# ---------------------------------------------------------------------------
# HTML-escaping + Markup safety
# ---------------------------------------------------------------------------


def test_prose_special_characters_are_html_escaped():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "Rankings: 'Model A' beats <Model B> & others, per the paper."
    result = linkify_mod.linkify(prose, [], slug_map)
    html_str = str(result.html)
    assert "<Model B>" not in html_str
    assert "&lt;Model B&gt;" in html_str
    assert "&amp;" in html_str


def test_escaping_happens_around_a_linked_term_too():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    prose = "A & B compared a transformer <model> design."
    result = linkify_mod.linkify(prose, ["transformer"], slug_map)
    html_str = str(result.html)
    assert "&amp;" in html_str
    assert "&lt;model&gt;" in html_str
    assert '<a href="/lexicon/transformer/">transformer</a>' in html_str


def test_anchor_href_escapes_a_slug_containing_html_special_characters():
    # `slug_map` values are derived from lexicon `term` strings, which
    # schemas/lexicon.schema.json does not character-restrict -- a term
    # containing a `"` must not be able to break out of the anchor's
    # `href="..."` attribute and inject markup/attributes of its own.
    # Ends in a word character deliberately -- linkify()'s own matching is
    # `\b`-bounded, so a term ending in a non-word character (e.g. a
    # trailing `)`) would never match at all regardless of escaping.
    hostile_term = 'evil" onmouseover="bad'
    slug_map = linkify_mod.build_slug_map(
        SYNTHETIC_LEXICON + [{"term": hostile_term, "one_liner": "...", "deeper": "...", "related": [], "seen_in": []}]
    )
    result = linkify_mod.linkify(f"A {hostile_term} appears here.", [hostile_term], slug_map)
    html_str = str(result.html)
    # slugify() only lowercases + hyphenates spaces -- it does not strip
    # other HTML-special characters, so the `"` reaches the anchor
    # construction and must be escaped there.
    assert '<a href="/lexicon/evil&quot;-onmouseover=&quot;bad/">' in html_str
    # The href attribute's own value must contain no literal `"` -- one
    # would close the attribute early and turn `onmouseover=...` into a
    # second, real, executable attribute rather than inert text.
    href_value = html_str.split('href="', 1)[1].split('">', 1)[0]
    assert '"' not in href_value


def test_result_html_is_markup_safe_instance():
    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    result = linkify_mod.linkify("A transformer story.", ["transformer"], slug_map)
    assert isinstance(result.html, Markup)


def test_result_html_survives_jinja_autoescape_unescaped():
    from jinja2 import Environment, select_autoescape

    slug_map = linkify_mod.build_slug_map(SYNTHETIC_LEXICON)
    result = linkify_mod.linkify(
        "A transformer & friends.", ["transformer"], slug_map
    )
    env = Environment(autoescape=select_autoescape(["html"]))
    template = env.from_string("{{ body }}")
    rendered = template.render(body=result.html)
    # autoescape must NOT re-escape the anchor tag or the ampersand this
    # module already escaped, precisely because linkify() marked its
    # output Markup-safe.
    assert '<a href="/lexicon/transformer/">transformer</a>' in rendered
    assert "&amp;" in rendered
    assert "&amp;amp;" not in rendered
