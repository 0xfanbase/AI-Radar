"""Tests for site/lib/svg_sparkline.py -- server-side inline SVG
sparklines (Phase 4).

Loaded by explicit file path (matching site/tests/test_build.py's own
convention) rather than an `import site.lib.svg_sparkline` package
import, since `site` is also a stdlib module name and this directory is
deliberately not turned into a package -- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SVG_SPARKLINE_PATH = REPO_ROOT / "site" / "lib" / "svg_sparkline.py"

SVG_NS = "{http://www.w3.org/2000/svg}"


def _load_svg_sparkline_module():
    spec = importlib.util.spec_from_file_location(
        "frontier_wire_site_lib_svg_sparkline", SVG_SPARKLINE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules *before* exec_module: svg_sparkline.py's
    # dataclass (combined with `from __future__ import annotations`)
    # needs its own module registered under `cls.__module__` for
    # dataclasses' internal annotation resolution to find it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


spark = _load_svg_sparkline_module()


# ---------------------------------------------------------------------------
# classify_trend()
# ---------------------------------------------------------------------------


def test_classify_trend_rising_when_later_days_higher_on_average():
    assert spark.classify_trend([0, 0, 0, 5, 6, 7, 8]) == "rising"


def test_classify_trend_falling_when_later_days_lower_on_average():
    assert spark.classify_trend([8, 7, 6, 5, 0, 0, 0]) == "falling"


def test_classify_trend_flat_when_averages_equal():
    assert spark.classify_trend([3, 3, 3, 3, 3, 3, 3]) == "flat"


def test_classify_trend_flat_for_all_zero_series():
    assert spark.classify_trend([0, 0, 0, 0, 0, 0, 0]) == "flat"


def test_classify_trend_flat_for_single_point_series():
    assert spark.classify_trend([4]) == "flat"


def test_classify_trend_matches_frozen_whats_moving_snapshot():
    # A frozen (hardcoded, not live-read) copy of data/whats_moving.json's
    # daily_counts as of 2026-07-09, paired with the trend label that
    # file's own precomputed "trend" field carried (mapped: accelerating
    # -> rising, cooling -> falling, flat -> flat). Hardcoded rather than
    # reading the live file directly so a future watch.yml run that
    # legitimately updates data/whats_moving.json's real counts can never
    # break this module's own, unrelated test suite.
    snapshot = [
        ([0, 0, 0, 0, 3, 6, 1], "rising"),       # models: accelerating
        ([0, 0, 0, 0, 0, 0, 0], "flat"),          # research: flat
        ([0, 0, 0, 0, 0, 0, 0], "flat"),          # chips/compute: flat
        ([0, 0, 0, 0, 0, 0, 0], "flat"),          # policy: flat
        ([0, 0, 0, 0, 0, 0, 1], "rising"),        # products: accelerating
        ([0, 0, 0, 0, 0, 0, 0], "flat"),          # safety: flat
        ([0, 0, 0, 0, 1, 1, 0], "rising"),        # open-source: accelerating
        ([0, 0, 0, 0, 1, 0, 0], "rising"),        # China: accelerating
        ([0, 0, 0, 0, 0, 0, 0], "flat"),          # funding: flat
    ]
    for daily_counts, expected_trend in snapshot:
        assert spark.classify_trend(daily_counts) == expected_trend


# ---------------------------------------------------------------------------
# render_sparkline() -- validation
# ---------------------------------------------------------------------------


def test_render_sparkline_rejects_empty_counts():
    with pytest.raises(spark.SparklineError):
        spark.render_sparkline("models", [])


def test_render_sparkline_rejects_more_than_seven_counts():
    with pytest.raises(spark.SparklineError):
        spark.render_sparkline("models", [1, 2, 3, 4, 5, 6, 7, 8])


def test_render_sparkline_rejects_negative_counts():
    with pytest.raises(spark.SparklineError):
        spark.render_sparkline("models", [0, 0, -1, 2, 3, 4, 5])


def test_render_sparkline_accepts_exactly_seven_counts():
    result = spark.render_sparkline("models", [0, 0, 0, 0, 3, 6, 1])
    assert result.trend == "rising"


def test_render_sparkline_accepts_fewer_than_seven_counts():
    result = spark.render_sparkline("models", [1, 2])
    assert result.trend == "rising"


# ---------------------------------------------------------------------------
# render_sparkline() -- SVG well-formedness (real XML parser)
# ---------------------------------------------------------------------------


def _parse_svg(svg_str: str) -> ET.Element:
    return ET.fromstring(svg_str)


@pytest.mark.parametrize(
    "daily_counts",
    [
        [0, 0, 0, 0, 3, 6, 1],  # rising
        [8, 7, 6, 5, 0, 0, 0],  # falling
        [3, 3, 3, 3, 3, 3, 3],  # flat
        [0, 0, 0, 0, 0, 0, 0],  # flat, all zero (division-by-zero guard)
        [5],  # single point
    ],
)
def test_render_sparkline_output_is_well_formed_xml(daily_counts):
    result = spark.render_sparkline("models", daily_counts)
    root = _parse_svg(result.svg)  # raises ET.ParseError if malformed
    assert root.tag == f"{SVG_NS}svg"


def test_svg_root_has_role_img_and_aria_label():
    result = spark.render_sparkline("models", [0, 0, 0, 0, 3, 6, 1])
    root = _parse_svg(result.svg)
    assert root.get("role") == "img"
    aria_label = root.get("aria-label")
    assert aria_label
    assert "rising" in aria_label
    assert "models" in aria_label


def test_svg_has_title_element_stating_the_trend():
    result = spark.render_sparkline("models", [8, 7, 6, 5, 0, 0, 0])
    root = _parse_svg(result.svg)
    title_el = root.find(f"{SVG_NS}title")
    assert title_el is not None
    assert title_el.text
    assert "falling" in title_el.text


def test_svg_has_polyline_using_signal_cyan():
    result = spark.render_sparkline("models", [0, 0, 0, 0, 3, 6, 1])
    root = _parse_svg(result.svg)
    polyline = root.find(f"{SVG_NS}polyline")
    assert polyline is not None
    assert polyline.get("stroke") == spark.SIGNAL_CYAN
    # every point must be a real "x,y" numeric pair -- a well-formed
    # points attribute proves the plotted data actually round-tripped.
    points = polyline.get("points")
    assert points
    pairs = points.strip().split(" ")
    assert len(pairs) == 7
    for pair in pairs:
        x_str, y_str = pair.split(",")
        float(x_str)
        float(y_str)


def test_svg_has_visible_trend_text_with_word_and_glyph():
    for daily_counts, expected_word, expected_glyph in [
        ([0, 0, 0, 0, 3, 6, 1], "rising", spark._TREND_GLYPHS["rising"]),
        ([8, 7, 6, 5, 0, 0, 0], "falling", spark._TREND_GLYPHS["falling"]),
        ([3, 3, 3, 3, 3, 3, 3], "flat", spark._TREND_GLYPHS["flat"]),
    ]:
        result = spark.render_sparkline("models", daily_counts)
        root = _parse_svg(result.svg)
        text_el = root.find(f"{SVG_NS}text")
        assert text_el is not None
        assert text_el.text is not None
        assert expected_word in text_el.text
        assert expected_glyph in text_el.text


def test_trend_is_never_conveyed_by_color_or_polyline_alone():
    # Both textual channels (aria-label and the visible <text> glyph+word)
    # must independently state the trend word -- this is the structural
    # proof that color/slope is never the sole carrier of the trend.
    result = spark.render_sparkline("models", [8, 7, 6, 5, 0, 0, 0])
    root = _parse_svg(result.svg)
    aria_label = root.get("aria-label")
    text_el = root.find(f"{SVG_NS}text")
    title_el = root.find(f"{SVG_NS}title")
    assert "falling" in aria_label
    assert "falling" in text_el.text
    assert "falling" in title_el.text
    # the polyline element itself carries no text/label of its own.
    polyline = root.find(f"{SVG_NS}polyline")
    assert polyline.text is None


def test_viewbox_is_small_and_matches_declared_width_and_height():
    result = spark.render_sparkline("models", [0, 0, 0, 0, 3, 6, 1], width=120, height=32)
    root = _parse_svg(result.svg)
    view_box = root.get("viewBox")
    assert view_box == "0 0 120 48"  # height + 16px reserved for the trend text row
    assert root.get("width") == "120"
    assert root.get("height") == "48"


def test_render_sparkline_topic_names_with_special_characters_stay_well_formed():
    # topic names containing XML-special characters must not break
    # well-formedness -- exercised even though today's real
    # data/whats_moving.json topic enum has no such characters, as a
    # resilience check against this reusable component being reused
    # elsewhere later with different labels.
    result = spark.render_sparkline("chips/compute & <policy>", [1, 2, 3])
    root = _parse_svg(result.svg)  # raises if malformed
    assert "chips/compute" in (root.get("aria-label") or "")
