"""World map builder (Phase 7, map-centric UI reshape) -- the new
homepage (`/`).

Renders a build-time inline SVG world map (country outlines from the
vendored Natural Earth 110m admin-0 countries GeoJSON, see
`site/static/geo/README.md` for the exact source/license) with one
marker per `content/companies/index.json` entry, projected with a plain
equirectangular lon/lat -> x/y transform (:func:`project`) -- pure
Python math, no new pip dependency, matching every sibling builder's
architecture in this directory.

Two-step build usage, mirroring `site/builders/board.py`'s own
documented convention:

1. Call :func:`build_context` once, passing the loaded
   `content/companies/index.json` array, `content/frontier_board.json`
   rows, and loaded cards (`[]` until an analyst run exists), to get the
   fully-computed template context (projected country paths, positioned
   markers, board rows + wire-story drill-down per marker).
2. Pass that context into `templates/map_index.html` (directly via
   :func:`render_map_page`, or via `site/generate.py`'s own shared
   environment through :func:`write_map_page`).

Marker interaction split (per the approved plan's View 2/View 3
distinction, structural so it holds even with JavaScript disabled): the
marker GLYPH (`.map-marker__glyph`, a `<button>`) is the only element
`site/static/js/map.js` attaches click/expand behavior to; the company
NAME (`.map-marker__name`, a plain `<a href="/companies/<slug>/">`) is a
real link regardless of script state -- two separate elements in the
rendered markup, never one element serving both jobs. `/companies/<id>/`
does not resolve to a real page yet (Phase 8 builds `company.py` +
`templates/company.html`) -- linking to it now is deliberate, per this
build's own brief, not a bug.

Map-rebuild note (owner follow-on to Phases 6-9): the map canvas itself
is now a bigger, full-bleed, pannable/zoomable surface, but that is
entirely a `site/templates/map_index.html` CSS layout change plus
`site/static/js/map.js` client-side behavior -- this module's own output
(projected country paths, marker percentage positions, popover data) is
unchanged in shape, with the single addition of `MarkerView.anchor_right`
below (a deterministic, build-time left/right popover-anchor flip, one
part of the mobile-popover-overflow fix; see map_index.html's CSS
comment for the rest of that fix).

Dense HQ clusters (SF Bay Area; Beijing; Hangzhou) are handled with
fixed, hand-computed per-marker pixel offsets (`MARKER_OFFSET_PX`) for
this build's real ~13-company registry -- not a runtime clustering
library. If the registry ever grows meaningfully past that size, this
table needs a human pass, the same way the reputable-outlet table or the
marker-offset table for any other small, hand-curated list in this repo
does (see CLAUDE.md's own "named explicitly rather than left to
discretion" precedent for the outlet table).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
STATIC_DIR = SITE_DIR / "static"
CONTENT_DIR = REPO_ROOT / "content"

GEO_PATH = STATIC_DIR / "geo" / "ne_110m_admin_0_countries.geojson"
COMPANIES_INDEX_PATH = CONTENT_DIR / "companies" / "index.json"

# The SVG viewBox this build's equirectangular projection targets. A 1.92:1
# aspect ratio (960x500) matches the conventional world-map viewBox this
# projection family is usually rendered at; every marker offset in
# MARKER_OFFSET_PX below is hand-tuned against exactly these numbers, so
# changing either constant means re-checking every offset by eye.
MAP_WIDTH = 960
MAP_HEIGHT = 500

# How many of a company's most-recent Frontier Board rows a popover shows.
MAX_BOARD_ROWS_PER_MARKER = 4

# How many of a company's most-recent wire cards a popover shows -- per
# the approved plan's View 2 spec ("Latest <=3 cards").
MAX_CARDS_PER_MARKER = 3

# Reader-facing empty state for a marker popover's news-drill-down slot,
# scanned by site/tests/test_reader_copy.py's copy-lint (see that file's
# BUILDER_MODULE_NAMES list). content/cards/ does not exist yet in this
# environment (no analyst run has happened for real yet) -- this message
# is the honest, permanent behavior for any company with no matching
# card, not a placeholder that needs replacing once cards exist.
EMPTY_CARDS_MESSAGE = "No recent wire stories."

# Same three-entry mapping as `site/builders/wire.py`'s own
# `STATUS_CHIP_CLASS` (duplicated rather than cross-imported: this
# module deliberately stays self-sufficient, matching every sibling
# builder's own "no reach-through into a module that doesn't call it
# back" convention -- see `site/builders/board.py`'s module docstring
# for the same rationale stated explicitly). Reuses the
# `.chip`/`.chip--confirmed|reported|corrected` classes already shipped
# in `site/static/css/components.css`, so this builder introduces no
# new status-color CSS.
STATUS_CHIP_CLASS = {
    "confirmed": "chip chip--confirmed",
    "reported": "chip chip--reported",
    "corrected": "chip chip--corrected",
}

# Fixed, deterministic per-marker pixel offsets (dx, dy), applied AFTER
# projection, hand-picked once against this build's real 13-row
# `content/companies/index.json` (verified 2026-07-13). Three real HQ
# clusters exist in the seeded data -- SF Bay Area (anthropic + openai
# share the exact same geocoded point; meta-ai/xai/nvidia sit within a
# few real-world kilometers, indistinguishable at this map's world
# scale), Hangzhou (deepseek + alibaba-qwen share the exact same
# point), and Beijing (moonshot-ai + zhipu-ai + bytedance-seed share the
# exact same point) -- so every marker in those three clusters gets a
# hand-picked offset; the remaining, genuinely isolated markers
# (google-deepmind/London, mistral/Paris, ai2/Seattle) get (0, 0).
# A company id absent from this table (e.g. the registry grows later)
# also defaults to (0, 0) via :func:`marker_offset` -- never a crash.
MARKER_OFFSET_PX: dict[str, tuple[float, float]] = {
    # SF Bay Area cluster -- stacked in a vertical column, 16px apart
    # (enough to clear one line of label text at this build's marker
    # font size without needing to measure text width at build time).
    "anthropic": (0.0, -32.0),
    "openai": (0.0, -16.0),
    "meta-ai": (0.0, 0.0),
    "xai": (0.0, 16.0),
    "nvidia": (0.0, 32.0),
    # Hangzhou cluster -- side by side.
    "deepseek": (-18.0, -8.0),
    "alibaba-qwen": (18.0, 8.0),
    # Beijing cluster -- a small triangle.
    "moonshot-ai": (0.0, -22.0),
    "zhipu-ai": (-20.0, 12.0),
    "bytedance-seed": (20.0, 12.0),
    # Isolated markers -- no real-world neighbor close enough at this
    # map's scale to need an offset.
    "google-deepmind": (0.0, 0.0),
    "mistral": (0.0, 0.0),
    "ai2": (0.0, 0.0),
}


def marker_offset(company_id: str) -> tuple[float, float]:
    """The hand-picked (dx, dy) pixel offset for `company_id`, or
    `(0.0, 0.0)` for any id not in :data:`MARKER_OFFSET_PX` (a company
    added to the registry without a corresponding offset entry renders
    at its true projected point rather than crashing the build)."""
    return MARKER_OFFSET_PX.get(company_id, (0.0, 0.0))


def project(
    lon: float, lat: float, width: int = MAP_WIDTH, height: int = MAP_HEIGHT
) -> tuple[float, float]:
    """Plain equirectangular projection: longitude maps linearly across
    `width` (-180 -> 0, +180 -> width), latitude maps linearly down
    `height` (+90 -> 0, -90 -> height, so north is up). Pure arithmetic,
    no trigonometry, no new pip dependency -- exactly the "simple
    pure-Python equirectangular... math, no new pip dependency" this
    build was scoped to. See `site/static/geo/README.md` for this
    projection's known area-distortion tradeoff toward the poles."""
    x = (lon + 180.0) / 360.0 * width
    y = (90.0 - lat) / 180.0 * height
    return x, y


def load_geojson(path: Path = GEO_PATH) -> dict[str, Any]:
    """Load the vendored Natural Earth 110m countries GeoJSON (see
    `site/static/geo/README.md`)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _ring_to_path_d(
    ring: Sequence[Sequence[float]], width: int, height: int
) -> str:
    """One GeoJSON linear ring (a closed list of [lon, lat] pairs) ->
    one SVG subpath ("M x,y L x,y ... Z"). An empty ring yields ""."""
    if not ring:
        return ""
    points = [project(pt[0], pt[1], width, height) for pt in ring]
    first_x, first_y = points[0]
    parts = [f"M{first_x:.2f},{first_y:.2f}"]
    for x, y in points[1:]:
        parts.append(f"L{x:.2f},{y:.2f}")
    parts.append("Z")
    return " ".join(parts)


def geometry_to_path_d(
    geometry: Mapping[str, Any], width: int = MAP_WIDTH, height: int = MAP_HEIGHT
) -> str:
    """One GeoJSON `Polygon`/`MultiPolygon` geometry -> one SVG `d`
    attribute string covering every ring (exterior + holes) as its own
    "M...Z" subpath. Holes render correctly regardless of the source
    data's ring winding order because `templates/map_index.html` applies
    `fill-rule: evenodd` to every country path -- evenodd only counts
    ray-crossings, so it doesn't depend on winding direction the way
    `fill-rule: nonzero` would. An unrecognized geometry `type` (schema
    says GeoJSON country layers are always Polygon/MultiPolygon, but
    this stays defensive rather than raising) returns ""."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    parts: list[str] = []
    if gtype == "Polygon":
        for ring in coords:
            d = _ring_to_path_d(ring, width, height)
            if d:
                parts.append(d)
    elif gtype == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                d = _ring_to_path_d(ring, width, height)
                if d:
                    parts.append(d)
    return " ".join(parts)


@dataclass(frozen=True)
class CountryPath:
    """One rendered country outline: an SVG `d` string plus its display
    name (used only as a `data-name` attribute for debugging/hover
    title, never required for correctness)."""

    name: str
    iso_a2: str
    d: str


def build_country_paths(
    geo: Mapping[str, Any], width: int = MAP_WIDTH, height: int = MAP_HEIGHT
) -> list[CountryPath]:
    """Every feature in the vendored GeoJSON's `features[]` -> one
    :class:`CountryPath`. A feature whose geometry projects to an empty
    `d` (defensive; doesn't happen in the real vendored file) is
    skipped rather than rendered as a broken empty `<path>`."""
    paths: list[CountryPath] = []
    for feature in geo.get("features", []):
        props = feature.get("properties", {}) or {}
        d = geometry_to_path_d(feature.get("geometry", {}), width, height)
        if not d:
            continue
        paths.append(
            CountryPath(
                name=str(props.get("name") or ""),
                iso_a2=str(props.get("iso_a2") or ""),
                d=d,
            )
        )
    return paths


def load_companies_index(path: Path = COMPANIES_INDEX_PATH) -> list[dict[str, Any]]:
    """Load `content/companies/index.json`'s `companies[]` array (the
    map's marker list -- id/name/hq_country/hq_city/hq_lat/hq_lng/status
    only; the fuller per-company profile files,
    `content/companies/<id>.json`, are Phase 8's company-profile-page
    concern and are not read here). Returns `[]` if the file doesn't
    exist -- this module tolerates a company-less build the same way
    every sibling builder tolerates an empty cards/lexicon input."""
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return list(payload.get("companies", []))


def board_rows_for_company(
    company_id: str, board_rows: Iterable[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """Every `content/frontier_board.json` row whose `company_id`
    matches, newest `release_date` first."""
    rows = [r for r in board_rows if r.get("company_id") == company_id]
    return sorted(rows, key=lambda r: str(r.get("release_date", "")), reverse=True)


def has_open_weights(
    company_id: str, board_rows: Iterable[Mapping[str, Any]]
) -> bool:
    """True if `company_id` has any current Board row with
    `access: "open-weights"` -- the open-weights badge/filter's source
    of truth, per the approved plan's "derived from whether a lab has
    any current Board row with access: open-weights" design (never a
    field stored directly on the company record itself)."""
    return any(
        r.get("company_id") == company_id and r.get("access") == "open-weights"
        for r in board_rows
    )


def _card_sort_key(card: Mapping[str, Any]) -> tuple[str, str]:
    return (str(card.get("date", "")), str(card.get("generated_at", "")))


def cards_for_company(
    company_id: str, cards: Iterable[Mapping[str, Any]], limit: int = MAX_CARDS_PER_MARKER
) -> list[dict[str, Any]]:
    """The `limit` most-recent cards (newest first) whose `companies[]`
    names `company_id`, each turned into the small plain-value view
    `templates/map_index.html` renders in a popover's news-drill-down
    slot. `content/cards/` is empty in this environment (no analyst run
    has happened for real yet), so this always returns `[]` today --
    written to work correctly the moment real cards exist rather than
    faking data now (per this build's own brief).

    Card pages don't exist as their own standalone route on this site
    (see `site/templates/card.html`'s own docstring: cards render
    inline inside the Wire index/month archive, each with a stable
    `id="card-<id>"` anchor) -- so a card's link here is a fragment
    link into its month's archive page,
    `/wire/<YYYY-MM>/#card-<id>`, matching that existing convention
    rather than inventing a second one.
    """
    matching = [c for c in cards if company_id in c.get("companies", [])]
    matching.sort(key=_card_sort_key, reverse=True)
    views: list[dict[str, Any]] = []
    for card in matching[:limit]:
        date_str = str(card.get("date", ""))
        year_month = date_str[:7]
        status = str(card.get("status", ""))
        views.append(
            {
                "id": card["id"],
                "headline": card.get("headline", ""),
                "date": date_str,
                "status_label": status.upper(),
                "status_chip_class": STATUS_CHIP_CLASS.get(status, "chip"),
                "href": f"/wire/{year_month}/#card-{card['id']}",
            }
        )
    return views


@dataclass(frozen=True)
class BoardRowView:
    model: str
    release_date: str
    access: str
    last_verified: str


@dataclass(frozen=True)
class MarkerView:
    """One rendered map marker -- a company positioned on the map plus
    every piece of data its popover shows, all pre-computed at build
    time (the popover is populated from data embedded in the page, per
    the approved plan's "no fetches" View 2 spec)."""

    id: str
    name: str
    profile_href: str
    hq_city: str
    hq_country: str
    pct_x: float
    pct_y: float
    open_weights: bool
    board_rows: tuple[BoardRowView, ...]
    cards: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    empty_cards_message: str = EMPTY_CARDS_MESSAGE
    anchor_right: bool = False


def build_markers(
    companies: Iterable[Mapping[str, Any]],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
    width: int = MAP_WIDTH,
    height: int = MAP_HEIGHT,
) -> list[MarkerView]:
    """Every `content/companies/index.json` entry with real `hq_lat`/
    `hq_lng` -> one positioned, popover-ready :class:`MarkerView`. A
    company entry missing either coordinate is skipped (defensive --
    every real seeded row has both, per `schemas/company.schema.json`'s
    required fields, but this module stays tolerant of malformed input
    the same way every sibling builder does)."""
    board_rows = list(board_rows)
    cards = list(cards)
    markers: list[MarkerView] = []
    for company in companies:
        company_id = str(company.get("id", ""))
        lat = company.get("hq_lat")
        lng = company.get("hq_lng")
        if not company_id or lat is None or lng is None:
            continue
        x, y = project(float(lng), float(lat), width, height)
        dx, dy = marker_offset(company_id)
        px, py = x + dx, y + dy
        rows = board_rows_for_company(company_id, board_rows)[:MAX_BOARD_ROWS_PER_MARKER]
        board_views = tuple(
            BoardRowView(
                model=str(r.get("model", "")),
                release_date=str(r.get("release_date", "")),
                access=str(r.get("access", "")),
                last_verified=str(r.get("last_verified", "")),
            )
            for r in rows
        )
        pct_x = round(px / width * 100, 3)
        markers.append(
            MarkerView(
                id=company_id,
                name=str(company.get("name", "")),
                profile_href=f"/companies/{company_id}/",
                hq_city=str(company.get("hq_city", "")),
                hq_country=str(company.get("hq_country", "")),
                pct_x=pct_x,
                pct_y=round(py / height * 100, 3),
                open_weights=has_open_weights(company_id, board_rows),
                board_rows=board_views,
                cards=tuple(cards_for_company(company_id, cards)),
                # A marker in the right half of the (now full-bleed, much
                # wider) map opens its popover extending LEFTWARD instead
                # of the default rightward-from-marker anchor, so a
                # marker near the map's right edge can never push its
                # popover off the page's right edge / force horizontal
                # scroll -- a deterministic, build-time half of the
                # narrow-viewport popover-overflow fix described in
                # templates/map_index.html's own CSS comment (PROGRESS.md
                # entry for the map rebuild has the full rationale).
                anchor_right=pct_x > 50.0,
            )
        )
    return markers


def build_context(
    companies: Iterable[Mapping[str, Any]],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
    geo: Mapping[str, Any] | None = None,
    masthead_sparklines: Any = None,
) -> dict[str, Any]:
    """The full `templates/map_index.html` render context. `geo`
    defaults to the real vendored file (:func:`load_geojson`) -- tests
    pass a small synthetic FeatureCollection instead, so this stays a
    pure function of its inputs rather than always touching disk."""
    if geo is None:
        geo = load_geojson()
    country_paths = build_country_paths(geo)
    markers = build_markers(companies, board_rows, cards)
    return {
        "country_paths": country_paths,
        "markers": markers,
        "map_width": MAP_WIDTH,
        "map_height": MAP_HEIGHT,
        "open_weights_count": sum(1 for m in markers if m.open_weights),
        "total_markers": len(markers),
        "masthead_sparklines": masthead_sparklines or [],
    }


def build_jinja_env(template_dir: Path = TEMPLATES_DIR) -> Environment:
    """A minimal standalone Jinja environment for this builder, matching
    `site/generate.py`'s / `site/builders/board.py`'s own
    `build_jinja_env()` configuration."""
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_map_page(
    companies: Iterable[Mapping[str, Any]],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
    *,
    env: Environment | None = None,
    geo: Mapping[str, Any] | None = None,
    masthead_sparklines: Any = None,
) -> str:
    """Render the full `/` (map homepage) page HTML."""
    jinja_env = env or build_jinja_env()
    template = jinja_env.get_template("map_index.html")
    context = build_context(
        companies, board_rows, cards, geo=geo, masthead_sparklines=masthead_sparklines
    )
    return template.render(**context)


def write_map_page(
    env: Environment,
    companies: Iterable[Mapping[str, Any]],
    board_rows: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
    public_dir: Path,
    geo: Mapping[str, Any] | None = None,
    masthead_sparklines: Any = None,
) -> Path:
    """Render + write `/` (`<public_dir>/index.html`) -- the map
    homepage. Matches every sibling builder's `write_<page>_page(env,
    ..., public_dir)` convention."""
    html = render_map_page(
        companies, board_rows, cards, env=env, geo=geo, masthead_sparklines=masthead_sparklines
    )
    path = Path(public_dir) / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
