"""Server-side inline SVG sparklines (Phase 4, build-plan section 5).

Renders a small trend chart for a topic's daily mention counts -- matching
``data/whats_moving.json``'s shape (up to 7 daily counts, oldest first) --
as a single self-contained inline ``<svg>`` string with no client-side
charting library and no JS at all, per the project's stack decision.

Accessibility is structural, not decorative: the build plan is explicit
that a trend must "never [be conveyed by] color or slope alone." This
module states the trend in text twice, redundantly, so the information
survives both without color vision and without sight:

* the ``aria-label`` on the ``role="img"`` element (and a matching
  ``<title>``) states the trend in words, for assistive tech and for the
  native tooltip; and
* a visible ``<text>`` element inside the SVG renders an arrow-like glyph
  plus the literal trend word ("(up arrow) rising", "(down arrow)
  falling", "(right arrow) flat") right next to the polyline, for sighted
  users who never touch the accessible-name machinery at all.

The polyline itself is drawn in the site's signal-green accent color
(``tokens.css``'s ``--color-signal-green``, hardcoded here as its resolved
hex value since this module has no CSS custom-property access -- see
IMPROVEMENT_BACKLOG.md), but the color is only ever a reinforcing visual
cue on top of the two textual statements above, never the sole carrier of
the trend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr as xml_quoteattr

# Mirrors tokens.css's --color-signal-green (#39FF6E). This module cannot
# read the CSS custom property directly (it emits plain SVG markup, not a
# templated fragment with access to the stylesheet cascade), so the hex
# value is duplicated here deliberately -- spec-silent judgment call,
# logged in IMPROVEMENT_BACKLOG.md.
SIGNAL_GREEN = "#39FF6E"

MAX_DAILY_COUNTS = 7

# Arrow-like glyphs, one per trend word -- never rely on the glyph alone
# either; it always renders directly beside the literal trend word.
_TREND_GLYPHS = {
    "rising": "↑",  # ↑
    "falling": "↓",  # ↓
    "flat": "→",  # →
}

TRENDS = tuple(_TREND_GLYPHS)  # ("rising", "falling", "flat")


class SparklineError(ValueError):
    """Raised for malformed ``daily_counts`` input (empty, negative
    values, or more than the 7-day window ``data/whats_moving.json``
    defines). Never raised for merely "boring" data -- an all-zero or
    single-point series renders a valid flat sparkline instead."""


@dataclass(frozen=True)
class Sparkline:
    """Result of :func:`render_sparkline`.

    ``svg`` is the full, well-formed, self-contained ``<svg>...</svg>``
    string, safe to inline directly into a Jinja2 template with the
    ``|safe`` filter (or ``markupsafe.Markup``) at the call site -- this
    module does not depend on markupsafe itself since it builds the whole
    string from properly escaped/quoted pieces.

    ``trend`` is the plain trend word (``"rising"``, ``"falling"``, or
    ``"flat"``) this render used, exposed separately so a caller (e.g. a
    future Board/Moving-page builder) can also drive a CSS class or
    adjacent copy from it without re-parsing the SVG.
    """

    svg: str
    trend: str


def classify_trend(daily_counts: Sequence[int]) -> str:
    """Classify a daily-counts series into "rising", "falling", or
    "flat".

    Method (spec-silent, logged): split the series into an earlier half
    and a later half (the later half gets the extra element on odd
    lengths, weighting the most recent days -- the ones a reader cares
    most about) and compare mean counts. This is deliberately simpler
    than a real regression/slope fit, matching the "small sparkline, not
    a dashboard" scope of this component; a single-point series is
    always "flat" (nothing to compare).

    Sanity-checked against every topic in the real, live
    ``data/whats_moving.json`` snapshot as of this module's writing: this
    method reproduces that file's own precomputed "accelerating" ->
    rising / "flat" -> flat labels for all 9 topics currently on record
    (see ``site/tests/test_svg_sparkline.py``).
    """
    counts = list(daily_counts)
    if len(counts) < 2:
        return "flat"
    mid = len(counts) // 2
    first_half = counts[:mid]
    second_half = counts[mid:]
    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)
    if avg_second > avg_first:
        return "rising"
    if avg_second < avg_first:
        return "falling"
    return "flat"


def render_sparkline(
    topic: str,
    daily_counts: Sequence[int],
    *,
    width: int = 120,
    height: int = 32,
) -> Sparkline:
    """Render one topic's daily counts as a labeled, inline SVG
    sparkline.

    ``daily_counts`` must be 1-7 non-negative integers, oldest first,
    matching ``data/whats_moving.json``'s ``daily_counts`` shape (which
    is always exactly 7 -- this function tolerates fewer for reuse
    against other, shorter series, but never more than 7).
    """
    counts = list(daily_counts)
    if not counts:
        raise SparklineError("daily_counts must be non-empty")
    if len(counts) > MAX_DAILY_COUNTS:
        raise SparklineError(
            f"daily_counts must have at most {MAX_DAILY_COUNTS} entries "
            f"(data/whats_moving.json's shape), got {len(counts)}"
        )
    if any(c < 0 for c in counts):
        raise SparklineError("daily_counts must all be non-negative")

    trend = classify_trend(counts)
    glyph = _TREND_GLYPHS[trend]

    pad = 4
    chart_h = height
    total_h = height + 16  # extra row reserved for the visible trend text
    plot_w = max(width - 2 * pad, 1)
    plot_h = max(chart_h - 2 * pad, 1)

    n = len(counts)
    if n == 1:
        xs = [width / 2]
    else:
        step = plot_w / (n - 1)
        xs = [pad + i * step for i in range(n)]

    span = max(counts) or 1  # avoid a divide-by-zero for an all-zero series
    ys = [pad + plot_h - (c / span) * plot_h for c in counts]

    points = " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))

    label = (
        f"{topic}: {trend} over the last {n} day{'s' if n != 1 else ''} "
        f"(day-by-day counts: {', '.join(str(c) for c in counts)})"
    )

    svg = (
        "<svg "
        'role="img" '
        f"aria-label={xml_quoteattr(label)} "
        f'viewBox="0 0 {width} {total_h}" '
        f'width="{width}" height="{total_h}" '
        'xmlns="http://www.w3.org/2000/svg" '
        'class="sparkline">'
        f"<title>{xml_escape(label)}</title>"
        f'<polyline points="{points}" fill="none" '
        f'stroke="{SIGNAL_GREEN}" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" />'
        f'<text x="{pad}" y="{height + 13}" class="sparkline__trend" '
        f'font-size="11" fill="{SIGNAL_GREEN}">'
        f"{xml_escape(glyph)} {xml_escape(trend)}</text>"
        "</svg>"
    )
    return Sparkline(svg=svg, trend=trend)
