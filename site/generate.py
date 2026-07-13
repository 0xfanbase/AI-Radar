#!/usr/bin/env python3
"""AI Frontier Wire -- static site generator entrypoint (Phase 4; homepage
route reassigned to the world map in Phase 7).

This is the Phase-4 **integration** commit: every page builder written in
this same working tree (`site/builders/{wire,board,lexicon,primer,moving,
method,corrections,about}.py`, each independently tested and each
deliberately self-sufficient -- see every one of their own docstrings and
`IMPROVEMENT_BACKLOG.md`'s per-builder entries -- for why none of them
called back into this file until now) is wired together here, in one
place, into the real `public/` output. Phase 7 (`site/builders/map.py`)
reassigns `/` from the Wire index to the new world-map homepage and moves
the Wire index itself to `/wire/` -- see PROGRESS.md's Phase 7 entry for
why. Current route table:

    /                       World map homepage (Phase 7)
    /wire/                  Wire index (last ~14 days) -- moved here from `/`
    /wire/<YYYY-MM>/        Wire monthly archive
    /board/                 Frontier Board
    /companies/             Companies index (Phase 8)
    /companies/<slug>/      One fact-checked profile page per company (Phase 8)
    /lexicon/                Lexicon index
    /lexicon/<slug>/        one page per Lexicon term
    /primer/                Primer (10-step on-ramp)
    /moving/                What's Moving
    /method/                Method & Audit
    /corrections/           Corrections
    /about/                 About
    /404.html               GitHub Pages' own not-found page
    /sitemap.xml /robots.txt

`content/cards/` is empty as of this build stage (no analyst run has
happened for real yet) -- every builder above is already written to
degrade gracefully around that (see each one's own "EMPTY_*_MESSAGE"
constant), so this integration does not special-case it further.

Usage:
    python -m site.generate [--out public] [-v]
    python site/generate.py [--out public] [-v]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import jsonschema
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SITE_DIR.parent
CONTENT_DIR = REPO_ROOT / "content"
DATA_DIR = REPO_ROOT / "data"
SCHEMAS_DIR = REPO_ROOT / "schemas"
TEMPLATES_DIR = SITE_DIR / "templates"
STATIC_DIR = SITE_DIR / "static"
BUILDERS_DIR = SITE_DIR / "builders"
PUBLIC_DIR = REPO_ROOT / "public"

log = logging.getLogger("frontier_wire.site.generate")

# GitHub Pages' default project-site URL for this repo, with Source:
# GitHub Actions and no custom domain configured --
# https://<owner>.github.io/<repo>/ (confirmed live: this repo is in fact
# served from this exact URL as of 2026-07-11). Used for sitemap.xml's
# absolute <loc> values, robots.txt's Sitemap: line, AND -- since this
# repo is a GitHub Pages *project* site, not a custom domain -- as the
# single source of truth for BASE_PATH below, which every internal
# root-relative link this site renders gets prefixed with post-render.
# If this project ever moves to a custom domain mapped to its root (a
# CNAME file + DNS), update ONLY this one constant to the bare domain
# (e.g. "https://example.com", empty path) and BASE_PATH below becomes ""
# automatically -- no other code changes needed.
SITE_BASE_URL = "https://0xfanbase.github.io/AI-Radar"

# Derived, not hand-maintained: the path component of SITE_BASE_URL
# ("/AI-Radar" today), stripped of any trailing slash. Confirmed live
# 2026-07-11 that every internal link this site renders (nav, footer,
# lexicon auto-links, "seen in" card links, static asset hrefs -- all
# root-relative by every builder module's own, still-correct, design) was
# actually broken (real 404s) once GitHub Pages started serving this repo
# from this project subpath rather than a domain root -- see PROGRESS.md
# for the incident. Rather than thread a base-path parameter through
# every one of the seven independently-tested builder modules plus
# site/lib/linkify.py's baked-in anchor hrefs (a much larger, riskier
# change touching every builder's tests), _apply_base_path() below does
# one deterministic find-and-replace pass over the fully-rendered HTML
# output -- see that function's own docstring for exactly what it
# rewrites and why it's safe.
BASE_PATH = urlparse(SITE_BASE_URL).path.rstrip("/")


def _load_module_by_path(name: str, path: Path):
    """Load a module from an explicit file path, registering it in
    `sys.modules` *before* `exec_module` runs.

    Matches the convention every Phase 4 module/test in this repo already
    uses (`site/builders/wire.py`, `site/builders/lexicon.py`,
    `site/tests/test_board_builder.py`, etc.): `site/` is deliberately never
    turned into an importable package (it would shadow the stdlib `site`
    module for anything else sharing the interpreter's `sys.path`), so
    every cross-file reference within `site/` loads its target by path
    instead of via `import site.builders.wire`. The early `sys.modules`
    registration is required because several of these modules use
    `@dataclass` together with `from __future__ import annotations`;
    dataclasses' own annotation resolution looks up
    `sys.modules[cls.__module__]` and raises if that key isn't populated
    yet.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wire = _load_module_by_path("frontier_wire_site_builders_wire", BUILDERS_DIR / "wire.py")
board = _load_module_by_path("frontier_wire_site_builders_board", BUILDERS_DIR / "board.py")
map_builder = _load_module_by_path("frontier_wire_site_builders_map", BUILDERS_DIR / "map.py")
company_builder = _load_module_by_path(
    "frontier_wire_site_builders_company", BUILDERS_DIR / "company.py"
)
lexicon_builder = _load_module_by_path(
    "frontier_wire_site_builders_lexicon", BUILDERS_DIR / "lexicon.py"
)
primer_builder = _load_module_by_path(
    "frontier_wire_site_builders_primer", BUILDERS_DIR / "primer.py"
)
moving = _load_module_by_path("frontier_wire_site_builders_moving", BUILDERS_DIR / "moving.py")
method = _load_module_by_path("frontier_wire_site_builders_method", BUILDERS_DIR / "method.py")
corrections_builder = _load_module_by_path(
    "frontier_wire_site_builders_corrections", BUILDERS_DIR / "corrections.py"
)
about = _load_module_by_path("frontier_wire_site_builders_about", BUILDERS_DIR / "about.py")
matrix_rain = _load_module_by_path(
    "frontier_wire_site_lib_matrix_rain", SITE_DIR / "lib" / "matrix_rain.py"
)


# Matches site/tests/test_contrast_ratios.py's own `_TOKEN_RE` -- the single
# sanctioned way to pull a real, on-disk tokens.css color value into Python.
# Never hardcode a token's hex value a second time anywhere in this module
# (that is exactly site/lib/svg_sparkline.py's already-logged
# duplicate-hex-literal bug this function exists to avoid repeating for the
# rain layer).
_TOKEN_RE = re.compile(r"--color-([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\b")


def read_color_token(name: str, tokens_css_path: Path = STATIC_DIR / "css" / "tokens.css") -> str:
    """Parse the real, on-disk tokens.css and return the `#RRGGBB` hex value
    bound to `--color-<name>`. Raises ValueError loudly if the token isn't
    found -- a silent fallback here would risk the rain layer quietly
    rendering in some other stale color if tokens.css is ever restructured,
    rather than failing the build immediately and visibly."""
    css_text = tokens_css_path.read_text(encoding="utf-8")
    tokens = dict(_TOKEN_RE.findall(css_text))
    if name not in tokens:
        raise ValueError(
            f"no --color-{name} token found in {tokens_css_path} -- "
            f"found tokens: {sorted(tokens)!r}"
        )
    return tokens[name]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def schema_for(content_path: Path) -> Path | None:
    """The schemas/*.schema.json counterpart for a content/data file, by
    filename-stem convention (frontier_board.json -> frontier_board.schema.json).
    Returns None if no such schema file exists yet."""
    candidate = SCHEMAS_DIR / f"{content_path.stem}.schema.json"
    return candidate if candidate.exists() else None


def iter_top_level_json(directory: Path) -> list[Path]:
    """Every *.json file directly inside `directory` (non-recursive --
    subdirectories like content/cards/, data/.cache/, or data/audit/ are
    handled by their own dedicated loaders, not swept up here)."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def load_cards() -> list[dict]:
    """Load every content/cards/<id>.json (excluding the generated
    index.json manifest). Returns [] if the directory doesn't exist yet or
    is empty -- true as of this build stage, since no analyst run has
    happened for real yet. Every template/builder touching cards must
    handle the empty-list case gracefully rather than crash or render a
    broken-looking page.

    Each card is jsonschema-validated against schemas/card.schema.json
    (when present) before being returned -- matching this function's own
    caller's docstring (`load_and_validate_content`, which has always
    claimed cards are validated "when a schema exists") and this repo's
    established principle (see scripts/update_card_index.py,
    IMPROVEMENT_BACKLOG.md): a card that fails its schema at build time
    is a real, loud bug worth surfacing immediately, not something to
    quietly publish or skip. `content/cards/<id>.json`'s filename stem is
    the card's own id, not "card", so `schema_for()`'s filename-stem
    convention can't locate this schema automatically the way it does for
    top-level content/data files -- this looks it up by its own known
    name instead.
    """
    cards_dir = CONTENT_DIR / "cards"
    if not cards_dir.is_dir():
        return []
    card_schema_path = SCHEMAS_DIR / "card.schema.json"
    card_schema = load_json(card_schema_path) if card_schema_path.exists() else None
    if card_schema is None:
        log.warning(
            "no schema found at schemas/card.schema.json -- cards loaded unvalidated"
        )
    cards = []
    for path in sorted(cards_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        card = load_json(path)
        if card_schema is not None:
            jsonschema.validate(card, card_schema, format_checker=jsonschema.FormatChecker())
        cards.append(card)
    return cards


def load_companies_index() -> list[dict]:
    """Load `content/companies/index.json`'s `companies[]` array -- the
    map homepage's marker list (id/name/hq_country/hq_city/hq_lat/
    hq_lng/status only). Returns `[]` if the file doesn't exist yet.

    No `schemas/company_index.schema.json` exists (this summary shape
    is deliberately not the same as `schemas/company.schema.json`,
    which describes the fuller per-company profile record at
    `content/companies/<id>.json` -- a Phase 8 concern this loader
    doesn't touch) -- logged in IMPROVEMENT_BACKLOG.md, same "loaded
    unvalidated" tolerance this module's own docstring already applies
    to `content/primer.json`. `content/companies/` is a subdirectory,
    not a top-level `content/*.json` file, so `iter_top_level_json()`
    never sees it -- this is a bespoke loader for the same reason
    `load_cards()` is."""
    path = CONTENT_DIR / "companies" / "index.json"
    if not path.is_file():
        log.warning(
            "no content/companies/index.json found -- map homepage will render "
            "zero markers"
        )
        return []
    payload = load_json(path)
    return list(payload.get("companies", []))


def load_companies() -> list[dict]:
    """Load every real `content/companies/<slug>.json` full profile
    (excluding the generated `index.json` summary manifest this module's
    own `load_companies_index()` reads instead) -- the Phase 8 company
    profile pages' own input, distinct from the map homepage's marker
    summary. Returns `[]` if `content/companies/` doesn't exist yet.

    Each profile is jsonschema-validated against
    `schemas/company.schema.json` (when present) before being returned --
    same "a bad on-disk artifact is a real, loud bug" discipline this
    module's own `load_cards()` already applies to `content/cards/`.
    """
    companies_dir = CONTENT_DIR / "companies"
    if not companies_dir.is_dir():
        return []
    company_schema_path = SCHEMAS_DIR / "company.schema.json"
    company_schema = load_json(company_schema_path) if company_schema_path.exists() else None
    if company_schema is None:
        log.warning(
            "no schema found at schemas/company.schema.json -- company "
            "profiles loaded unvalidated"
        )
    companies = []
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        company = load_json(path)
        if company_schema is not None:
            jsonschema.validate(
                company, company_schema, format_checker=jsonschema.FormatChecker()
            )
        companies.append(company)
    return companies


def load_and_validate_content() -> dict[str, Any]:
    """Load every top-level content/*.json and data/*.json artifact plus
    content/cards/*.json, jsonschema-validating each against its
    schemas/*.schema.json counterpart when one exists. A file with no
    matching schema is loaded unvalidated and logged, rather than treated
    as fatal -- one such gap (content/primer.json) is inherited from
    earlier phases and is not this integration commit's to fix; see
    IMPROVEMENT_BACKLOG.md. `data/audit/latest.json` is loaded separately
    by `site/builders/method.py::load_audit_latest` (it's nested under
    `data/audit/`, not a top-level `data/*.json` file, and doesn't exist
    yet in this environment -- Phase 5 scope)."""
    loaded: dict[str, Any] = {}
    for path in iter_top_level_json(CONTENT_DIR) + iter_top_level_json(DATA_DIR):
        payload = load_json(path)
        schema_path = schema_for(path)
        if schema_path is None:
            log.warning(
                "no schema found for %s (expected schemas/%s.schema.json) "
                "-- loaded unvalidated",
                path.relative_to(REPO_ROOT),
                path.stem,
            )
        else:
            schema = load_json(schema_path)
            # format_checker=jsonschema.FormatChecker(): jsonschema.validate()
            # silently *ignores* every "format" keyword (format: date,
            # date-time, uri, ...) unless a FormatChecker is explicitly
            # passed in -- without one, a schema's "format": "date" is a
            # no-op annotation, not a constraint, so e.g. a malformed
            # last_verified/release_date string would sail through
            # validation here and only blow up later, deep inside a page
            # builder's own date parsing (see site/builders/board.py's
            # is_pulse_eligible), rather than failing loudly and clearly
            # at this validation step the way every other schema
            # violation already does.
            jsonschema.validate(payload, schema, format_checker=jsonschema.FormatChecker())
        loaded[path.stem] = payload
    loaded["cards"] = load_cards()
    loaded["companies_index"] = load_companies_index()
    loaded["companies"] = load_companies()
    return loaded


def build_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def copy_static(public_dir: Path) -> None:
    dest = public_dir / "static"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(STATIC_DIR, dest)


def write_matrix_tiles_css(unique_tiles: list[str], public_dir: Path) -> Path:
    """Write `public/static/css/matrix-tiles.css`: one `.matrix-rain` rule
    binding each unique rain-tile `data:image/svg+xml,...` URI (see
    site/lib/matrix_rain.py) to its own `--rain-tile-<index>` custom
    property, so `_matrix_rain.html`'s per-column inline style can reference
    `var(--rain-tile-<n>)` instead of inlining a ~7KB data URI into every
    one of `DEFAULT_COLUMN_COUNT` (72) column divs on every page.

    Generated, not hand-maintained -- do not hand-edit this file; it is
    fully overwritten on every `site/generate.py` run. Must run *after*
    `copy_static()` (which `shutil.rmtree()`s `public/static` before
    recopying `site/static`) or this file would be deleted immediately
    after being written."""
    lines = [
        "/* generated by site/generate.py from site/lib/matrix_rain.py; glyph "
        "color sourced live from tokens.css --color-signal-green -- do not "
        "hand-edit */",
        ".matrix-rain {",
    ]
    for i, uri in enumerate(unique_tiles):
        lines.append(f'  --rain-tile-{i}: url("{uri}");')
    lines.append("}")
    path = public_dir / "static" / "css" / "matrix-tiles.css"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def apply_base_path(public_dir: Path, base_path: str = BASE_PATH) -> int:
    """Rewrite every root-relative `href="/...`/`src="/...` in every
    generated `*.html` file under `public_dir` to `href="{base_path}/...`,
    so the site works when served from a GitHub Pages project subpath
    (e.g. `/AI-Radar/`) rather than a domain root. A no-op (returns 0
    immediately) when `base_path` is empty -- the correct behavior for a
    custom domain mapped to its root, or for local testing against a bare
    `python -m http.server`.

    Deliberately a post-render find-and-replace over already-written HTML,
    not a parameter threaded through every builder/template: every one of
    the seven page builders, `site/lib/linkify.py`'s baked-in lexicon
    anchors, and `site/builders/lexicon.py`'s `seen_in_href` all
    independently, correctly render root-relative internal links by
    design (matching the build plan's own route table) -- rewriting the
    literal `href="/`/`src="/` prefix they all share, once, here, fixes
    every one of them without touching any of their already-tested
    internals or existing test expectations (which correctly keep
    asserting bare root-relative hrefs, the correct default for
    base_path="").

    Only ever rewrites a *literal* `href="/` / `src="/` immediately
    following the attribute name and an opening quote -- an external,
    already-absolute link (`href="https://..."`) or a same-page fragment
    link (`href="#main-content"`) never matches this exact substring, so
    neither needs any special-casing to stay untouched.

    Safe to call on a freshly-generated `public_dir` only: `generate()`
    always removes and recreates `public_dir` before rendering (see its
    own docstring), specifically so this function is never applied twice
    to the same already-rewritten file and cannot double-prefix a path.
    """
    if not base_path:
        return 0
    rewritten = 0
    for html_path in public_dir.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        new_text = text.replace('href="/', f'href="{base_path}/').replace(
            'src="/', f'src="{base_path}/'
        )
        if new_text != text:
            html_path.write_text(new_text, encoding="utf-8")
            rewritten += 1
    return rewritten


def collect_routes(
    cards: list[dict], lexicon_entries: list[dict], companies: list[dict] | None = None
) -> list[str]:
    """Every route this build produces, root-relative, for sitemap.xml.
    Order roughly matches the masthead nav's own route order (Map, Wire,
    Board, Companies, Lexicon, Primer, ...), with the Wire's own
    newest-first archive months, the Companies index's own alphabetical
    profile slugs, and the Lexicon's own alphabetical term slugs
    interleaved in their natural order. `/` is the map homepage as of
    Phase 7 (site/builders/map.py); the Wire index itself moved to
    `/wire/` (see PROGRESS.md's Phase 7 entry) -- both are listed
    explicitly since neither is derivable from the other. `companies`
    (Phase 8, defaulted to `()` so an existing caller passing only two
    positional args keeps working) is the full set of loaded
    `content/companies/<slug>.json` profiles."""
    companies = companies or []
    routes = ["/", "/wire/"]
    routes += [f"/wire/{ym}/" for ym in wire.available_months(cards)]
    routes.append("/board/")
    routes.append("/companies/")
    routes += [
        f"/companies/{slug}/"
        for slug in [str(c["id"]) for c in company_builder.sorted_companies(companies)]
    ]
    routes.append("/lexicon/")
    routes += [f"/lexicon/{slug}/" for slug in lexicon_builder.all_slugs(lexicon_entries)]
    routes.append("/primer/")
    routes.append("/moving/")
    routes.append("/method/")
    routes.append("/corrections/")
    routes.append("/about/")
    return routes


def write_sitemap(routes: list[str], public_dir: Path, base_url: str = SITE_BASE_URL) -> Path:
    """Write `public/sitemap.xml` -- one `<url><loc>` per route in
    `routes`, each an absolute URL under `base_url`. `404.html` is
    deliberately not a sitemap entry (it isn't a page anyone should be
    directed to visit; it's GitHub Pages' own not-found fallback)."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for route in routes:
        loc = xml_escape(f"{base_url}{route}")
        lines.append(f"  <url><loc>{loc}</loc></url>")
    lines.append("</urlset>")
    path = public_dir / "sitemap.xml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_robots(public_dir: Path, base_url: str = SITE_BASE_URL) -> Path:
    """Write `public/robots.txt` -- allow every crawler, point at
    sitemap.xml. This site has no non-public area to disallow."""
    content = "User-agent: *\nAllow: /\nSitemap: {}/sitemap.xml\n".format(base_url)
    path = public_dir / "robots.txt"
    path.write_text(content, encoding="utf-8")
    return path


def render_pages(env: Environment, content: dict[str, Any], public_dir: Path) -> list[Path]:
    """Render + write every real page this site has, per the approved
    build plan's route table (module docstring above). Delegates all
    page-specific logic to each independently-tested builder module under
    `site/builders/` -- this function's only job is calling each one with
    the right slice of `content` and the shared `env`/`public_dir`, in a
    sensible order (the map homepage first, since it's `/` as of Phase
    7; About/404 last, since they carry no data dependency at all)."""
    cards = content.get("cards", [])
    lexicon_entries = content.get("lexicon", [])
    frontier_board_rows = content.get("frontier_board", [])
    companies_index = content.get("companies_index", [])
    companies = content.get("companies", [])
    primer = content.get("primer", {})
    whats_moving = content.get("whats_moving", {})
    ledger = content.get("ledger", {})
    verifier_stats = content.get("verifier_stats", {})
    corrections_entries = content.get("corrections", [])
    audit_latest = method.load_audit_latest()

    # A single "today" for the whole build (the Wire's 14-day window and
    # the Board's 7-day pulse-eligibility window both anchor on it) --
    # computed once here, at the top-level entrypoint, rather than by any
    # individual builder (each of which stays a pure function of an
    # explicitly-passed date; see board.py's own `is_pulse_eligible`
    # docstring for why that matters for testability).
    today = datetime.now(timezone.utc).date()

    # Nav-condense pass (see IMPROVEMENT_BACKLOG.md): the masthead
    # sparkline strip is scoped to exactly one page, not site-wide -- so
    # it's computed once here and passed explicitly to that page's
    # writer below, rather than injected as a Jinja environment global
    # every template picked up automatically. Capped to the top
    # `moving.MASTHEAD_TOPIC_LIMIT` topics by 7-day mention total
    # (`build_masthead_sparklines`'s own job) so the strip fits a narrow
    # mobile viewport. Phase 7 moves the strip from the Wire home page
    # to the new map homepage (see the approved plan's "the existing
    # What's Moving strip stays above the map" interaction-design
    # note) -- the Wire itself (now at `/wire/`) no longer receives it,
    # so it's passed to `map_builder.write_map_page()` only, never to
    # `wire.write_wire_pages()` below.
    strip_views = moving.build_masthead_sparklines(list(whats_moving.get("topics", [])))

    written: list[Path] = []
    written.append(
        map_builder.write_map_page(
            env,
            companies_index,
            frontier_board_rows,
            cards,
            public_dir,
            masthead_sparklines=strip_views,
        )
    )
    written += wire.write_wire_pages(
        env,
        cards,
        lexicon_entries,
        public_dir,
        today=today,
        masthead_sparklines=None,
        index_output_dir=public_dir / "wire",
    )
    written.append(
        board.write_board_page(
            env, frontier_board_rows, today, public_dir, lexicon_entries=lexicon_entries
        )
    )
    written += company_builder.write_company_pages(
        env, companies, frontier_board_rows, cards, public_dir
    )
    written += lexicon_builder.write_lexicon_pages(
        env, lexicon_entries, public_dir, cards=cards
    )
    written.append(
        primer_builder.write_primer_page(env, primer, lexicon_entries, public_dir)
    )
    written.append(moving.write_moving_page(env, whats_moving, public_dir))
    written.append(
        method.write_method_page(env, ledger, verifier_stats, audit_latest, public_dir)
    )
    written.append(
        corrections_builder.write_corrections_page(env, corrections_entries, public_dir)
    )
    written.append(about.write_about_page(env, public_dir))
    written.append(write_404_page(env, public_dir))

    routes = collect_routes(cards, lexicon_entries, companies)
    write_sitemap(routes, public_dir)
    write_robots(public_dir)

    return written


def write_404_page(env: Environment, public_dir: Path) -> Path:
    """Render + write `public/404.html` -- GitHub Pages automatically
    serves a repo-root `404.html` for any unmatched path on a project
    site, no extra configuration needed once Pages is enabled. Rendered
    through the same shared `base.html` shell as every other page (skip
    link, masthead, footer, single `<h1>`), not a bare fragment."""
    html = env.get_template("404.html").render()
    path = public_dir / "404.html"
    path.write_text(html, encoding="utf-8")
    return path


def generate(public_dir: Path = PUBLIC_DIR) -> Path:
    """Run the full build: load+validate content/data, render every page,
    copy static assets, write everything under `public_dir`. Returns the
    output directory. Safely re-runnable into the same directory (CI
    re-runs, local iteration) -- `public_dir` is removed and recreated
    fresh on every call (rather than merely `mkdir(exist_ok=True)`-ed) so
    a re-run can never leave a stale page (e.g. a since-removed lexicon
    term's old file) lingering, and so `apply_base_path()` below is
    guaranteed to run against exactly one generation's worth of freshly
    root-relative hrefs -- never a previous run's already-rewritten
    output, which would double-prefix every internal link."""
    if public_dir.exists():
        shutil.rmtree(public_dir)
    public_dir.mkdir(parents=True)
    content = load_and_validate_content()
    env = build_jinja_env()

    # Matrix-theme digital-rain layer (site-wide decorative chrome, zero-JS):
    # unlike the since-revisited masthead_sparklines global (scoped to one
    # builder/page, see IMPROVEMENT_BACKLOG.md's nav-condense entry), the
    # rain layer is a genuinely global concern -- every page's base.html
    # shell renders it -- so env.globals is the right mechanism here rather
    # than threading it through render_pages()/every individual builder.
    # `read_color_token()` is the single sanctioned way to get the live
    # signal-green hex into Python; never hardcode it a second time (that
    # is exactly site/lib/svg_sparkline.py's already-logged duplicate-hex
    # bug this call avoids repeating).
    rain_color = read_color_token("signal-green")
    rain_columns = matrix_rain.build_rain_columns(rain_color)
    unique_tiles = list(dict.fromkeys(col.tile_data_uri for col in rain_columns))
    tile_index = {uri: i for i, uri in enumerate(unique_tiles)}
    env.globals["matrix_rain_columns"] = [
        {
            "left_pct": col.left_pct,
            "duration_s": col.duration_s,
            "delay_s": col.delay_s,
            "tile_index": tile_index[col.tile_data_uri],
        }
        for col in rain_columns
    ]
    env.globals["matrix_rain_tile_height_px"] = (
        matrix_rain.GLYPH_UNIT_HEIGHT * matrix_rain.DEFAULT_GLYPHS_PER_TILE
    )
    env.globals["matrix_rain_tile_width_px"] = matrix_rain.TILE_WIDTH_UNITS

    render_pages(env, content, public_dir)
    copy_static(public_dir)
    # Must run after copy_static() (which rmtree()s+recopies public/static)
    # and before apply_base_path() (harmless either order for this file
    # specifically, since data URIs are never rewritten by it, but this
    # keeps build-step ordering deterministic and matches the task's own
    # documented sequencing).
    write_matrix_tiles_css(unique_tiles, public_dir)
    apply_base_path(public_dir)
    return public_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=PUBLIC_DIR,
        help="Output directory for the built site (default: public/, gitignored)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    out = generate(args.out)
    print(f"Built site to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
