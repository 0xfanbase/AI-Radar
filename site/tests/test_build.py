"""Build + accessibility tests for site/generate.py -- Phase 4 integration.

This build stage wires every Phase 4 page builder
(`site/builders/{wire,board,lexicon,primer,moving,method,corrections,
about}.py`) together in `site/generate.py`'s own `render_pages()`. This
file now covers, in addition to the original scaffold-stage smoke tests:

* every named route in the route table actually gets written under
  `public/` (map homepage, Wire index, Board, Lexicon index + one page
  per real term, Primer, What's Moving, Method, Corrections, About,
  404, sitemap.xml, robots.txt);
* the accessibility pass this integration commit performs across the
  *whole* generated output: exactly one `<h1>` per HTML page, one `<main
  id="main-content">` landmark per page, and the skip-link as the first
  focusable element in `<body>` on every page (not just base.html's own
  scaffold-stage placeholder);
* `public/404.html`, `public/sitemap.xml`, and `public/robots.txt` are
  real, well-formed files reachable the way GitHub Pages expects; and
* the masthead sparkline strip is scoped to exactly one page (the
  nav-condense pass narrowed this from an earlier "site-wide" design --
  see `IMPROVEMENT_BACKLOG.md`): present on `public/index.html`, absent
  from every other generated page. Phase 7 moved that page's own
  *content* from the Wire index to the new world-map homepage (the Wire
  index itself moved to `public/wire/index.html`) without changing
  which physical file carries the strip -- see PROGRESS.md's Phase 7
  entry.

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
    assert "--color-bg: #000000" in css
    assert "--color-signal-green: #39FF6E" in css


def test_generate_produces_components_css(tmp_path):
    generate.generate(public_dir=tmp_path)
    components = tmp_path / "static" / "css" / "components.css"
    assert components.is_file()


# --- Matrix-theme digital-rain layer (T3): generate.py wiring --------------
#
# site/generate.py::read_color_token() is the single sanctioned way to pull
# a token's real hex value out of tokens.css into Python, for exactly the
# reason site/lib/svg_sparkline.py's hardcoded SIGNAL_GREEN constant is
# logged as a duplicate to avoid repeating (IMPROVEMENT_BACKLOG.md). These
# tests exercise that function directly, plus write_matrix_tiles_css()'s own
# output shape, before the broader page-integration checks further below.


def test_read_color_token_reads_the_real_signal_green_value():
    value = generate.read_color_token("signal-green")
    assert value == "#39FF6E"
    # Cross-check against the same regex convention
    # site/tests/test_contrast_ratios.py independently uses, so this test
    # doesn't just re-assert a hardcoded literal against itself.
    css = (generate.STATIC_DIR / "css" / "tokens.css").read_text(encoding="utf-8")
    assert f"--color-signal-green: {value}" in css


def test_read_color_token_raises_loudly_on_a_missing_token(tmp_path):
    fake_tokens = tmp_path / "tokens.css"
    fake_tokens.write_text(":root { --color-bg: #000000; }", encoding="utf-8")
    with pytest.raises(ValueError):
        generate.read_color_token("signal-green", tokens_css_path=fake_tokens)


def test_write_matrix_tiles_css_writes_one_custom_property_per_unique_tile(tmp_path):
    tiles = ["data:image/svg+xml,%3Csvg%3E1%3C%2Fsvg%3E", "data:image/svg+xml,%3Csvg%3E2%3C%2Fsvg%3E"]
    path = generate.write_matrix_tiles_css(tiles, tmp_path)
    assert path == tmp_path / "static" / "css" / "matrix-tiles.css"
    css = path.read_text(encoding="utf-8")
    assert ".matrix-rain {" in css
    assert f'--rain-tile-0: url("{tiles[0]}");' in css
    assert f'--rain-tile-1: url("{tiles[1]}");' in css
    assert "do not hand-edit" in css


# --- Matrix-theme digital-rain layer (T3): whole-site integration ----------


def test_matrix_css_and_matrix_tiles_css_are_copied_to_every_build(tmp_path):
    generate.generate(public_dir=tmp_path)
    assert (tmp_path / "static" / "css" / "matrix.css").is_file()
    assert (tmp_path / "static" / "css" / "matrix-tiles.css").is_file()


def test_matrix_tiles_css_contains_the_live_signal_green_hex_percent_encoded(tmp_path):
    generate.generate(public_dir=tmp_path)
    css = (tmp_path / "static" / "css" / "matrix-tiles.css").read_text(encoding="utf-8")
    color = generate.read_color_token("signal-green")
    encoded_hex = "%23" + color.lstrip("#")
    assert encoded_hex in css
    assert "data:image/svg+xml" in css


def test_matrix_css_has_no_hardcoded_hex_colors(tmp_path):
    generate.generate(public_dir=tmp_path)
    css = (tmp_path / "static" / "css" / "matrix.css").read_text(encoding="utf-8")
    hex_literals = re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)
    assert hex_literals == [], (
        f"matrix.css must contain zero color values (glyph color lives inside "
        f"the SVG tiles), found hardcoded hex literal(s): {hex_literals}"
    )


def test_matrix_css_animation_only_declared_inside_reduced_motion_media_query():
    # Only the CSS *outside* any comment may declare an animation -- the
    # header comment's own prose ("The ONLY animation on this layer lives
    # inside...") legitimately contains the word, so strip comments first
    # rather than substring-matching the raw file.
    css = (generate.STATIC_DIR / "css" / "matrix.css").read_text(encoding="utf-8")
    css_no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    media_start = css_no_comments.index("@media (prefers-reduced-motion: no-preference)")
    before_media = css_no_comments[:media_start]
    assert not re.search(r"\banimation\s*:", before_media)
    assert "@keyframes" not in before_media
    after_media = css_no_comments[media_start:]
    assert re.search(r"\banimation\s*:", after_media)
    assert "@keyframes matrix-rain-fall" in after_media


# --- Opaque chrome-background invariant (Matrix-theme rain layer, T2) ------
#
# The decorative fixed rain layer (site/static/css/matrix.css) is
# `position: fixed; inset: 0; z-index: -1` -- it paints above the body's own
# canvas background but below anything in-flow with its own background.
# .masthead, .site-footer, and main must each set an opaque
# `background: var(--color-bg)` so rain is only ever visible in gutters /
# empty chrome space, never behind reading text. This test parses the real
# built components.css rather than re-asserting a hardcoded string, so it
# actually checks the CSS rule the browser will use, not just substring
# presence anywhere in the file.


def _css_rule_body(css_text: str, selector: str) -> str:
    """Return the `{ ... }` body text of the first rule whose selector list
    is exactly `selector` (e.g. "main", ".masthead"). Raises AssertionError
    if no such rule is found."""
    pattern = re.compile(
        r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", re.MULTILINE
    )
    match = pattern.search(css_text)
    assert match is not None, f"no CSS rule found for selector {selector!r}"
    return match.group(1)


def test_masthead_site_footer_and_main_have_opaque_token_backgrounds(tmp_path):
    generate.generate(public_dir=tmp_path)
    css = (tmp_path / "static" / "css" / "components.css").read_text(encoding="utf-8")
    for selector in ("main", ".masthead", ".site-footer"):
        body = _css_rule_body(css, selector)
        assert "background: var(--color-bg);" in body, (
            f"{selector} rule is missing an opaque background: var(--color-bg) "
            "declaration -- the fixed rain layer would show through it"
        )


def test_components_css_has_no_hardcoded_hex_colors(tmp_path):
    generate.generate(public_dir=tmp_path)
    css = (tmp_path / "static" / "css" / "components.css").read_text(encoding="utf-8")
    hex_literals = re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)
    assert hex_literals == [], (
        f"components.css must source every color from tokens.css custom "
        f"properties, found hardcoded hex literal(s): {hex_literals}"
    )


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
    # regardless. Phase 7: "index.html" is now the map homepage;
    # "wire/index.html" is the Wire index that used to live at
    # "index.html" before the homepage swap.
    expected = [
        "index.html",
        "wire/index.html",
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


def test_masthead_sparkline_strip_renders_on_the_wire_home_page(built_site):
    # Nav-condense pass (see IMPROVEMENT_BACKLOG.md): the masthead
    # sparkline strip is scoped to the Wire home page -- the site's own
    # front page -- only.
    html = (built_site / "index.html").read_text(encoding="utf-8")
    assert "masthead-strip" in html, "public/index.html is missing the masthead sparkline strip"


def test_masthead_sparkline_strip_absent_from_every_other_page(built_site):
    non_home_pages = [p for p in _all_html_files(built_site) if p != built_site / "index.html"]
    assert non_home_pages, "expected at least one non-home generated page"
    for page in non_home_pages:
        html = page.read_text(encoding="utf-8")
        assert "masthead-strip" not in html, (
            f"{page.relative_to(built_site)} unexpectedly has the home-page-only masthead sparkline strip"
        )


# --- Matrix-theme digital-rain layer (T3): rendered on every page ----------
#
# Unlike the masthead sparkline strip above (Wire-home-page-only), the rain
# layer is a genuinely site-wide concern -- it must render on every page.


def test_matrix_rain_layer_renders_on_every_generated_page(built_site):
    pages = _all_html_files(built_site)
    assert pages, "expected at least one generated HTML page"
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert 'class="matrix-rain"' in html, (
            f"{page.relative_to(built_site)} is missing the matrix-rain layer"
        )
        assert 'aria-hidden="true"' in html


def test_matrix_rain_layer_has_the_full_default_column_count_on_every_page(built_site):
    html = (built_site / "index.html").read_text(encoding="utf-8")
    assert html.count('class="matrix-rain__col"') == generate.matrix_rain.DEFAULT_COLUMN_COUNT


def test_matrix_rain_layer_comes_after_the_skip_link_and_before_the_masthead(built_site):
    html = (built_site / "index.html").read_text(encoding="utf-8")
    skip_link_pos = html.index('class="skip-link"')
    rain_pos = html.index('class="matrix-rain"')
    masthead_pos = html.index('class="masthead"')
    assert skip_link_pos < rain_pos < masthead_pos


def test_matrix_rain_layer_has_no_inline_animation_or_data_uri(built_site):
    # The rendered partial itself must carry only inert custom properties
    # (--rain-duration/--rain-delay/tile-height/tile-width) -- no literal
    # "animation" property and no inlined data: URI -- both belong in
    # matrix.css / matrix-tiles.css respectively, not in the per-page HTML.
    html = (built_site / "index.html").read_text(encoding="utf-8")
    rain_start = html.index('class="matrix-rain"')
    rain_end = html.index("</div>", html.index('class="matrix-rain__col"', rain_start))
    rain_markup = html[rain_start:rain_end]
    assert "animation:" not in rain_markup
    assert "data:image/svg+xml" not in rain_markup


# --- Matrix-theme digital-rain layer (T4): regression tests ----------------
#
# T3 built the rain layer; these tests lock in five properties that a
# future edit could otherwise silently break: the rain wrapper's own
# opening tag genuinely carries both class="matrix-rain" and
# aria-hidden="true" (not just "both strings appear somewhere on the
# page"), the whole site stays zero-JavaScript, matrix.css's inertness
# properties and reduced-motion gating are real (not just present in a
# comment), matrix-tiles.css sources the live signal-green token, a
# from-scratch build is byte-identical across two independent output
# directories (the rain layer's seeded RNG must not break the
# already-established idempotent-rebuild property), and the rain layer is
# structurally inert (no focusable element inside it at all), which is
# what makes the skip-link-first-focusable guarantee hold regardless of
# how many rain columns render.


def test_matrix_rain_wrapper_opening_tag_carries_class_and_aria_hidden_together(built_site):
    # Order-independent: requires both attributes on the *same* opening
    # <div ...> tag (no intervening ">"), not merely both substrings
    # appearing anywhere in the page.
    tag_re = re.compile(
        r'<div\b(?=[^>]*\bclass="matrix-rain")(?=[^>]*\baria-hidden="true")[^>]*>'
    )
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        assert tag_re.search(html), (
            f"{page.relative_to(built_site)}: no single <div> opening tag carries "
            f'both class="matrix-rain" and aria-hidden="true"'
        )


def test_exactly_one_script_tag_and_it_is_the_matrix_rain_canvas(built_site):
    # AI Frontier Wire's zero-JavaScript architecture has exactly TWO
    # deliberate, narrow exceptions (see IMPROVEMENT_BACKLOG.md): the
    # canvas-based Matrix rain effect (site/static/js/matrix-rain.js), a
    # progressive enhancement over the always-present <noscript> CSS/SVG
    # fallback (see the tests below), present on EVERY page with no
    # exception; and, as of Phase 7, the world-map marker-interaction
    # script (site/static/js/map.js), allowed ONLY on the map homepage
    # (public/index.html) -- see site/templates/map_index.html's own
    # `extra_scripts` block. This is the test that keeps both exceptions
    # singular and narrow -- any OTHER <script> tag anywhere, on any
    # page, or map.js appearing on any page other than the map homepage,
    # is still a hard failure, exactly like the test this one replaces.
    script_re = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
    map_home = built_site / "index.html"
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        matches = script_re.findall(html)
        allowed_srcs = ["static/js/matrix-rain.js"]
        if page == map_home:
            allowed_srcs.append("static/js/map.js")
        assert len(matches) == len(allowed_srcs), (
            f"{page.relative_to(built_site)} contains {len(matches)} <script> "
            f"tag(s) -- expected exactly {len(allowed_srcs)}: {allowed_srcs!r}"
        )
        for tag in matches:
            assert 'src="' in tag, (
                f"{page.relative_to(built_site)} has a <script> tag with no "
                f"src attribute: {tag!r}"
            )
            # "static/js/..." rather than an anchored "/static/..." prefix:
            # apply_base_path() legitimately rewrites every src="/... to
            # include the site's base path (e.g. "/AI-Radar/static/...")
            # -- see site/generate.py -- so only the path's own tail is
            # matched.
            assert any(src in tag for src in allowed_srcs), (
                f"{page.relative_to(built_site)}'s <script> tag isn't one of "
                f"the allowed {allowed_srcs!r}, got: {tag!r}"
            )
            assert "defer" in tag, (
                f"{page.relative_to(built_site)}'s <script> tag must carry "
                f"defer so it never blocks parsing/first paint: {tag!r}"
            )
        # Every allowed src for this page must actually be present exactly
        # once -- not just that every present tag is *some* allowed src
        # (which alone wouldn't catch e.g. matrix-rain.js appearing twice
        # while map.js is silently missing from the map homepage).
        for src in allowed_srcs:
            count = sum(1 for tag in matches if src in tag)
            assert count == 1, (
                f"{page.relative_to(built_site)} expected exactly one "
                f"<script src=\"...{src}\"> tag, found {count}"
            )


def test_matrix_rain_canvas_present_on_every_page_before_the_noscript_fallback(
    built_site,
):
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        canvas_pos = html.index('id="matrix-rain-canvas"')
        noscript_pos = html.index("<noscript>")
        assert 'aria-hidden="true"' in html[canvas_pos : canvas_pos + 80], (
            f"{page.relative_to(built_site)}'s matrix-rain canvas must carry "
            f"aria-hidden=\"true\""
        )
        assert canvas_pos < noscript_pos, (
            f"{page.relative_to(built_site)}: the live canvas must come "
            f"before the <noscript> CSS/SVG fallback in document order"
        )


def test_old_css_rain_layer_now_lives_inside_the_noscript_fallback(built_site):
    # The static CSS/SVG rain layer built by the earlier Matrix-theme
    # redesign is NOT dead code -- it's the real fallback for when
    # JavaScript doesn't run. This asserts it's specifically inside the
    # <noscript> element, not just present somewhere on the page.
    html = (built_site / "index.html").read_text(encoding="utf-8")
    noscript_start = html.index("<noscript>")
    noscript_end = html.index("</noscript>")
    noscript_body = html[noscript_start:noscript_end]
    assert 'class="matrix-rain"' in noscript_body
    assert 'class="matrix-rain__col"' in noscript_body


def test_matrix_rain_js_reads_every_color_from_a_css_custom_property():
    # No hardcoded hex/rgba color literal anywhere in the script -- every
    # color it draws with must come from getComputedStyle(...).getPropertyValue
    # against a real tokens.css custom property, exactly like every
    # stylesheet on this site is already required to do.
    js = (generate.STATIC_DIR / "js" / "matrix-rain.js").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", js), (
        "matrix-rain.js contains a hardcoded hex color literal -- colors "
        "must be read from tokens.css custom properties at runtime"
    )
    assert not re.search(r"rgba?\(\s*\d", js), (
        "matrix-rain.js contains a hardcoded rgb()/rgba() color literal -- "
        "colors must be read from tokens.css custom properties at runtime"
    )
    for custom_property in (
        "--color-bg",
        "--color-rain-fade",
        "--color-signal-green",
        "--color-star-white",
    ):
        assert custom_property in js, f"matrix-rain.js never reads {custom_property}"


def test_matrix_rain_js_respects_reduced_motion_including_a_live_toggle():
    js = (generate.STATIC_DIR / "js" / "matrix-rain.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in js
    # Must react to the OS-level preference changing while the page is
    # open, not just its value at first paint -- matches this file's own
    # docstring claim.
    assert "addEventListener" in js and "change" in js


def test_no_hardcoded_decimal_rgb_color_literal_anywhere_in_built_html(built_site):
    # Regression test for a real bug an independent verification pass
    # caught: site/templates/board.html's pulse-dot glow hardcoded a
    # decimal RGB triple equal to the pre-Matrix-theme signal-accent
    # token's own old hex value. The Matrix-theme palette rename's own
    # greps (for the old token's name, and for "#RRGGBB"-style hex
    # literals) never matched that decimal-triple encoding, so it
    # silently kept glowing the old color after everything else had
    # re-themed (see IMPROVEMENT_BACKLOG.md for the full account -- this
    # comment deliberately avoids repeating the old token name/hex
    # verbatim, since those strings are themselves checked to be fully
    # gone from site/ elsewhere). Every color on this site must come
    # from a tokens.css custom property (directly, or via color-mix() on
    # one), never a second hardcoded copy in any encoding -- this test
    # scans the actual rendered HTML of every generated page for a bare
    # rgb()/rgba() literal with a numeric first channel, which no
    # legitimate token-sourced declaration ever produces.
    rgb_re = re.compile(r"rgba?\(\s*\d")
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        assert not rgb_re.search(html), (
            f"{page.relative_to(built_site)} contains a hardcoded decimal "
            f"rgb()/rgba() color literal -- colors must derive from a "
            f"tokens.css custom property, never a second hardcoded copy"
        )


def test_matrix_css_declares_pointer_events_none_and_a_negative_z_index():
    css = (generate.STATIC_DIR / "css" / "matrix.css").read_text(encoding="utf-8")
    assert "pointer-events: none" in css
    assert "z-index: -1" in css


def test_matrix_css_reduced_motion_gating_is_positional_and_singular():
    # Mirrors site/tests/test_board_builder.py's own
    # test_pulse_animation_keyframes_only_inside_reduced_motion_media_query
    # positional-index convention: the media query must open strictly
    # before both the "animation:" declaration and the "@keyframes"
    # matrix-rain-fall rule, and there must be exactly one "animation:"
    # declaration in the whole file -- if a second, unguarded fallback
    # animation is ever added outside the media query, this fails.
    css = (generate.STATIC_DIR / "css" / "matrix.css").read_text(encoding="utf-8")
    media_pos = css.index("@media (prefers-reduced-motion: no-preference)")
    animation_pos = css.index("animation:")
    keyframes_pos = css.index("@keyframes matrix-rain-fall")
    assert media_pos < animation_pos
    assert media_pos < keyframes_pos
    assert css.count("animation:") == 1


def test_built_matrix_tiles_css_sources_the_live_signal_green_token(built_site):
    # Regression-tests generate.py::read_color_token() itself (never a
    # pasted hex literal here) against the actual built output, reusing
    # the shared module-scoped fixture rather than a second standalone
    # build.
    css = (built_site / "static" / "css" / "matrix-tiles.css").read_text(encoding="utf-8")
    assert "data:image/svg+xml" in css
    color = generate.read_color_token("signal-green")
    assert ("%23" + color.lstrip("#")) in css


def test_generate_produces_byte_identical_output_across_two_independent_dirs(
    tmp_path_factory,
):
    # Function-scoped, its own two output directories -- deliberately not
    # the shared built_site fixture, since this test needs two full,
    # independent from-scratch builds to compare against each other.
    dir_a = tmp_path_factory.mktemp("public_idempotence_a")
    dir_b = tmp_path_factory.mktemp("public_idempotence_b")
    generate.generate(public_dir=dir_a)
    generate.generate(public_dir=dir_b)

    def _relative_files(root: Path) -> set[Path]:
        return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}

    files_a = _relative_files(dir_a)
    files_b = _relative_files(dir_b)
    assert files_a == files_b, (
        "generate() produced a different set of files across two "
        "independent builds"
    )
    for rel in sorted(files_a):
        bytes_a = (dir_a / rel).read_bytes()
        bytes_b = (dir_b / rel).read_bytes()
        assert bytes_a == bytes_b, (
            f"{rel} differs byte-for-byte between two independent "
            f"generate() runs -- the rain layer's seeded RNG must not "
            f"break the established byte-idempotent-rebuild property"
        )


def test_matrix_rain_layer_contains_no_focusable_elements(built_site):
    # The rain layer sits between the skip-link and <header class="masthead">
    # on every page (base.html's own template order) and is made up
    # entirely of empty, childless <div class="matrix-rain__col"> elements
    # -- this asserts that structurally, not just by convention, so the
    # existing skip-link-first-focusable test's guarantee doesn't quietly
    # depend on the rain layer staying "well-behaved" by accident.
    focusable_re = re.compile(r"<(a|button|input|select|textarea)\b", re.IGNORECASE)
    for page in _all_html_files(built_site):
        html = page.read_text(encoding="utf-8")
        if 'class="matrix-rain"' not in html:
            continue
        rain_start = html.index('<div class="matrix-rain"')
        header_start = html.index("<header", rain_start)
        rain_markup = html[rain_start:header_start]
        assert not focusable_re.search(rain_markup), (
            f"{page.relative_to(built_site)}'s matrix-rain layer unexpectedly "
            f"contains a focusable element"
        )


def test_skip_link_first_focusable_guarantee_still_holds(built_site):
    # Unmodified re-assertion of the existing invariant, run explicitly
    # here alongside the rain-layer inertness test above so the two are
    # read together: the rain layer being empty (previous test) is *why*
    # this one holds regardless of how many columns render.
    test_skip_link_is_first_focusable_element_on_every_page(built_site)


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
