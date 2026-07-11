"""Deterministic, zero-JavaScript "digital rain" decorative background
(Matrix-theme redesign).

Builds a small, fixed set of tileable SVG glyph-column patterns (half-width
katakana -- the actual glyph range the source material's on-screen effect
used -- plus digits and capital Latin letters), each rendered once as an
inline `data:image/svg+xml,...` URI. Every column div in the shared
`_matrix_rain.html` partial gets one of these tiles as its CSS
`background-image` with `background-repeat: repeat-y`, animated purely via
a CSS `background-position` keyframe (see `site/static/css/matrix.css`) --
there is no per-glyph DOM node anywhere, and no JavaScript anywhere. This
keeps the DOM small (a few dozen empty `<div>`s) no matter how "tall" the
rain looks, and keeps the effect purely decorative and inert: the shared
partial marks the whole layer `aria-hidden="true"` with
`pointer-events: none`, and every column's motion lives only inside
`@media (prefers-reduced-motion: no-preference)` (this module has no
opinion on that CSS gating; it only supplies the static tile images and
per-column timing/position values).

A fixed `random.Random(seed)` (never the wall clock, never the stdlib
`random` module's global default state) makes every call -- and therefore
every `site/generate.py` build -- produce byte-for-byte identical output,
matching this project's established idempotent-rebuild convention (the
same property `site/generate.py`'s `apply_base_path()` and the watcher's
ledger hashing both already rely on). Re-running the site generator must
never change this file's own output.

The rendered glyph color is passed in by the caller (`site/generate.py`,
which reads it from the real, on-disk `tokens.css` -- see that module's
own `read_signal_color()` sibling helper if one exists, or its inline
parse) rather than hardcoded here a second time, specifically to avoid
repeating `site/lib/svg_sparkline.py`'s own logged judgment call (a
hardcoded duplicate of `--color-signal-green`'s hex value that a future
palette change could silently miss).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

# Half-width katakana (U+FF66-U+FF9D) is the actual Unicode block the
# source material's on-screen "code" effect draws its glyphs from, mixed
# with digits and capital Latin letters for variety -- matching the real
# effect's own mixed character set rather than using katakana exclusively.
_HALF_WIDTH_KATAKANA = [chr(cp) for cp in range(0xFF66, 0xFF9E)]
_GLYPH_POOL: tuple[str, ...] = tuple(
    _HALF_WIDTH_KATAKANA + list("0123456789") + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
)

# One glyph occupies this many SVG user-units of vertical space; the tile
# height is derived from this so glyphs never look stretched/squashed
# regardless of how many glyphs-per-tile is configured.
GLYPH_UNIT_HEIGHT = 22
TILE_WIDTH_UNITS = 24
FONT_SIZE_UNITS = 18

# Defaults match the shared partial's expectations (see
# `site/templates/_matrix_rain.html` / `site/static/css/matrix.css`):
# enough distinct tiles that adjacent columns rarely look identical, a
# glyph count per tile tall enough that the seamless `repeat-y` loop isn't
# obviously short, and enough columns to fill a wide desktop viewport
# (extras beyond a narrow/mobile viewport's width simply overflow-hidden,
# same convention as every other fixed-width overflow guard on this site).
DEFAULT_SEED = 1337
DEFAULT_TILE_COUNT = 10
DEFAULT_GLYPHS_PER_TILE = 44
DEFAULT_COLUMN_COUNT = 72
DEFAULT_MIN_DURATION_S = 9.0
DEFAULT_MAX_DURATION_S = 23.0


@dataclass(frozen=True)
class RainColumn:
    """One `<div>`'s worth of render-ready decorative rain state. `left_pct`
    positions columns evenly across the layer's width; `duration_s` and
    `delay_s` (the latter always <= 0, i.e. "already partway through its
    loop") desynchronize columns so they don't all visibly start in
    lock-step on page load."""

    tile_data_uri: str
    duration_s: float
    delay_s: float
    left_pct: float


def _build_tile_svg(rng: random.Random, glyphs_per_tile: int, color: str) -> str:
    """One seamlessly-`repeat-y`-tileable SVG data URI: a vertical strip of
    `glyphs_per_tile` random glyphs. Seamless by construction -- a
    `background-repeat: repeat-y` tiling of any image against itself has no
    seam, so this never needs special first/last-glyph handling."""
    glyphs = [rng.choice(_GLYPH_POOL) for _ in range(glyphs_per_tile)]
    height = glyphs_per_tile * GLYPH_UNIT_HEIGHT
    center_x = TILE_WIDTH_UNITS / 2
    rows = "".join(
        '<text x="{x}" y="{y}" text-anchor="middle" font-size="{size}" '
        'font-family="monospace" fill="{color}">{glyph}</text>'.format(
            x=center_x,
            y=(i + 1) * GLYPH_UNIT_HEIGHT - (GLYPH_UNIT_HEIGHT - FONT_SIZE_UNITS),
            size=FONT_SIZE_UNITS,
            color=xml_escape(color),
            glyph=xml_escape(glyph),
        )
        for i, glyph in enumerate(glyphs)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="{w}" height="{h}">{rows}</svg>'
    ).format(w=TILE_WIDTH_UNITS, h=height, rows=rows)
    return "data:image/svg+xml," + quote(svg, safe="")


def build_rain_columns(
    color: str,
    *,
    seed: int = DEFAULT_SEED,
    tile_count: int = DEFAULT_TILE_COUNT,
    glyphs_per_tile: int = DEFAULT_GLYPHS_PER_TILE,
    column_count: int = DEFAULT_COLUMN_COUNT,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
    max_duration_s: float = DEFAULT_MAX_DURATION_S,
) -> list[RainColumn]:
    """Build the full set of decorative rain columns for one site build.

    `color` is the caller's responsibility to source from the real
    `tokens.css` (never a second hardcoded hex here). Every other
    parameter defaults to this module's tuned constants above; callers
    (tests, `site/generate.py`) may override any of them, e.g. a test
    asking for a tiny `column_count`/`glyphs_per_tile` to keep fixture
    output small and readable.

    Deterministic: the same arguments always produce the same
    `list[RainColumn]`, byte for byte -- this function never reads
    `random`'s global state, wall-clock time, or any other process-entropy
    source; it only ever uses the `random.Random(seed)` instance it
    constructs internally from the explicit `seed` argument.
    """
    rng = random.Random(seed)
    tiles = [_build_tile_svg(rng, glyphs_per_tile, color) for _ in range(tile_count)]

    columns: list[RainColumn] = []
    for i in range(column_count):
        tile = tiles[i % tile_count]
        duration = round(rng.uniform(min_duration_s, max_duration_s), 2)
        # A strictly-negative delay starts the animation "already in
        # progress" at a random phase -- CSS honors a negative
        # animation-delay this way -- so columns never visibly synchronize
        # on first paint even though they all share the same handful of
        # tile images.
        delay = round(-rng.uniform(0.0, duration), 2)
        left_pct = round((i / column_count) * 100, 3)
        columns.append(RainColumn(tile, duration, delay, left_pct))
    return columns
