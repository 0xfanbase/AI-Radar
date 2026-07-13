"""Company profile builder (Phase 8, map-centric UI reshape).

Renders each `content/companies/<slug>.json` (the wiki-like fact-checked
profile written under `schemas/company.schema.json` -- Overview, What
they've done, Strengths, Current focus, Roadmap, each claim independently
cited) at `/companies/<slug>/`, plus a plain `/companies/` index page
listing every company with a link.

That index page is this build's own explicit accessibility/graceful-
degradation requirement: `site/builders/map.py`'s marker popovers already
link to `/companies/<slug>/` via a plain `<a class="map-marker__name">`
that works with no JS (see that module's own docstring for the
glyph-vs-name split), but the map itself is still one single page a
reader has to land on first. `/companies/` is a second, independent path
into every company profile -- a plain list, zero JS, reachable from the
masthead nav -- so no company page is reachable *only* by finding it on
the map.

Two-step build usage, mirroring every sibling builder in this directory
(`board.py`, `lexicon.py`, `map.py`):

1. Call :func:`build_company_context` (one profile page) or
   :func:`build_index_context` (the `/companies/` listing) against the
   loaded `content/companies/<slug>.json` files, `content/frontier_board
   .json` rows, and loaded cards (`[]` until an analyst run exists) to
   get the fully-computed template context.
2. Render via :func:`render_company_page`/:func:`render_companies_index`
   (each accepts a Jinja `Environment` the caller supplies, or builds its
   own minimal one via :func:`build_jinja_env` when none is given) or
   write both via :func:`write_company_pages`.

This module is deliberately self-sufficient (duplicates
`board_rows_for_company`/small formatting helpers rather than importing
`site/builders/map.py` or `board.py`) -- matching every sibling builder's
own "no reach-through into a module that doesn't call it back" convention
(see `board.py`'s module docstring for the same rationale stated
explicitly).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
CONTENT_DIR = REPO_ROOT / "content"
COMPANIES_DIR = CONTENT_DIR / "companies"
COMPANIES_INDEX_PATH = COMPANIES_DIR / "index.json"

# Same two-working-status chip mapping card.schema.json/map.py use, minus
# "corrected" -- schemas/company.schema.json's own `status` enum only has
# confirmed/reported (company profiles don't have their own corrections
# workflow wiring yet; see that schema's own field description). Reuses
# the `.chip`/`.chip--confirmed|reported` classes already shipped in
# site/static/css/components.css, so this builder introduces no new
# status-color CSS.
STATUS_CHIP_CLASS = {
    "confirmed": "chip chip--confirmed",
    "reported": "chip chip--reported",
}

# Reader-facing empty state for a company's wire-history section, scanned
# by site/tests/test_reader_copy.py's copy-lint (see that file's
# BUILDER_MODULE_NAMES list). content/cards/ does not exist yet in this
# environment (no analyst run has happened for real yet) -- this message
# is the honest, permanent behavior for any company with no matching
# card, not a placeholder that needs replacing once cards exist.
EMPTY_WIRE_HISTORY_MESSAGE = "No wire stories about this company yet."

# Reader-facing empty state for the /companies/ index, matching
# map.py's own analogous "No companies are tracked yet." wording on the
# map homepage for the same (today, real) zero-companies edge case.
EMPTY_COMPANIES_MESSAGE = "No companies are tracked yet."

# Fixed heading label for the Roadmap section, deliberately spelling out
# "what the company says" verbatim -- schemas/company.schema.json's own
# roadmap field description ("Only the company's own stated plans/
# direction ... never speculation framed as roadmap") is an
# attributed-only content rule (Hard Rule 3, claims hygiene), and this
# heading is the reader-facing signal for that rule, not just an
# implementation detail.
ROADMAP_HEADING = "Roadmap — what the company says"


# ---------------------------------------------------------------------------
# Small formatting helpers (deliberately duplicated from board.py/map.py --
# see module docstring for why).
# ---------------------------------------------------------------------------


def source_host(url: str) -> str:
    """A short, human-readable link label for a source cell -- the URL's
    host with a leading "www." stripped. Falls back to the full URL if it
    has no parseable host (defensive; every real seeded URL is a full
    https URL)."""
    host = urlsplit(url).netloc
    if not host:
        return url
    if host.startswith("www."):
        host = host[len("www.") :]
    return host


def format_context_window(context_window: int | None) -> str:
    """Render a Board row's `context_window` value as page text -- `None`
    (a lab that hasn't publicly disclosed one, valid per
    frontier_board.schema.json) becomes "not disclosed"."""
    if context_window is None:
        return "not disclosed"
    return f"{context_window:,}"


def format_modality(modality: Iterable[str]) -> str:
    """Render a Board row's `modality[]` array as page text, e.g.
    `["text", "image"]` -> "text, image"."""
    return ", ".join(modality)


def official_site_url(official_domains: Sequence[str]) -> str:
    """The company's official site link -- `https://` + the first entry
    in `official_domains[]` (schemas/company.schema.json requires at
    least one). Empty input (defensive; every real seeded company has at
    least one domain) returns "" rather than raising."""
    if not official_domains:
        return ""
    return f"https://{official_domains[0]}"


# ---------------------------------------------------------------------------
# View models -- the template only ever reads these already-computed
# fields, never branches on raw content/companies/<slug>.json data itself.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitationView:
    url: str
    outlet: str
    quote: str


@dataclass(frozen=True)
class CitedTextView:
    """One `schemas/company.schema.json` `citedText` object -- own-words
    prose plus every citation backing it."""

    text: str
    citations: tuple[CitationView, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CompanyBoardRowView:
    """One `content/frontier_board.json` row for this company, formatted
    for the company page's own (single-company, no lab column) row list."""

    model: str
    release_date: str
    modality_display: str
    context_window_display: str
    access: str
    significance: str
    source_url: str
    source_host: str
    last_verified: str


@dataclass(frozen=True)
class CompanyCardView:
    """One wire card mentioning this company, for the page's "Wire
    history" section."""

    id: str
    headline: str
    date: str
    status_label: str
    status_chip_class: str
    href: str


@dataclass(frozen=True)
class CompanyView:
    """The full `templates/company.html` render context for one company."""

    id: str
    name: str
    hq_city: str
    hq_country: str
    founded: str
    official_site_url: str
    official_site_label: str
    status: str
    status_label: str
    status_chip_class: str
    generated_at: str
    model: str
    last_verified: str
    overview: CitedTextView
    what_theyve_done: tuple[CitedTextView, ...]
    strengths: tuple[CitedTextView, ...]
    current_focus: CitedTextView
    roadmap: tuple[CitedTextView, ...]
    board_rows: tuple[CompanyBoardRowView, ...]
    cards: tuple[CompanyCardView, ...]


@dataclass(frozen=True)
class CompanyIndexRow:
    """One row of the `/companies/` plain listing page."""

    id: str
    name: str
    hq_city: str
    hq_country: str
    status_label: str
    status_chip_class: str
    href: str


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------


def build_citation_view(raw: Mapping[str, Any]) -> CitationView:
    return CitationView(
        url=str(raw["url"]), outlet=str(raw["outlet"]), quote=str(raw.get("quote", ""))
    )


def build_cited_text_view(raw: Mapping[str, Any]) -> CitedTextView:
    return CitedTextView(
        text=str(raw["text"]),
        citations=tuple(build_citation_view(c) for c in raw.get("citations", [])),
    )


def board_rows_for_company(
    company_id: str, board_rows: Iterable[Mapping[str, Any]]
) -> list[CompanyBoardRowView]:
    """Every `content/frontier_board.json` row whose `company_id` matches
    `company_id`, newest `release_date` first -- same ordering
    `map.py::board_rows_for_company` uses for a marker popover, computed
    independently here (formatted rows, not raw dicts)."""
    rows = [r for r in board_rows if r.get("company_id") == company_id]
    rows.sort(key=lambda r: str(r.get("release_date", "")), reverse=True)
    return [
        CompanyBoardRowView(
            model=str(r.get("model", "")),
            release_date=str(r.get("release_date", "")),
            modality_display=format_modality(r.get("modality", [])),
            context_window_display=format_context_window(r.get("context_window")),
            access=str(r.get("access", "")),
            significance=str(r.get("significance", "")),
            source_url=str(r.get("source_url", "")),
            source_host=source_host(str(r.get("source_url", ""))),
            last_verified=str(r.get("last_verified", "")),
        )
        for r in rows
    ]


def _card_sort_key(card: Mapping[str, Any]) -> tuple[str, str]:
    return (str(card.get("date", "")), str(card.get("generated_at", "")))


def cards_for_company(
    company_id: str, cards: Iterable[Mapping[str, Any]]
) -> list[CompanyCardView]:
    """Every wire card whose `companies[]` names `company_id`, newest
    first -- the page's full "Wire history" list (unlike
    `map.py::cards_for_company`'s popover, which caps to
    `MAX_CARDS_PER_MARKER`, this shows every matching card, no limit).
    `content/cards/` is empty in this environment (no analyst run has
    happened for real yet), so this always returns `[]` today -- written
    to work correctly the moment real cards exist rather than faking data
    now."""
    matching = [c for c in cards if company_id in (c.get("companies") or [])]
    matching.sort(key=_card_sort_key, reverse=True)
    views: list[CompanyCardView] = []
    for card in matching:
        date_str = str(card.get("date", ""))
        year_month = date_str[:7]
        status = str(card.get("status", ""))
        status_chip = {
            "confirmed": "chip chip--confirmed",
            "reported": "chip chip--reported",
            "corrected": "chip chip--corrected",
        }.get(status, "chip")
        views.append(
            CompanyCardView(
                id=str(card["id"]),
                headline=str(card.get("headline", "")),
                date=date_str,
                status_label=status.upper(),
                status_chip_class=status_chip,
                href=f"/wire/{year_month}/#card-{card['id']}",
            )
        )
    return views


def build_company_view(
    raw: Mapping[str, Any],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
) -> CompanyView:
    """Turn one raw `content/companies/<slug>.json` dict into the plain-
    value view model `templates/company.html` renders."""
    company_id = str(raw["id"])
    profile = raw["profile"]
    status = str(raw["status"])
    official_domains = raw.get("official_domains", [])
    return CompanyView(
        id=company_id,
        name=str(raw["name"]),
        hq_city=str(raw["hq_city"]),
        hq_country=str(raw["hq_country"]),
        founded=str(raw["founded"]),
        official_site_url=official_site_url(official_domains),
        official_site_label=str(official_domains[0]) if official_domains else "",
        status=status,
        status_label=status.upper(),
        status_chip_class=STATUS_CHIP_CLASS.get(status, "chip"),
        generated_at=str(raw["generated_at"]),
        model=str(raw["model"]),
        last_verified=str(raw["last_verified"]),
        overview=build_cited_text_view(profile["overview"]),
        what_theyve_done=tuple(
            build_cited_text_view(item) for item in profile.get("what_theyve_done", [])
        ),
        strengths=tuple(
            build_cited_text_view(item) for item in profile.get("strengths", [])
        ),
        current_focus=build_cited_text_view(profile["current_focus"]),
        roadmap=tuple(build_cited_text_view(item) for item in profile.get("roadmap", [])),
        board_rows=tuple(board_rows_for_company(company_id, board_rows)),
        cards=tuple(cards_for_company(company_id, cards)),
    )


def build_company_context(
    raw: Mapping[str, Any],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """The full `templates/company.html` render context for one company."""
    return {
        "company": build_company_view(raw, board_rows, cards),
        "empty_wire_history_message": EMPTY_WIRE_HISTORY_MESSAGE,
        "roadmap_heading": ROADMAP_HEADING,
    }


def sorted_companies(companies: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every full `content/companies/<slug>.json` payload, alphabetically
    by `name` (case-insensitive), matching `lexicon.py::sorted_entries`'s
    own convention."""
    return sorted(companies, key=lambda c: str(c.get("name", "")).lower())


def build_index_context(companies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Full Jinja context for `/companies/` (`company_index.html`)."""
    ordered = sorted_companies(companies)
    rows = [
        CompanyIndexRow(
            id=str(c["id"]),
            name=str(c["name"]),
            hq_city=str(c.get("hq_city", "")),
            hq_country=str(c.get("hq_country", "")),
            status_label=str(c.get("status", "")).upper(),
            status_chip_class=STATUS_CHIP_CLASS.get(str(c.get("status", "")), "chip"),
            href=f"/companies/{c['id']}/",
        )
        for c in ordered
    ]
    return {
        "companies": rows,
        "total_companies": len(rows),
        "empty_companies_message": EMPTY_COMPANIES_MESSAGE,
    }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_companies(companies_dir: Path = COMPANIES_DIR) -> list[dict[str, Any]]:
    """Load every real `content/companies/<slug>.json` full profile
    (excluding the generated `index.json` summary manifest). Returns `[]`
    if the directory doesn't exist yet -- true tolerance every sibling
    "load every X" loader in this codebase (`site/generate.py::
    load_cards`) already applies."""
    if not companies_dir.is_dir():
        return []
    companies: list[dict[str, Any]] = []
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        with path.open("r", encoding="utf-8") as fh:
            companies.append(json.load(fh))
    return companies


def load_company(slug: str, companies_dir: Path = COMPANIES_DIR) -> dict[str, Any]:
    """Load one `content/companies/<slug>.json` full profile by id."""
    path = companies_dir / f"{slug}.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Render / write
# ---------------------------------------------------------------------------


def build_jinja_env(template_dir: Path = TEMPLATES_DIR) -> Environment:
    """A minimal standalone Jinja environment for this builder, mirroring
    every sibling builder's own `build_jinja_env()` (autoescape on,
    `StrictUndefined`, `trim_blocks`/`lstrip_blocks`)."""
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_company_page(
    raw: Mapping[str, Any],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
    *,
    env: Environment | None = None,
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_company_context(raw, board_rows, cards)
    return jinja_env.get_template("company.html").render(**context)


def render_companies_index(
    companies: Sequence[Mapping[str, Any]], *, env: Environment | None = None
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_index_context(companies)
    return jinja_env.get_template("company_index.html").render(**context)


def write_company_pages(
    env: Environment,
    companies: Sequence[Mapping[str, Any]],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
    public_dir: Path,
) -> list[Path]:
    """Render + write `/companies/` (`<public_dir>/companies/index.html`)
    and every `/companies/<slug>/` profile page
    (`<public_dir>/companies/<slug>/index.html`). `companies` is the full
    set of loaded `content/companies/<slug>.json` profiles (see
    :func:`load_companies`), not the summary `content/companies/index
    .json` list `map.py` uses for markers -- a company profile page needs
    every `profile.*` field the summary index doesn't carry."""
    public_dir = Path(public_dir)
    board_rows = list(board_rows)
    cards = list(cards)
    written: list[Path] = []

    index_html = render_companies_index(companies, env=env)
    index_path = public_dir / "companies" / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_html, encoding="utf-8")
    written.append(index_path)

    for raw in companies:
        company_html = render_company_page(raw, board_rows, cards, env=env)
        company_path = public_dir / "companies" / str(raw["id"]) / "index.html"
        company_path.parent.mkdir(parents=True, exist_ok=True)
        company_path.write_text(company_html, encoding="utf-8")
        written.append(company_path)

    return written
