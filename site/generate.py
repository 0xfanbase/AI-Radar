#!/usr/bin/env python3
"""AI Frontier Wire -- static site generator entrypoint (Phase 4).

This is the Phase-4 **integration** commit: every page builder written in
this same working tree (`site/builders/{wire,board,lexicon,primer,moving,
method,corrections,about}.py`, each independently tested and each
deliberately self-sufficient -- see every one of their own docstrings and
`IMPROVEMENT_BACKLOG.md`'s per-builder entries -- for why none of them
called back into this file until now) is wired together here, in one
place, into the real `public/` output the approved build plan's route
table names:

    /                       Wire home page (last ~14 days)
    /wire/<YYYY-MM>/        Wire monthly archive
    /board/                 Frontier Board
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
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
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

# GitHub Pages' default project-site URL for this repo once Pages is
# enabled with Source: GitHub Actions and no custom domain configured --
# https://<owner>.github.io/<repo>/ (confirmed against this repo's own
# identity: watcher/config.py's user-agent string names
# "github.com/0xfanbase/AI-Radar" as this project's real GitHub
# location). Used only for sitemap.xml's absolute <loc> values and
# robots.txt's Sitemap: line -- every *internal* link this site renders
# stays root-relative (`/board/`, `/lexicon/<slug>/`, etc.), matching
# every route the build plan itself writes as an absolute root path.
# That root-relative convention was already flagged, unresolved, in
# IMPROVEMENT_BACKLOG.md's Phase 4 scaffold entry as a real risk *if*
# GitHub Pages ends up serving this repo from a project subpath
# (`/AI-Radar/`) rather than a custom domain -- this integration commit
# does not fix that (it would mean touching href-generation code in
# every one of the four independently-built, already-tested builder
# modules, well beyond "wire them together and fix integration
# glue"-scope), it only re-confirms and restates the gap plainly here so
# it isn't lost. See IMPROVEMENT_BACKLOG.md's entry for this commit.
SITE_BASE_URL = "https://0xfanbase.github.io/AI-Radar"


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


def collect_routes(cards: list[dict], lexicon_entries: list[dict]) -> list[str]:
    """Every route this build produces, root-relative, for sitemap.xml.
    Order roughly matches the masthead nav's own route order (build plan
    section 5's page list), with the Wire's own newest-first archive
    months and the Lexicon's own alphabetical term slugs interleaved in
    their natural order."""
    routes = ["/"]
    routes += [f"/wire/{ym}/" for ym in wire.available_months(cards)]
    routes.append("/board/")
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
    sensible order (Wire first, since it's the home page; About/404 last,
    since they carry no data dependency at all)."""
    cards = content.get("cards", [])
    lexicon_entries = content.get("lexicon", [])
    frontier_board_rows = content.get("frontier_board", [])
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

    # Build-plan section 5: "a thin masthead sparkline strip site-wide."
    # site/builders/moving.py already computes this exact view model
    # (`build_masthead_sparklines`) and `base.html` already includes the
    # partial whenever its render context carries a non-empty
    # `masthead_sparklines` -- rather than have every other builder's
    # own `build_*_context()` grow a new parameter to pass this through
    # (touching seven already-tested, independently-scoped modules for a
    # cross-cutting concern that belongs to the shared shell, not any one
    # page), this integration sets it once as a Jinja *environment
    # global*: every template rendered through this one shared `env`
    # picks it up automatically (Jinja resolves an undeclared template
    # variable against `env.globals` before falling back to
    # `Undefined`), and `/moving/`'s own context still sets the identical
    # value locally (local context wins over a same-named global with no
    # conflict in value). Logged as a judgment call in
    # IMPROVEMENT_BACKLOG.md.
    env.globals["masthead_sparklines"] = moving.build_masthead_sparklines(
        list(whats_moving.get("topics", []))
    )

    written: list[Path] = []
    written += wire.write_wire_pages(env, cards, lexicon_entries, public_dir, today=today)
    written.append(board.write_board_page(env, frontier_board_rows, today, public_dir))
    written += lexicon_builder.write_lexicon_pages(env, lexicon_entries, public_dir)
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

    routes = collect_routes(cards, lexicon_entries)
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
    re-runs, local iteration)."""
    public_dir.mkdir(parents=True, exist_ok=True)
    content = load_and_validate_content()
    env = build_jinja_env()
    render_pages(env, content, public_dir)
    copy_static(public_dir)
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
