"""What's Moving builder (Phase 4, build-plan section 5) -- the site's
topic-velocity strip, plus the thin, site-wide masthead sparkline partial.

Reads the pure-code, no-AI ``data/whats_moving.json`` snapshot (see
``schemas/whats_moving.schema.json`` / ``IMPROVEMENT_BACKLOG.md`` for its
shape -- nine fixed topic tags, each carrying exactly 7 daily HN-mention
counts oldest-first and a precomputed ``accelerating``/``cooling``/``flat``
trend label) and renders:

* ``/moving/`` -- one row per topic, each with its own inline SVG
  sparkline (``site/lib/svg_sparkline.py``, already implemented -- this
  module never draws its own chart, only calls that one).
* the thin, site-wide masthead sparkline strip
  (``templates/_masthead_moving_strip.html``), which ``templates/base.html``
  now conditionally ``{% include %}``s on every page once a caller's
  render context carries a non-empty ``masthead_sparklines`` list. Every
  other already-committed Phase 4
  builder (``board.py``/``lexicon.py``/``primer.py``/``wire.py``) never
  sets that variable, so ``base.html``'s own
  ``{% if masthead_sparklines is defined and masthead_sparklines %}``
  guard keeps every one of their already-tested renders byte-for-byte
  unchanged -- the strip only ever appears on a page whose own builder
  opts in (today, that's only this module's own ``/moving/`` page, via
  :func:`build_moving_context`). Wiring every other builder up to pass
  this same context is a future ``site/generate.py`` integration turn's
  job, not this one's -- see ``IMPROVEMENT_BACKLOG.md``.

Two-step build usage (mirrors ``site/builders/board.py``'s own
convention):

1. Call :func:`build_moving_context` once, passing the loaded
   ``data/whats_moving.json`` dict, to get the fully-computed
   ``/moving/`` template context (which also carries
   ``masthead_sparklines``, for the reason above).
2. Render via :func:`render_moving_page` (accepts a Jinja ``Environment``
   the caller supplies, or builds its own minimal one via
   :func:`build_jinja_env` when none is given).

This module deliberately does *not* wire itself into ``site/generate.py``
(out of this turn's scope -- another turn integrates every Phase 4
builder together); see ``IMPROVEMENT_BACKLOG.md``.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

BUILDERS_DIR = Path(__file__).resolve().parent
SITE_DIR = BUILDERS_DIR.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
LIB_DIR = SITE_DIR / "lib"
DATA_DIR = REPO_ROOT / "data"
WHATS_MOVING_PATH = DATA_DIR / "whats_moving.json"


def _load_module_by_path(name: str, path: Path):
    """Load a module from an explicit file path, registering it in
    `sys.modules` *before* `exec_module` runs -- matches the convention
    every other Phase 4 module already uses (`site/builders/board.py`,
    `site/builders/wire.py`, `site/builders/primer.py`): `site/` is
    deliberately never turned into an importable package (it would shadow
    the stdlib `site` module), so every cross-file reference within
    `site/` loads its target by path instead of via a package import."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Reused verbatim, never reimplemented -- svg_sparkline.py already handles
# the accessibility requirements (aria-label + <title> + a visible trend
# word, never color/slope alone) this module must not duplicate or drift
# from.
svg_sparkline = _load_module_by_path(
    "frontier_wire_site_lib_svg_sparkline", LIB_DIR / "svg_sparkline.py"
)


# Human-readable display names for whats_moving.schema.json's nine fixed
# topic-tag enum values -- mirrors site/builders/board.py's own
# REGION_HEADINGS convention, for the identical reason: a couple of the
# raw enum values ("chips/compute", "open-source") read awkwardly as page
# copy verbatim. Spec-silent, logged in IMPROVEMENT_BACKLOG.md.
TOPIC_DISPLAY_NAMES: dict[str, str] = {
    "models": "Models",
    "research": "Research",
    "chips/compute": "Chips / Compute",
    "policy": "Policy",
    "products": "Products",
    "safety": "Safety",
    "open-source": "Open Source",
    "China": "China",
    "funding": "Funding",
}

# Human display text for data/whats_moving.json's own precomputed trend
# enum (accelerating/cooling/flat). Deliberately distinct from
# svg_sparkline.py's own independently-computed rising/falling/flat
# vocabulary rendered inside each sparkline's own <text> label -- see that
# module's docstring for why the two vocabularies coexist; this builder
# never conflates them, it just displays both, redundantly, which is the
# point (never color/slope alone).
TREND_DISPLAY: dict[str, str] = {
    "accelerating": "Accelerating",
    "cooling": "Cooling",
    "flat": "Flat",
}

# The masthead strip's sparklines render smaller than the full /moving/
# page's -- "thin", per this turn's instruction -- but reuse the exact
# same labeled, accessible svg_sparkline.render_sparkline() call this
# module uses everywhere else; there is no separate, unlabeled mini-chart
# code path.
MASTHEAD_SPARKLINE_WIDTH = 56
MASTHEAD_SPARKLINE_HEIGHT = 14

EMPTY_MOVING_MESSAGE = (
    "No topic-velocity data yet -- check back once watch.yml has run and "
    "produced data/whats_moving.json."
)


def _display_name(topic: str) -> str:
    return TOPIC_DISPLAY_NAMES.get(topic, topic)


def _trend_label(trend: str) -> str:
    return TREND_DISPLAY.get(trend, trend)


@dataclass(frozen=True)
class TopicRowView:
    """One `/moving/` page row -- the template only reads these
    already-computed fields, it never branches on raw
    `data/whats_moving.json` data itself."""

    topic: str
    display_name: str
    trend: str
    trend_label: str
    total_mentions: int
    daily_counts: tuple[int, ...]
    sparkline_svg: Markup


@dataclass(frozen=True)
class MastheadSparklineView:
    """One entry in the thin, site-wide masthead sparkline strip."""

    topic: str
    display_name: str
    sparkline_svg: Markup


def build_topic_row(raw: Mapping[str, Any]) -> TopicRowView:
    topic = str(raw["topic"])
    display_name = _display_name(topic)
    daily_counts = tuple(int(c) for c in raw["daily_counts"])
    trend = str(raw["trend"])
    rendered = svg_sparkline.render_sparkline(display_name, daily_counts)
    return TopicRowView(
        topic=topic,
        display_name=display_name,
        trend=trend,
        trend_label=_trend_label(trend),
        total_mentions=sum(daily_counts),
        daily_counts=daily_counts,
        sparkline_svg=Markup(rendered.svg),
    )


def build_topic_rows(topics: Sequence[Mapping[str, Any]]) -> list[TopicRowView]:
    """Every `data/whats_moving.json` topic entry, in the file's own
    order (a fixed nine-topic order per the schema's own description --
    this builder never re-sorts it). Handles an empty `topics` list
    gracefully -- an honest empty state, matching this build stage's
    established zero-collection-handling convention (see
    `site/builders/board.py::build_regions` for the sibling precedent)."""
    return [build_topic_row(t) for t in topics]


def build_masthead_sparklines(
    topics: Sequence[Mapping[str, Any]],
) -> list[MastheadSparklineView]:
    """The thin masthead strip's own view models -- one small sparkline
    per topic, reusing the same file data `build_topic_rows` does but at
    the masthead's smaller `MASTHEAD_SPARKLINE_*` dimensions."""
    views: list[MastheadSparklineView] = []
    for raw in topics:
        topic = str(raw["topic"])
        display_name = _display_name(topic)
        daily_counts = tuple(int(c) for c in raw["daily_counts"])
        rendered = svg_sparkline.render_sparkline(
            display_name,
            daily_counts,
            width=MASTHEAD_SPARKLINE_WIDTH,
            height=MASTHEAD_SPARKLINE_HEIGHT,
        )
        views.append(
            MastheadSparklineView(
                topic=topic,
                display_name=display_name,
                sparkline_svg=Markup(rendered.svg),
            )
        )
    return views


def build_moving_context(whats_moving: Mapping[str, Any]) -> dict[str, Any]:
    """Full Jinja context for `/moving/` (`moving.html`) -- also carries
    `masthead_sparklines`, so rendering this page through
    `templates/base.html` lights up the shared masthead strip (see this
    module's own top-of-file docstring)."""
    topics = list(whats_moving.get("topics", []))
    return {
        "topics": build_topic_rows(topics),
        "generated_at": whats_moving.get("generated_at"),
        "window_days": whats_moving.get("window_days", 7),
        "empty_message": EMPTY_MOVING_MESSAGE,
        "masthead_sparklines": build_masthead_sparklines(topics),
    }


def build_masthead_context(whats_moving: Mapping[str, Any]) -> dict[str, Any]:
    """Just the masthead-strip slice of `build_moving_context`, for a
    caller (or test) that wants to render/inspect
    `templates/_masthead_moving_strip.html` on its own, without the full
    `/moving/` page around it."""
    topics = list(whats_moving.get("topics", []))
    return {"masthead_sparklines": build_masthead_sparklines(topics)}


def load_whats_moving(path: Path = WHATS_MOVING_PATH) -> dict[str, Any]:
    """Load `data/whats_moving.json` (defaults to this repo's real,
    pure-code-generated file)."""
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


def render_moving_page(
    whats_moving: Mapping[str, Any], *, env: Environment | None = None
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_moving_context(whats_moving)
    return jinja_env.get_template("moving.html").render(**context)


def render_masthead_strip(
    whats_moving: Mapping[str, Any], *, env: Environment | None = None
) -> str:
    """Render just the `_masthead_moving_strip.html` partial standalone
    (for tests/inspection) -- `base.html` itself `{% include %}`s the same
    template directly once its own render context carries
    `masthead_sparklines`, rather than calling this function."""
    jinja_env = env or build_jinja_env()
    context = build_masthead_context(whats_moving)
    return jinja_env.get_template("_masthead_moving_strip.html").render(**context)


def write_moving_page(
    env: Environment, whats_moving: Mapping[str, Any], public_dir: Path
) -> Path:
    """Render + write `/moving/` (`<public_dir>/moving/index.html`).
    Convenience entry point for a future `site/generate.py` integration --
    not called by this module itself, and not called from `generate.py`
    yet (out of this turn's scope)."""
    html = render_moving_page(whats_moving, env=env)
    path = Path(public_dir) / "moving" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
