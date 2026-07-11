"""Unit tests for site/lib/matrix_rain.py -- the deterministic, zero-JS
digital-rain tile/column generator (Matrix-theme redesign, T3/T4).

Loaded by explicit file path under a unique module name, exactly matching
site/tests/test_build.py's own `_load_generate_module()` convention:
`site/` is deliberately never turned into an importable package (it would
shadow the stdlib `site` module for anything else sharing the
interpreter's `sys.path`), so every cross-file reference within `site/`
loads its target by path instead of via a normal `import`.

These are pure unit tests of the module in isolation, with no dependency
on tokens.css or site/generate.py: an arbitrary placeholder color
("#123456") is used throughout. Unlike site/generate.py's own call site
(which must source the real signal-green hex from tokens.css -- see
IMPROVEMENT_BACKLOG.md's note on site/lib/svg_sparkline.py's own logged
hardcoded-duplicate risk), the color here is a plain function argument
being unit-tested, not a token duplicate to avoid.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import unquote

SITE_DIR = Path(__file__).resolve().parent.parent


def _load_matrix_rain_module():
    spec = importlib.util.spec_from_file_location(
        "frontier_wire_site_lib_matrix_rain_test", SITE_DIR / "lib" / "matrix_rain.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Must register in sys.modules *before* exec_module runs: matrix_rain.py
    # combines `@dataclass` with `from __future__ import annotations`, and
    # dataclasses' own annotation resolution looks up
    # `sys.modules[cls.__module__]`, raising if that key isn't populated
    # yet. Matches site/generate.py's own `_load_module_by_path` convention
    # (see that function's docstring) for exactly this reason.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


matrix_rain = _load_matrix_rain_module()

PLACEHOLDER_COLOR = "#123456"


# --- determinism -------------------------------------------------------


def test_build_rain_columns_is_deterministic_for_identical_arguments():
    first = matrix_rain.build_rain_columns(PLACEHOLDER_COLOR)
    second = matrix_rain.build_rain_columns(PLACEHOLDER_COLOR)
    # RainColumn is a frozen dataclass, so list equality here is real
    # field-by-field (and therefore byte-for-byte tile URI) equality, not
    # merely "same length" or identity.
    assert first == second


def test_a_different_seed_produces_a_different_result():
    default_seed = matrix_rain.build_rain_columns(PLACEHOLDER_COLOR, seed=1)
    other_seed = matrix_rain.build_rain_columns(PLACEHOLDER_COLOR, seed=2)
    assert default_seed != other_seed


# --- per-column value bounds --------------------------------------------


def test_delay_duration_and_left_pct_stay_within_documented_bounds():
    min_duration = 5.0
    max_duration = 10.0
    columns = matrix_rain.build_rain_columns(
        PLACEHOLDER_COLOR, min_duration_s=min_duration, max_duration_s=max_duration
    )
    assert columns
    for col in columns:
        assert col.delay_s <= 0
        assert min_duration <= col.duration_s <= max_duration
        assert 0 <= col.left_pct < 100


# --- tile URI safety -----------------------------------------------------


def test_every_tile_data_uri_is_a_safe_fully_percent_encoded_svg_uri():
    columns = matrix_rain.build_rain_columns(PLACEHOLDER_COLOR)
    assert columns
    for col in columns:
        assert col.tile_data_uri.startswith("data:image/svg+xml,")
        # Fully percent-encoded: safe to drop straight inside a CSS
        # url("...") without a second layer of escaping, and specifically
        # free of the two characters that would break out of a
        # double-quoted CSS url() value or reintroduce raw markup.
        assert "<" not in col.tile_data_uri
        assert '"' not in col.tile_data_uri


# --- override plumbing ----------------------------------------------------


def test_column_tile_and_glyph_count_overrides_are_all_respected():
    column_count = 7
    tile_count = 3
    glyphs_per_tile = 5
    columns = matrix_rain.build_rain_columns(
        PLACEHOLDER_COLOR,
        column_count=column_count,
        tile_count=tile_count,
        glyphs_per_tile=glyphs_per_tile,
    )
    assert len(columns) == column_count

    unique_uris = {col.tile_data_uri for col in columns}
    assert len(unique_uris) == tile_count

    expected_height = glyphs_per_tile * matrix_rain.GLYPH_UNIT_HEIGHT
    prefix = "data:image/svg+xml,"
    for uri in unique_uris:
        svg = unquote(uri[len(prefix):])
        match = re.search(r'height="(\d+)"', svg)
        assert match is not None, f"no height attribute found in decoded tile SVG: {svg!r}"
        assert int(match.group(1)) == expected_height


def test_the_passed_color_is_percent_encoded_into_every_tile():
    columns = matrix_rain.build_rain_columns(PLACEHOLDER_COLOR)
    assert columns
    encoded_color = "%23" + PLACEHOLDER_COLOR.lstrip("#")
    for col in columns:
        assert encoded_color in col.tile_data_uri
