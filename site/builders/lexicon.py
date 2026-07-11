"""Lexicon builder (Phase 4, build-plan section 5) -- alphabetical index +
one page per term.

Reads the seeded ``content/lexicon.json`` (30 real, live-cited entries as
of this commit -- see ``IMPROVEMENT_BACKLOG.md``'s Phase 3 entries) and
renders:

* ``/lexicon/`` -- an alphabetical index of every term, each linking to
  its own page and showing its ``one_liner``.
* ``/lexicon/<slug>/`` -- one page per term, rendering ``one_liner``,
  ``deeper`` (with its one inline citation anchor preserved as real,
  trusted HTML -- see :func:`render_deeper_html`), ``related`` terms as
  real links to other term pages, and ``seen_in`` as links to the Wire
  card(s) that reference it, or an honest "not yet referenced" message
  when empty (true for all 30 seed terms today -- no analyst run has
  happened for real yet).

``deeper`` field HTML handling (build-plan section 5's own "no
``<img>``, careful about untrusted HTML" discipline, applied here):
Phase 3's backfill wrote each entry's ``deeper`` prose with exactly one
inline ``<a href="...">...</a>`` citation anchor and nothing else that
needs HTML-escaping (no stray ``<``, ``>``, ``&`` outside that anchor --
spot-checked against all 30 real entries before writing this module).
Rather than either (a) blindly marking the whole field ``Markup``-safe
(would also trust any *other* accidental markup in the surrounding
prose, e.g. a future backfill/analyst edit that introduces one) or (b)
blindly ``html.escape()``-ing the whole field (would mangle the one
anchor's own ``<a>``/``</a>`` tag delimiters into ``&lt;a&gt;`` and break
the trusted link), :func:`render_deeper_html` does what
``site/lib/linkify.py`` already does for card prose: escape every
character of the surrounding text, and reconstruct only the anchor
tag(s) it can actually find via a narrow, literal regex, escaping the
anchor's own ``href``/text pieces again on the way back out (a no-op for
today's clean seed content, but a real defense if a future entry's
anchor text or href ever contained a stray HTML-special character).

Two-step build usage (mirrors ``site/builders/wire.py`` and
``site/builders/board.py``'s own convention):

1. Call :func:`build_index_context`/:func:`build_term_context` against
   the loaded ``content/lexicon.json`` array to get the fully-computed
   template context.
2. Render via :func:`render_lexicon_index`/:func:`render_lexicon_term`
   (each accepts a Jinja ``Environment`` the caller supplies -- e.g. a
   future ``site/generate.py`` integration's own env -- or builds its own
   minimal one via :func:`build_jinja_env` when none is given, matching
   ``board.py``'s "self-sufficient, not wired into generate.py yet" turn
   scope).

This module deliberately does *not* wire itself into ``site/generate.py``
(out of this turn's scope -- another turn integrates every Phase 4
builder together); see ``IMPROVEMENT_BACKLOG.md``.
"""
from __future__ import annotations

import html
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

BUILDERS_DIR = Path(__file__).resolve().parent
SITE_DIR = BUILDERS_DIR.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
LIB_DIR = SITE_DIR / "lib"
CONTENT_DIR = REPO_ROOT / "content"
LEXICON_PATH = CONTENT_DIR / "lexicon.json"


def _load_module_by_path(name: str, path: Path):
    """Load a module from an explicit file path, registering it in
    `sys.modules` *before* `exec_module` runs -- matches the convention
    every other Phase 4 module/test already uses (`site/builders/wire.py`,
    `site/tests/test_linkify.py`, `site/tests/test_board_builder.py`): `site/` is
    deliberately never turned into an importable package (it would shadow
    the stdlib `site` module), so every cross-file reference within
    `site/` loads its target by path instead of via a package import. The
    early `sys.modules` registration is required because `linkify.py` uses
    `@dataclass` together with `from __future__ import annotations`.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Reused verbatim rather than reimplemented -- `slugify()`/
# `build_slug_map()` must stay byte-for-byte identical to what
# `site/builders/wire.py` uses to link card prose to lexicon pages, or
# the same term could resolve to two different URLs across the site.
linkify = _load_module_by_path("frontier_wire_site_lib_linkify", LIB_DIR / "linkify.py")

slugify = linkify.slugify
build_slug_map = linkify.build_slug_map


EMPTY_SEEN_IN_MESSAGE = "Not yet referenced in a Wire card."

# Matches exactly the anchor shape Phase 3's backfill writes into every
# `deeper` field: a plain `<a href="...">...</a>` with no nested tags and
# no other attributes. Deliberately narrow (not a general HTML parser) --
# see `render_deeper_html`'s docstring for why a narrow, literal match is
# the safer choice here.
_ANCHOR_RE = re.compile(r'<a href="([^"]*)">([^<]*)</a>')


def render_deeper_html(deeper: str) -> Markup:
    """Render a lexicon entry's `deeper` field as trusted HTML: every
    character of the surrounding prose is HTML-escaped, and the one (or
    more) inline citation anchor(s) `_ANCHOR_RE` can find are rebuilt from
    their own extracted `href`/text pieces -- each of which is itself
    escaped again on the way back out, so a stray HTML-special character
    inside an anchor's title or href (not expected in today's seed
    content, but not assumed either) still can't smuggle unescaped markup
    into the page. A `deeper` string with no matching anchor at all (not
    expected for real content -- every one of the 30 seed entries has
    exactly one) falls back to fully-escaped plain text rather than
    raising, matching this build stage's "handle it, don't crash"
    convention.
    """
    pieces: list[str] = []
    last_end = 0
    for match in _ANCHOR_RE.finditer(deeper):
        pieces.append(html.escape(deeper[last_end : match.start()]))
        href, text = match.group(1), match.group(2)
        pieces.append(f'<a href="{html.escape(href, quote=True)}">{html.escape(text)}</a>')
        last_end = match.end()
    pieces.append(html.escape(deeper[last_end:]))
    return Markup("".join(pieces))


def seen_in_href(card_id: str) -> str:
    """Best-effort link target for a `seen_in[]` card id.

    `card.schema.json` documents card ids as `YYYY-MM-DD-slug` (e.g.
    `"2026-07-09-gpt-5-5-release"`, not schema-enforced but the only
    convention in use). When the leading 10 characters parse as an ISO
    date, this links to that month's Wire archive page
    (`site/builders/wire.py`'s `/wire/<YYYY-MM>/` route), anchored at the
    card's own headline heading id -- `site/templates/card.html` already
    gives every rendered card's `<h2>` the id `card-<id>-headline`, so
    this resolves to an in-page scroll target once that month's archive
    is built. Falls back to the bare Wire home route if the id doesn't
    start with a parseable date -- still a valid, resolvable link, just
    without the same-page scroll-to-card behavior. `seen_in[]` is empty
    for all 30 real seed terms today (see `EMPTY_SEEN_IN_MESSAGE`), so
    this path is only exercised by synthetic fixtures until the analyst's
    auto-growth rule starts populating it for real.
    """
    prefix = card_id[:10]
    try:
        date.fromisoformat(prefix)
    except ValueError:
        return "/wire/"
    year_month = prefix[:7]
    return f"/wire/{year_month}/#card-{card_id}-headline"


@dataclass(frozen=True)
class RelatedTermView:
    """One `related[]` reference, resolved against the lexicon slug map.

    `slug` is `None` only for a related name with no matching lexicon
    entry -- not expected in real content (every one of the 30 seed
    entries' `related[]` names resolves exactly to another entry's own
    `term`, verified before writing this module), but handled without
    raising: the template renders such a name as plain unlinked text
    rather than a broken `/lexicon/None/` link.
    """

    term: str
    slug: str | None


@dataclass(frozen=True)
class SeenInView:
    """One `seen_in[]` reference, resolved to a best-effort href plus a
    human-readable link label.

    `label` is the referenced card's real headline when the id resolves
    against a supplied `headline_by_id` mapping, falling back to the bare
    `card_id` machine slug (e.g. `"2026-07-09-gpt-5-5-release"`) only when
    it doesn't -- a stale/unresolvable reference, not the common case.
    Readers should never have to parse a slug to know what they're
    clicking into.
    """

    card_id: str
    href: str
    label: str


@dataclass(frozen=True)
class LexiconEntryView:
    """One rendered Lexicon term page's full context -- the template only
    reads these already-computed fields, it never branches on raw
    `content/lexicon.json` data itself."""

    term: str
    slug: str
    one_liner: str
    deeper_html: Markup
    related: tuple[RelatedTermView, ...] = field(default_factory=tuple)
    seen_in: tuple[SeenInView, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LexiconIndexRow:
    """One row of the `/lexicon/` alphabetical index."""

    term: str
    slug: str
    one_liner: str


def sorted_entries(entries: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every loaded `content/lexicon.json` entry, alphabetically by
    `term` (case-insensitive, so e.g. "MoE" sorts under "m" rather than
    before every lowercase term by ASCII order)."""
    return sorted(entries, key=lambda e: str(e["term"]).lower())


def resolve_related(
    related_terms: Iterable[str], slug_map: Mapping[str, str]
) -> tuple[RelatedTermView, ...]:
    return tuple(
        RelatedTermView(term=term, slug=slug_map.get(term.lower()))
        for term in related_terms
    )


def resolve_seen_in(
    card_ids: Iterable[str], headline_by_id: Mapping[str, str] | None = None
) -> tuple[SeenInView, ...]:
    """Resolve each `seen_in[]` card id to a link target + a human-readable
    label. `headline_by_id` (defaulted so every existing call site keeps
    working unchanged) maps `card_id -> headline`; a card id absent from it
    -- a stale reference to a card that no longer exists, or simply no
    mapping supplied at all -- falls back to showing the bare id, exactly
    today's pre-fix behavior, rather than a broken/blank label.
    """
    headline_by_id = headline_by_id or {}
    return tuple(
        SeenInView(
            card_id=cid,
            href=seen_in_href(cid),
            label=headline_by_id.get(cid) or cid,
        )
        for cid in card_ids
    )


def build_entry_view(
    raw: Mapping[str, Any],
    slug_map: Mapping[str, str],
    headline_by_id: Mapping[str, str] | None = None,
) -> LexiconEntryView:
    """Turn one raw `content/lexicon.json` entry into the plain-value view
    model `templates/lexicon_term.html` renders."""
    term = str(raw["term"])
    return LexiconEntryView(
        term=term,
        slug=slug_map[term.lower()],
        one_liner=str(raw["one_liner"]),
        deeper_html=render_deeper_html(str(raw["deeper"])),
        related=resolve_related(raw.get("related", []), slug_map),
        seen_in=resolve_seen_in(raw.get("seen_in", []), headline_by_id),
    )


def build_index_context(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Full Jinja context for `/lexicon/` (`lexicon_index.html`)."""
    slug_map = build_slug_map(entries)
    ordered = sorted_entries(entries)
    rows = [
        LexiconIndexRow(
            term=str(e["term"]),
            slug=slug_map[str(e["term"]).lower()],
            one_liner=str(e["one_liner"]),
        )
        for e in ordered
    ]
    return {"entries": rows, "total_terms": len(rows)}


def build_term_context(
    entries: Sequence[Mapping[str, Any]],
    slug: str,
    cards: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Full Jinja context for one `/lexicon/<slug>/` page
    (`lexicon_term.html`). Raises `KeyError` if `slug` doesn't resolve to
    any entry -- every real caller only ever asks for a slug it already
    derived from this same `entries` list (see `all_slugs`), so an
    unresolvable slug here means a caller bug, not user input to handle
    gracefully.

    `cards` (defaulted -- every existing call site keeps working
    unchanged) is the full set of raw Wire cards, used only to build the
    `card_id -> headline` mapping so `seen_in[]` entries render a real
    headline instead of a raw card-id slug; see `resolve_seen_in`.
    """
    slug_map = build_slug_map(entries)
    by_slug = {slug_map[str(e["term"]).lower()]: e for e in entries}
    raw = by_slug[slug]
    headline_by_id = {str(c["id"]): str(c["headline"]) for c in cards}
    return {
        "entry": build_entry_view(raw, slug_map, headline_by_id),
        "empty_seen_in_message": EMPTY_SEEN_IN_MESSAGE,
    }


def all_slugs(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every term's slug, in the same alphabetical order as the index
    page -- the full set of `/lexicon/<slug>/` routes this builder
    produces."""
    slug_map = build_slug_map(entries)
    return [slug_map[str(e["term"]).lower()] for e in sorted_entries(entries)]


def load_lexicon(path: Path = LEXICON_PATH) -> list[dict[str, Any]]:
    """Load `content/lexicon.json` (defaults to this repo's real seeded
    file)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_jinja_env(template_dir: Path = TEMPLATES_DIR) -> Environment:
    """A minimal standalone Jinja environment for this builder, mirroring
    `site/generate.py`/`site/builders/board.py`'s own `build_jinja_env()`
    (autoescape on, `StrictUndefined`, `trim_blocks`/`lstrip_blocks`).
    Deliberately not imported from `generate.py` -- this builder isn't
    wired into `generate.py`'s render pipeline yet (out of this turn's
    scope; see IMPROVEMENT_BACKLOG.md)."""
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_lexicon_index(
    entries: Sequence[Mapping[str, Any]], *, env: Environment | None = None
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_index_context(entries)
    return jinja_env.get_template("lexicon_index.html").render(**context)


def render_lexicon_term(
    entries: Sequence[Mapping[str, Any]],
    slug: str,
    *,
    env: Environment | None = None,
    cards: Sequence[Mapping[str, Any]] = (),
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_term_context(entries, slug, cards=cards)
    return jinja_env.get_template("lexicon_term.html").render(**context)


def write_lexicon_pages(
    env: Environment,
    entries: Sequence[Mapping[str, Any]],
    public_dir: Path,
    cards: Sequence[Mapping[str, Any]] = (),
) -> list[Path]:
    """Render + write `/lexicon/` (`<public_dir>/lexicon/index.html`) and
    every `/lexicon/<slug>/` term page
    (`<public_dir>/lexicon/<slug>/index.html`).

    `cards` (defaulted so every existing call site keeps working
    unchanged) is the full set of raw Wire cards -- passed straight
    through to `render_lexicon_term`/`build_term_context` so each term
    page's "Seen in" list can resolve a real headline for every
    `seen_in[]` card id instead of showing the raw machine slug."""
    public_dir = Path(public_dir)
    written: list[Path] = []

    index_html = render_lexicon_index(entries, env=env)
    index_path = public_dir / "lexicon" / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_html, encoding="utf-8")
    written.append(index_path)

    for slug in all_slugs(entries):
        term_html = render_lexicon_term(entries, slug, env=env, cards=cards)
        term_path = public_dir / "lexicon" / slug / "index.html"
        term_path.parent.mkdir(parents=True, exist_ok=True)
        term_path.write_text(term_html, encoding="utf-8")
        written.append(term_path)

    return written
