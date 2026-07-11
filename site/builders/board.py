"""Frontier Board builder (Phase 4, build-plan section 5).

Reads the seeded ``content/frontier_board.json`` rows (13 real,
live-cited rows as of this commit -- Anthropic/OpenAI/Google
DeepMind/Meta/xAI/Mistral in the US region, DeepSeek/Alibaba
Qwen/Moonshot AI/Zhipu AI/ByteDance in China, Ai2/NVIDIA in
open-weights) and renders the ``/board/`` "observatory status wall" page:
one ``<table>`` per *present* region (US -> China -> open-weights, a
region with zero rows is simply omitted, matching this build stage's
established "don't render a broken-looking empty section" convention --
see ``site/generate.py``'s ``load_cards()`` docstring for the sibling
precedent for cards), with a heading immediately before each table and
the table associated with that heading via ``aria-labelledby`` for
screen readers.

Pulse eligibility -- the small dedicated dot rendered next to a row's
Model cell for anything verified within the last
:data:`PULSE_WINDOW_DAYS` days -- is computed by :func:`is_pulse_eligible`
purely from a ``today`` value **the caller supplies explicitly**. This
module never calls ``date.today()``/``datetime.now()`` itself anywhere,
so pulse eligibility stays deterministic and unit-testable against a
synthetic "today" rather than depending on the wall clock at import or
render time (per this turn's explicit instruction).

Two-step build usage (mirrors ``site/lib/linkify.py``'s own "two-step"
convention):

1. Call :func:`build_context` once, passing the loaded
   ``content/frontier_board.json`` array and a ``datetime.date`` for
   "today", to get the fully-computed template context (grouped,
   ordered, pulse-flagged rows).
2. Pass that context into ``templates/board.html`` (directly via
   :func:`render_board_page`, which builds its own minimal Jinja
   environment, or -- once a future commit wires this builder into
   ``site/generate.py``'s shared pipeline -- via that module's own
   environment instead; see IMPROVEMENT_BACKLOG.md for why this commit
   does not do that wiring itself).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

SITE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
LIB_DIR = SITE_DIR / "lib"
CONTENT_DIR = REPO_ROOT / "content"
FRONTIER_BOARD_PATH = CONTENT_DIR / "frontier_board.json"


def _load_module_by_path(name: str, path: Path):
    """Load a module from an explicit file path, registering it in
    `sys.modules` *before* `exec_module` runs.

    Matches the convention every other Phase 4 module/test already uses
    (`site/builders/wire.py`, `site/generate.py`'s own test,
    `site/tests/test_linkify.py`, `site/tests/test_svg_sparkline.py`):
    `site/` is deliberately never turned into an importable package (it
    would shadow the stdlib `site` module for anything else sharing the
    interpreter's `sys.path`), so every cross-file reference within
    `site/` loads its target by path instead of via `import
    site.lib.linkify`. The early `sys.modules` registration is required
    because `linkify.py` uses `@dataclass` together with `from __future__
    import annotations`; dataclasses' own annotation resolution looks up
    `sys.modules[cls.__module__]` and raises if that key isn't populated
    yet.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


linkify = _load_module_by_path("frontier_wire_site_lib_linkify", LIB_DIR / "linkify.py")

# A row is "pulse-eligible" -- gets the small dot next to its Model cell --
# when its last_verified date is within this many days at-or-before
# "today". Matches the build plan's "Board pulse-eligibility from
# last_verified (<=7 days)" / CLAUDE.md's "7-day pulse indicator" wording.
PULSE_WINDOW_DAYS = 7

# Fixed render order for the three regions the schema's `region` enum
# defines (schemas/frontier_board.schema.json), per the build plan's
# "/board/ (one <table> per region...)" wording and this turn's explicit
# "US, CHINA, OPEN WEIGHTS" heading order.
REGION_ORDER: tuple[str, ...] = ("US", "China", "open-weights")

# Human-readable heading text per region key. "open-weights" (the
# schema's lowercase enum value) reads as "Open Weights" as a page
# heading; US/China are already display-ready.
REGION_HEADINGS: dict[str, str] = {
    "US": "US",
    "China": "China",
    "open-weights": "Open Weights",
}


def parse_iso_date(value: str) -> date:
    """Parse a `YYYY-MM-DD` string (frontier_board.schema.json's
    `format: date` fields) into a `datetime.date`."""
    return date.fromisoformat(value)


def is_pulse_eligible(
    last_verified: str, today: date, window_days: int = PULSE_WINDOW_DAYS
) -> bool:
    """True if `last_verified` (an ISO `YYYY-MM-DD` string) falls within
    `window_days` days at-or-before `today`.

    `today` is always supplied by the caller -- this function never reads
    the wall clock itself, so it stays a pure, unit-testable function of
    its two/three arguments (per this turn's explicit instruction).

    A `last_verified` date *after* `today` (clock skew, malformed seed
    data) is never pulse-eligible -- only genuinely-recent past
    verification lights the dot; a negative delta is clamped to
    ineligible rather than wrapping around via `abs()`.

    A missing (empty/`None`) or unparseable `last_verified` -- content
    that should already have been rejected by schema validation upstream,
    but this module never assumes a caller went through that gate (its
    own tests call it directly with synthetic dicts) -- is likewise never
    pulse-eligible rather than raising: one row's bad data must not crash
    the whole Board page, same "bad seed data" tolerance already applied
    to a future-dated `last_verified` above.
    """
    if not last_verified:
        return False
    try:
        verified = parse_iso_date(last_verified)
    except ValueError:
        return False
    delta_days = (today - verified).days
    return 0 <= delta_days <= window_days


def source_host(url: str) -> str:
    """A short, human-readable link label for a Source cell -- the URL's
    host with a leading "www." stripped (e.g.
    "https://platform.claude.com/docs/..." -> "platform.claude.com").
    Falls back to the full URL if it has no parseable host (defensive;
    every real seeded `source_url` is a full https URL)."""
    host = urlsplit(url).netloc
    if not host:
        return url
    if host.startswith("www."):
        host = host[len("www.") :]
    return host


def format_context_window(context_window: int | None) -> str:
    """Render a `context_window` value (an int token count, or `None`
    when a lab hasn't publicly disclosed one -- both valid per
    `frontier_board.schema.json`) as Board cell text."""
    if context_window is None:
        return "not disclosed"
    return f"{context_window:,}"


def format_modality(modality: Iterable[str]) -> str:
    """Render a row's `modality[]` array as Board cell text, e.g.
    `["text", "image"]` -> "text, image"."""
    return ", ".join(modality)


@dataclass(frozen=True)
class BoardRow:
    """One rendered Frontier Board row, computed once from a raw
    `content/frontier_board.json` entry plus the build's `today` value --
    the template only ever reads these already-computed fields, it never
    branches on raw data itself."""

    lab: str
    model: str
    release_date: str
    modality_display: str
    context_window_display: str
    access: str
    significance: str
    significance_html: Markup
    source_url: str
    source_host: str
    last_verified: str
    pulse_eligible: bool


@dataclass(frozen=True)
class BoardRegion:
    """One region's table: a heading (with a stable `id` the table's
    `aria-labelledby` references) plus its ordered rows."""

    key: str
    heading: str
    heading_id: str
    table_id: str
    rows: tuple[BoardRow, ...] = field(default_factory=tuple)


def _row_region(raw: Mapping[str, Any]) -> str:
    return str(raw["region"])


def _to_board_row(
    raw: Mapping[str, Any],
    today: date,
    terms: Sequence[str] = (),
    slug_map: Mapping[str, str] | None = None,
) -> BoardRow:
    # `.get(...)` rather than `raw["last_verified"]`: a row missing this
    # required field entirely should already have been rejected by
    # schema validation upstream, but this module is deliberately
    # self-sufficient (see module docstring) and must not crash on bad
    # input either way -- an absent value becomes `""`, which
    # `is_pulse_eligible` already treats as never-eligible.
    last_verified_raw = raw.get("last_verified")
    last_verified = str(last_verified_raw) if last_verified_raw is not None else ""
    significance = str(raw["significance"])
    significance_html = linkify.linkify(
        significance, list(terms), slug_map or {}
    ).html
    return BoardRow(
        lab=str(raw["lab"]),
        model=str(raw["model"]),
        release_date=str(raw["release_date"]),
        modality_display=format_modality(raw["modality"]),
        context_window_display=format_context_window(raw.get("context_window")),
        access=str(raw["access"]),
        significance=significance,
        significance_html=significance_html,
        source_url=str(raw["source_url"]),
        source_host=source_host(str(raw["source_url"])),
        last_verified=last_verified,
        pulse_eligible=is_pulse_eligible(last_verified, today),
    )


def build_regions(
    board_rows: Sequence[Mapping[str, Any]],
    today: date,
    lexicon_entries: Iterable[Mapping[str, Any]] = (),
) -> list[BoardRegion]:
    """Group+order the loaded `content/frontier_board.json` rows into
    per-region row-card lists.

    Regions render in the fixed `REGION_ORDER` (US -> China ->
    open-weights); any region key present in the data but not in that
    tuple (schema-invalid today, but handled defensively rather than
    raising) is appended afterward in sorted order. A region entirely
    absent from `board_rows` -- including the degenerate `board_rows ==
    []` case, matching this build's "content/cards/ is currently EMPTY"
    empty-collection-handling precedent elsewhere in the site generator
    -- is simply omitted: ONE row-card list per *present* region, never a
    placeholder for a region with zero rows.

    `lexicon_entries` (defaulted to `()` so every existing caller keeps
    working unchanged) drives auto-linking of each row's `significance`
    prose, exactly like `site/builders/wire.py` already does for card
    bodies: `terms`/`slug_map` are computed once here, not once per row,
    then threaded into every `_to_board_row` call.
    """
    lexicon_entries = list(lexicon_entries)
    terms = [str(e["term"]) for e in lexicon_entries]
    slug_map = linkify.build_slug_map(lexicon_entries)

    by_region: dict[str, list[BoardRow]] = {}
    for raw in board_rows:
        by_region.setdefault(_row_region(raw), []).append(
            _to_board_row(raw, today, terms=terms, slug_map=slug_map)
        )

    ordered_keys = list(REGION_ORDER) + sorted(
        key for key in by_region if key not in REGION_ORDER
    )

    regions: list[BoardRegion] = []
    for key in ordered_keys:
        rows = by_region.get(key)
        if not rows:
            continue
        slug = key.lower().replace(" ", "-")
        regions.append(
            BoardRegion(
                key=key,
                heading=REGION_HEADINGS.get(key, key),
                heading_id=f"board-region-{slug}-heading",
                table_id=f"board-region-{slug}-table",
                rows=tuple(rows),
            )
        )
    return regions


def build_context(
    board_rows: Sequence[Mapping[str, Any]],
    today: date,
    lexicon_entries: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The full `templates/board.html` render context for a given set of
    loaded `content/frontier_board.json` rows and an explicit `today`.

    `lexicon_entries` (defaulted to `()`) is passed straight through to
    :func:`build_regions` -- see that function's docstring for the
    auto-linking behavior.
    """
    regions = build_regions(board_rows, today, lexicon_entries=lexicon_entries)
    return {
        "regions": regions,
        "today": today.isoformat(),
        "total_rows": sum(len(region.rows) for region in regions),
        "pulse_window_days": PULSE_WINDOW_DAYS,
    }


def load_frontier_board(path: Path = FRONTIER_BOARD_PATH) -> list[dict[str, Any]]:
    """Load `content/frontier_board.json` (defaults to this repo's real
    seeded file)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_jinja_env(template_dir: Path = TEMPLATES_DIR) -> Environment:
    """A minimal standalone Jinja environment for this builder, mirroring
    `site/generate.py`'s own `build_jinja_env()` (autoescape on,
    `StrictUndefined`, `trim_blocks`/`lstrip_blocks`). Deliberately not
    imported from `generate.py` -- this builder isn't wired into
    `generate.py`'s render pipeline yet (out of this turn's scope; see
    IMPROVEMENT_BACKLOG.md), so it stays fully self-sufficient rather than
    reaching into a module that doesn't call it back."""
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_board_page(
    board_rows: Sequence[Mapping[str, Any]],
    today: date,
    *,
    env: Environment | None = None,
    lexicon_entries: Iterable[Mapping[str, Any]] = (),
) -> str:
    """Render the full `/board/` page HTML for `board_rows` (a loaded
    `content/frontier_board.json` array) as of `today`.

    `today` is always the caller's explicit value (never this module's
    own wall-clock read) -- see :func:`is_pulse_eligible`. `lexicon_entries`
    (defaulted to `()` so every pre-existing call site keeps rendering the
    same raw-prose output) is passed through to :func:`build_context` to
    auto-link Significance prose against `content/lexicon.json`, matching
    `site/builders/wire.py`'s card-body linkify behavior.
    """
    jinja_env = env or build_jinja_env()
    template = jinja_env.get_template("board.html")
    context = build_context(board_rows, today, lexicon_entries=lexicon_entries)
    return template.render(**context)


def write_board_page(
    env: Environment,
    board_rows: Sequence[Mapping[str, Any]],
    today: date,
    public_dir: Path,
    lexicon_entries: Iterable[Mapping[str, Any]] = (),
) -> Path:
    """Render + write `/board/` (`<public_dir>/board/index.html`).

    Added during the Phase 4 integration turn (`site/generate.py` wiring
    every builder together) so this module matches the `write_<page>_page(
    env, ..., public_dir)` convention every sibling builder
    (`wire.py`/`lexicon.py`/`primer.py`/`moving.py`/`method.py`/
    `corrections.py`/`about.py`) already exposes -- this module was the one
    built without it, since it was written concurrently before that
    convention was established elsewhere. See IMPROVEMENT_BACKLOG.md.

    `lexicon_entries` (defaulted to `()`) is passed straight through to
    :func:`render_board_page`.
    """
    html = render_board_page(board_rows, today, env=env, lexicon_entries=lexicon_entries)
    path = Path(public_dir) / "board" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
