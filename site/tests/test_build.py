"""Build + accessibility tests for site/generate.py -- Phase 4 integration.

This build stage wires every Phase 4 page builder
(`site/builders/{wire,board,lexicon,primer,moving,method,corrections,
about}.py`) together in `site/generate.py`'s own `render_pages()`. This
file now covers, in addition to the original scaffold-stage smoke tests:

* every named route in the approved build plan's route table actually
  gets written under `public/` (home, Board, Lexicon index + one page per
  real term, Primer, What's Moving, Method, Corrections, About, 404,
  sitemap.xml, robots.txt);
* the accessibility pass this integration commit performs across the
  *whole* generated output: exactly one `<h1>` per HTML page, one `<main
  id="main-content">` landmark per page, and the skip-link as the first
  focusable element in `<body>` on every page (not just base.html's own
  scaffold-stage placeholder);
* `public/404.html`, `public/sitemap.xml`, and `public/robots.txt` are
  real, well-formed files reachable the way GitHub Pages expects; and
* the site-wide masthead sparkline strip (build plan section 5's "+ a
  thin masthead sparkline strip site-wide") actually renders on every
  page, not just `/moving/` itself -- see this commit's own
  `IMPROVEMENT_BACKLOG.md` entry for the `env.globals` mechanism that
  makes this hold without touching any of the seven other already-tested
  builder modules.

All of this runs against this repo's *real* `content/*.json` and
`data/*.json` (not fixtures), per this file's own established
scaffold-stage precedent -- there is no analyst run yet, so `cards ==
[]` throughout, and every assertion below tolerates that (no `/wire/
<YYYY-MM>/` page is asserted to exist, since none should).
"""
from __future__ import annotations

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SITE_DIR = Path(__file__).resolve().parent.parent


def _load_generate_module():
    """Load site/generate.py by explicit file path rather than
    `import generate` off a sys.path insert, and specifically NOT as
    `import site` -- `site` is also a Python stdlib module name, so
    treating this directory as an importable package under that name
    risks shadowing it. Loading by path sidesteps the collision entirely.
    See IMPROVEMENT_BACKLOG.md."""
    spec = importlib.util.spec_from_file_location(
        "frontier_wire_site_generate", SITE_DIR / "generate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = _load_generate_module()


def test_generate_runs_against_real_repo_content_without_crashing(tmp_path):
    out_dir = generate.generate(public_dir=tmp_path)
    assert out_dir == tmp_path


def test_generate_produces_index_html(tmp_path):
    generate.generate(public_dir=tmp_path)
    index = tmp_path / "index.html"
    assert index.is_file()
    html = index.read_text(encoding="utf-8")
    assert "<html" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html
    assert "<h1" in html


def test_generate_produces_tokens_css(tmp_path):
    generate.generate(public_dir=tmp_path)
    tokens = tmp_path / "static" / "css" / "tokens.css"
    assert tokens.is_file()
    css = tokens.read_text(encoding="utf-8")
    assert "--color-bg: #0B0E17" in css
    assert "--color-signal-cyan: #43E5C4" in css


def test_generate_produces_components_css(tmp_path):
    generate.generate(public_dir=tmp_path)
    components = tmp_path / "static" / "css" / "components.css"
    assert components.is_file()


def test_load_cards_handles_empty_cards_dir_gracefully():
    # content/cards/ has no real analyst output yet -- must not crash, and
    # must return an empty list rather than None or raise.
    cards = generate.load_cards()
    assert cards == []


def test_generate_is_rerunnable_into_the_same_directory(tmp_path):
    # generate() must be safe to run twice into the same output dir
    # (e.g. a CI re-run) without leaving stale or half-written output.
    generate.generate(public_dir=tmp_path)
    generate.generate(public_dir=tmp_path)
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "static" / "css" / "tokens.css").is_file()


# --- Integration: every named route exists ---------------------------------
#
# One shared build per test session (a module-scoped fixture, not
# per-test) -- generate() against this repo's real content/data is a
# real, if fast, jsonschema-validating + Jinja-rendering build; running it
# once and asserting against the same output directory for every
# assertion below keeps this file's own runtime in line with the rest of
# the (fast) suite while still exercising the real pipeline end to end.


@pytest.fixture(scope="module")
def built_site(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("public_integration")
    generate.generate(public_dir=out_dir)
    return out_dir


def _all_html_files(public_dir: Path) -> list[Path]:
    return sorted(public_dir.rglob("*.html"))


def test_every_named_route_in_the_build_plan_is_written(built_site):
    # content/cards/ is empty in this environment (no analyst run has
    # happened for real yet), so no /wire/<YYYY-MM>/ archive page is
    # expected -- every other route in the build plan's table is,
    # regardless.
    expected = [
        "index.html",
        "board/index.html",
        "lexicon/index.html",
        "primer/index.html",
        "moving/index.html",
        "method/index.html",
        "corrections/index.html",
        "about/index.html",
        "404.html",
        "sitemap.xml",
        "robots.txt",
    ]
    for rel in expected:
        assert (built_site / rel).is_file(), f"missing expected route: {rel}"


def test_every_real_lexicon_term_gets_its_own_page(built_site):
    import json

    lexicon_entries = json.loads(
        (generate.CONTENT_DIR / "lexicon.json").read_text(encoding="utf-8")
    )
    assert len(lexicon_entries) == 30  # Phase 3's own seeded acceptance bar
    for entry in lexicon_entries:
        slug = entry["term"].lower().replace(" ", "-")
        page = built_site / "lexicon" / slug / "index.html"
        assert page.is_file(), f"lexicon term {entry['term']!r} has no generated page"


# --- Accessibility pass: every generated page ------------------------------


def test_every_generated_html_page_has_exactly_one_h1(built_site):
    pages = _all_html_files(built_site)
    assert pages, "expected at least one generated HTML page"
    for page in pages:
        html = page.read_text(encoding="utf-8")
        h1_count = len(re.findall(r"<h1[ >]", html))
        assert h1_count == 1, f"{page.relative_to(built_site)} has {h1_count} <h1> elements, expected exactly 1"


def test_every_generated_html_page_has_one_main_content_landmark(built_site):
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        main_count = len(re.findall(r'<main\b[^>]*\bid="main-content"', html))
        assert main_count == 1, f"{page.relative_to(built_site)} has {main_count} <main id=\"main-content\"> landmarks, expected exactly 1"


def test_skip_link_is_first_focusable_element_on_every_page(built_site):
    focusable = re.compile(r"<(a|button|input|select|textarea)\b", re.IGNORECASE)
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        body = html.split("<body>", 1)[1]
        match = focusable.search(body)
        assert match is not None, f"{page.relative_to(built_site)} has no focusable element at all"
        snippet = body[match.start() : match.start() + 60]
        assert 'class="skip-link"' in snippet, (
            f"{page.relative_to(built_site)}'s first focusable element isn't the skip-link: {snippet!r}"
        )


def test_masthead_sparkline_strip_renders_site_wide(built_site):
    # Build-plan section 5: "+ a thin masthead sparkline strip
    # site-wide" -- every generated page, not just /moving/ itself,
    # should include the masthead strip partial.
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        assert "masthead-strip" in html, (
            f"{page.relative_to(built_site)} is missing the site-wide masthead sparkline strip"
        )


# --- 404 / sitemap.xml / robots.txt ----------------------------------------


def test_404_page_uses_the_shared_shell_and_is_a_real_not_found_page(built_site):
    html = (built_site / "404.html").read_text(encoding="utf-8")
    assert "Skip to content" in html
    assert 'id="main-content"' in html
    assert "<h1>Page not found</h1>" in html


def test_sitemap_xml_is_well_formed_and_lists_expected_routes(built_site):
    sitemap_path = built_site / "sitemap.xml"
    tree = ET.parse(sitemap_path)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in tree.getroot().findall("sm:url/sm:loc", ns)]
    assert len(locs) > 30  # home + board + lexicon index + 30 term pages + ...
    assert any(loc.endswith("/board/") for loc in locs)
    assert any(loc.endswith("/lexicon/") for loc in locs)
    assert any(loc.endswith("/primer/") for loc in locs)
    # every entry must be an absolute URL (a real <loc> requirement of the
    # sitemap protocol, not just "looks like a path")
    assert all(loc.startswith("https://") for loc in locs)


def test_robots_txt_allows_everything_and_references_sitemap(built_site):
    robots = (built_site / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert "Sitemap: https://" in robots
    assert robots.rstrip("\n").endswith("sitemap.xml")
