"""What's Moving builder (Phase 4, build-plan section 5; masthead scope
narrowed in the nav-condense pass -- see ``IMPROVEMENT_BACKLOG.md``) --
the site's topic-velocity strip, plus the thin masthead sparkline view
model consumed by the Wire home page only.

Reads the pure-code, no-AI ``data/whats_moving.json`` snapshot (see
``schemas/whats_moving.schema.json`` / ``IMPROVEMENT_BACKLOG.md`` for its
shape -- nine fixed topic tags, each carrying exactly 7 daily HN-mention
counts oldest-first and a precomputed ``accelerating``/``cooling``/``flat``
trend label) and renders:

* ``/moving/`` -- one row per topic, each with its own inline SVG
  sparkline (``site/lib/svg_sparkline.py``, already implemented -- this
  module never draws its own chart, only calls that one).
* the thin masthead sparkline strip
  (``templates/_masthead_moving_strip.html``), which ``templates/base.html``
  conditionally ``{% include %}``s once a caller's render context carries
  a non-empty ``masthead_sparklines`` list. Only the Wire home page
  (``site/builders/wire.py::build_wire_context``, wired up by
  ``site/generate.py``) ever sets that variable now -- every other page
  (including this module's own ``/moving/``) leaves it unset, so
  ``base.html``'s own
  ``{% if masthead_sparklines is defined and masthead_sparklines %}``
  guard keeps it off everywhere except the home page. :func:`build_masthead_sparklines`
  is this module's contribution to that: it caps the strip to the
  ``MASTHEAD_TOPIC_LIMIT`` (5) topics with the highest 7-day mention
  totals, so the strip fits a narrow mobile viewport without silently
  clipping the long tail. ``build_moving_context`` (the ``/moving/`` page's
  own context) does *not* include ``masthead_sparklines`` any more --
  ``/moving/`` already shows every topic's own full-size sparkline in its
  main list, so the strip would have been the same content rendered
  twice on that one page.

Two-step build usage (mirrors ``site/builders/board.py``'s own
convention):

1. Call :func:`build_moving_context` once, passing the loaded
   ``data/whats_moving.json`` dict, to get the fully-computed
   ``/moving/`` template context.
2. Render via :func:`render_moving_page` (accepts a Jinja ``Environment``
   the caller supplies, or builds its own minimal one via
   :func:`build_jinja_env` when none is given).

:func:`build_masthead_sparklines` is called directly by
``site/generate.py`` (not through this module's own page context) to
build the Wire home page's masthead-strip view models; see that
function's own docstring.
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
# topic-tag enum values -- the exact same raw values as card.schema.json's
# `topics` enum, so this module loads the one shared mapping
# (site/lib/topics.py) rather than keeping its own copy, which would risk
# drifting from site/builders/wire.py's own card-topic-chip display names.
# See topics.py's own docstring. Spec-silent choice to centralize, logged
# in IMPROVEMENT_BACKLOG.md.
topics_lib = _load_module_by_path(
    "frontier_wire_site_lib_topics", LIB_DIR / "topics.py"
)

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

# The masthead strip is scoped to the Wire home page only (see this
# module's own top-of-file docstring) and must fit a narrow (390px)
# mobile viewport without silently clipping topics off the right edge --
# so it shows only the topics with the most 7-day HN mentions, not all
# nine. Named as its own constant (rather than an inline literal) so a
# future change to this cap is a one-line, logged decision -- matching
# this repo's own established convention (e.g. the reputable-outlet
# table in CLAUDE.md) of naming a cutoff explicitly rather than leaving
# it to be rediscovered from a bare number.
MASTHEAD_TOPIC_LIMIT = 5

EMPTY_MOVING_MESSAGE = (
    "No topic data yet -- check back soon. This snapshot updates daily "
    "from Hacker News activity."
)


def _display_name(topic: str) -> str:
    """Thin delegate to the shared `site/lib/topics.py::display_name` --
    kept as its own function so every existing caller in this module (and
    in `site/tests/test_moving_builder.py`) is untouched."""
    return topics_lib.display_name(topic)


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
    mentions_label: str
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
    total_mentions = sum(daily_counts)
    mentions_label = f"{total_mentions} mention{'s' if total_mentions != 1 else ''} / 7d"
    return TopicRowView(
        topic=topic,
        display_name=display_name,
        trend=trend,
        trend_label=_trend_label(trend),
        total_mentions=total_mentions,
        mentions_label=mentions_label,
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
    """The Wire home page's masthead strip view models -- one small
    sparkline per topic, reusing the same file data `build_topic_rows`
    does but at the masthead's smaller `MASTHEAD_SPARKLINE_*` dimensions,
    capped to the `MASTHEAD_TOPIC_LIMIT` topics with the highest 7-day
    mention totals (descending), so the strip fits a narrow mobile
    viewport without silently clipping topics off the right edge. Ties
    keep `topics`' own original (fixed nine-topic, per-schema) relative
    order -- `sorted(..., reverse=True)` is guaranteed stable in Python,
    so this never depends on dict/JSON key order beyond that guarantee."""
    ranked = sorted(topics, key=lambda raw: sum(int(c) for c in raw["daily_counts"]), reverse=True)
    views: list[MastheadSparklineView] = []
    for raw in ranked[:MASTHEAD_TOPIC_LIMIT]:
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
    """Full Jinja context for `/moving/` (`moving.html`). Deliberately
    does *not* carry `masthead_sparklines` -- the masthead strip is
    scoped to the Wire home page only (see this module's own
    top-of-file docstring); `/moving/`'s own main list already shows
    every topic's full-size sparkline, so repeating a second, smaller
    copy of the same nine sparklines above it would just be the same
    content shown twice on the one page that needs it least."""
    topics = list(whats_moving.get("topics", []))
    return {
        "topics": build_topic_rows(topics),
        "generated_at": whats_moving.get("generated_at"),
        "window_days": whats_moving.get("window_days", 7),
        "empty_message": EMPTY_MOVING_MESSAGE,
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
