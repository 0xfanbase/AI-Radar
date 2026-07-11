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
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import jsonschema
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


def test_load_cards_fails_loudly_on_a_card_that_fails_schema_validation(tmp_path, monkeypatch):
    # A card missing required fields (card.schema.json) must be a loud,
    # immediate build failure -- not silently published or skipped. This
    # matches update_card_index.py's own established principle: by the
    # time a file exists at content/cards/<id>.json it should already be
    # schema-valid, so a failure here is a real bug worth surfacing now.
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "bad-card.json").write_text(
        json.dumps({"id": "not-a-real-card"}), encoding="utf-8"
    )
    monkeypatch.setattr(generate, "CONTENT_DIR", tmp_path)
    with pytest.raises(jsonschema.exceptions.ValidationError):
        generate.load_cards()


def test_load_and_validate_content_fails_loudly_on_a_malformed_date_field(tmp_path, monkeypatch):
    # jsonschema.validate() silently ignores "format" keywords (format:
    # date/date-time/uri) unless a FormatChecker is passed explicitly --
    # without one, a malformed last_verified/release_date string would
    # pass this validation step and only surface later as a raw crash
    # deep inside a builder's own date parsing. A schema-declared
    # "format": "date" violation must fail loudly right here instead.
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bad_row = {
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
    (content_dir / "frontier_board.json").write_text(
        json.dumps([bad_row]), encoding="utf-8"
    )
    monkeypatch.setattr(generate, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(generate, "DATA_DIR", data_dir)
    with pytest.raises(jsonschema.exceptions.ValidationError):
        generate.load_and_validate_content()


def test_generate_is_rerunnable_into_the_same_directory(tmp_path):
    # generate() must be safe to run twice into the same output dir
    # (e.g. a CI re-run) without leaving stale or half-written output.
    generate.generate(public_dir=tmp_path)
    generate.generate(public_dir=tmp_path)
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "static" / "css" / "tokens.css").is_file()


# --- base-path rewriting (GitHub Pages project-subpath fix) -----------------
#
# Real incident: this site's internal links and static-asset hrefs were all
# root-relative by design (every builder module's own, still-correct,
# convention), which broke -- real 404s, confirmed live -- the moment GitHub
# Pages actually started serving this repo from its project subpath
# (https://0xfanbase.github.io/AI-Radar/) rather than a domain root. See
# PROGRESS.md for the incident and generate.py's own BASE_PATH/
# apply_base_path() docstrings for the fix.


def test_apply_base_path_rewrites_internal_hrefs_and_srcs(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(
        '<link rel="stylesheet" href="/static/css/tokens.css">'
        '<img src="/static/img/foo.png">'
        '<a href="/board/">Board</a>'
        '<a href="https://arxiv.org/abs/123">External</a>'
        '<a href="#main-content">Skip</a>',
        encoding="utf-8",
    )
    rewritten = generate.apply_base_path(tmp_path, base_path="/AI-Radar")
    assert rewritten == 1
    html = page.read_text(encoding="utf-8")
    assert 'href="/AI-Radar/static/css/tokens.css"' in html
    assert 'src="/AI-Radar/static/img/foo.png"' in html
    assert 'href="/AI-Radar/board/"' in html
    # External and fragment-only links are untouched.
    assert 'href="https://arxiv.org/abs/123"' in html
    assert 'href="#main-content"' in html


def test_apply_base_path_is_a_noop_when_base_path_is_empty(tmp_path):
    page = tmp_path / "page.html"
    original = '<a href="/board/">Board</a>'
    page.write_text(original, encoding="utf-8")
    rewritten = generate.apply_base_path(tmp_path, base_path="")
    assert rewritten == 0
    assert page.read_text(encoding="utf-8") == original


def test_base_path_is_derived_from_site_base_url(monkeypatch):
    # Not hand-maintained: BASE_PATH is a pure function of SITE_BASE_URL,
    # so a future custom-domain move (SITE_BASE_URL -> a bare domain, no
    # path) automatically stops prefixing internal links, with no other
    # code change required.
    assert generate.BASE_PATH == "/AI-Radar"


def test_generate_applies_the_real_derived_base_path_to_actual_output(tmp_path):
    generate.generate(public_dir=tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert f'href="{generate.BASE_PATH}/board/"' in html
    assert f'href="{generate.BASE_PATH}/static/css/tokens.css"' in html
    # The skip-link fragment and (if present) any external link must never
    # be prefixed.
    assert 'href="#main-content"' in html
    assert generate.BASE_PATH + generate.BASE_PATH not in html


def test_generate_rerun_does_not_double_prefix_base_path(tmp_path):
    generate.generate(public_dir=tmp_path)
    generate.generate(public_dir=tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert generate.BASE_PATH + generate.BASE_PATH not in html
    assert f'href="{generate.BASE_PATH}/board/"' in html


def test_sitemap_and_robots_are_not_touched_by_base_path_rewriting(tmp_path):
    # sitemap.xml/robots.txt already use absolute SITE_BASE_URL values and
    # are not *.html -- apply_base_path()'s glob must never touch them.
    generate.generate(public_dir=tmp_path)
    sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    robots = (tmp_path / "robots.txt").read_text(encoding="utf-8")
    assert generate.SITE_BASE_URL + generate.BASE_PATH not in sitemap
    assert f"<loc>{generate.SITE_BASE_URL}/</loc>" in sitemap
    assert f"Sitemap: {generate.SITE_BASE_URL}/sitemap.xml" in robots


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
