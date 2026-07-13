"""Tests for site/builders/map.py + site/templates/map_index.html -- the
world-map homepage (Phase 7, map-centric UI reshape).

Loaded by explicit file path, matching every other `site/tests/*.py`
file's own convention (`site/` is deliberately not an importable package
-- see IMPROVEMENT_BACKLOG.md).

Exercises the real, committed `content/companies/index.json` (13 seeded
companies, per Phase 6) and `content/frontier_board.json` (13 rows, each
carrying a `company_id`) for the marker-count/open-weights/board-row
assertions, plus the real vendored
`site/static/geo/ne_110m_admin_0_countries.geojson` for the
country-path projection assertions -- and small synthetic inputs for the
pure-function projection/offset/sort-order unit tests, matching
`site/tests/test_board_builder.py`'s own "real content + synthetic
fixtures" split.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAP_PATH = REPO_ROOT / "site" / "builders" / "map.py"
COMPANIES_INDEX_PATH = REPO_ROOT / "content" / "companies" / "index.json"
FRONTIER_BOARD_PATH = REPO_ROOT / "content" / "frontier_board.json"
GEO_PATH = REPO_ROOT / "site" / "static" / "geo" / "ne_110m_admin_0_countries.geojson"


def _load_map_module():
    spec = importlib.util.spec_from_file_location("frontier_wire_site_builders_map", MAP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


map_builder = _load_map_module()


def _load_real_companies() -> list[dict]:
    with COMPANIES_INDEX_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["companies"]


def _load_real_board_rows() -> list[dict]:
    with FRONTIER_BOARD_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


REAL_COMPANIES = _load_real_companies()
REAL_BOARD_ROWS = _load_real_board_rows()


# ---------------------------------------------------------------------------
# project() -- pure equirectangular projection
# ---------------------------------------------------------------------------


def test_project_center_of_the_world_lands_at_the_viewbox_center():
    x, y = map_builder.project(0.0, 0.0, width=960, height=500)
    assert x == pytest.approx(480.0)
    assert y == pytest.approx(250.0)


def test_project_top_left_corner():
    x, y = map_builder.project(-180.0, 90.0, width=960, height=500)
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(0.0)


def test_project_bottom_right_corner():
    x, y = map_builder.project(180.0, -90.0, width=960, height=500)
    assert x == pytest.approx(960.0)
    assert y == pytest.approx(500.0)


def test_project_north_is_up_positive_latitude_gets_smaller_y():
    _, y_north = map_builder.project(0.0, 60.0)
    _, y_south = map_builder.project(0.0, -60.0)
    assert y_north < y_south


# ---------------------------------------------------------------------------
# geometry_to_path_d -- GeoJSON geometry -> SVG path data
# ---------------------------------------------------------------------------


def test_geometry_to_path_d_polygon_produces_one_closed_subpath():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
    }
    d = map_builder.geometry_to_path_d(geometry, width=360, height=180)
    assert d.startswith("M")
    assert d.count("M") == 1
    assert d.endswith("Z")


def test_geometry_to_path_d_multipolygon_produces_one_subpath_per_ring():
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
            [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]],
        ],
    }
    d = map_builder.geometry_to_path_d(geometry, width=360, height=180)
    assert d.count("M") == 2
    assert d.count("Z") == 2


def test_geometry_to_path_d_unknown_type_returns_empty_string():
    assert map_builder.geometry_to_path_d({"type": "Point", "coordinates": [0, 0]}) == ""


def test_geometry_to_path_d_polygon_with_a_hole_emits_two_subpaths():
    # Exterior ring + one interior hole ring -- both become their own
    # "M...Z" subpath; templates/map_index.html relies on
    # `fill-rule: evenodd` (not this function) to render the hole
    # correctly regardless of winding order.
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]],
            [[-2, -2], [2, -2], [2, 2], [-2, 2], [-2, -2]],
        ],
    }
    d = map_builder.geometry_to_path_d(geometry, width=360, height=180)
    assert d.count("M") == 2


# ---------------------------------------------------------------------------
# build_country_paths -- against the real vendored geometry
# ---------------------------------------------------------------------------


def test_load_geojson_reads_the_real_vendored_file():
    geo = map_builder.load_geojson()
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) > 150


def test_build_country_paths_against_real_vendored_geometry():
    geo = map_builder.load_geojson()
    paths = map_builder.build_country_paths(geo)
    assert len(paths) == len(geo["features"])
    for country in paths:
        assert country.d.startswith("M")
        assert country.d.endswith("Z")


def test_build_country_paths_skips_a_feature_with_empty_geometry():
    geo = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "Nowhere"}, "geometry": {"type": "Polygon", "coordinates": []}},
            {
                "type": "Feature",
                "properties": {"name": "Somewhere"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]],
                },
            },
        ],
    }
    paths = map_builder.build_country_paths(geo)
    assert len(paths) == 1
    assert paths[0].name == "Somewhere"


# ---------------------------------------------------------------------------
# marker_offset -- hand-tuned per-marker table
# ---------------------------------------------------------------------------


def test_marker_offset_returns_zero_for_an_unknown_company():
    assert map_builder.marker_offset("some-future-lab") == (0.0, 0.0)


def test_marker_offset_table_covers_every_real_seeded_company():
    real_ids = {c["id"] for c in REAL_COMPANIES}
    assert real_ids <= set(map_builder.MARKER_OFFSET_PX.keys())


def test_marker_offset_bay_area_cluster_members_are_all_distinct():
    # anthropic and openai share the exact same hq_lat/hq_lng in the real
    # seeded data -- without a hand offset they'd render as one
    # indistinguishable dot. ai2/Seattle is included here too (UPDATED as
    # part of the dense-cluster-overlap fix): it looked geographically
    # "isolated" from the Bay Area cluster and was originally left at
    # (0, 0), but at this map's whole-world projection scale it's only
    # ~27 SVG units from the rest of the cluster -- well inside its
    # footprint -- so it collided with it in practice and needs its own
    # distinct offset too. Assert every US-cluster member gets a
    # different offset from every other member.
    us_cluster = ["anthropic", "openai", "meta-ai", "xai", "nvidia", "ai2"]
    offsets = [map_builder.marker_offset(cid) for cid in us_cluster]
    assert len(set(offsets)) == len(us_cluster)


def test_marker_offset_china_cluster_members_are_all_distinct():
    # Beijing (moonshot-ai/zhipu-ai/bytedance-seed) and Hangzhou
    # (deepseek/alibaba-qwen) share exact same-city coordinates within
    # each city, and the two cities' own true positions are only ~10x27
    # SVG units apart -- too close to lay out as two independently
    # hand-offset sub-clusters without them colliding with each other,
    # so all five are laid out together as one cluster (see
    # MARKER_OFFSET_PX's own docstring). Assert every member gets a
    # distinct offset.
    china_cluster = ["moonshot-ai", "zhipu-ai", "bytedance-seed", "deepseek", "alibaba-qwen"]
    offsets = [map_builder.marker_offset(cid) for cid in china_cluster]
    assert len(set(offsets)) == len(china_cluster)


def test_marker_offset_europe_cluster_members_are_distinct_and_nonzero():
    # google-deepmind/London and mistral/Paris were originally called
    # "genuinely isolated" and left at (0, 0) -- also wrong (UPDATED as
    # part of the dense-cluster-overlap fix): they're only ~7x7 SVG
    # units apart at this map's whole-world scale, close enough that a
    # real headless-browser check found their labels overlapping. Assert
    # both now have a real, distinct, nonzero offset.
    europe_cluster = ["google-deepmind", "mistral"]
    offsets = [map_builder.marker_offset(cid) for cid in europe_cluster]
    assert len(set(offsets)) == len(europe_cluster)
    assert all(offset != (0.0, 0.0) for offset in offsets)


# ---------------------------------------------------------------------------
# board_rows_for_company / has_open_weights -- against real Board data
# ---------------------------------------------------------------------------


def test_board_rows_for_company_finds_the_real_anthropic_row():
    rows = map_builder.board_rows_for_company("anthropic", REAL_BOARD_ROWS)
    assert len(rows) == 1
    assert rows[0]["model"] == "Claude Fable 5"


def test_board_rows_for_company_empty_for_unknown_company():
    assert map_builder.board_rows_for_company("no-such-company", REAL_BOARD_ROWS) == []


def test_board_rows_for_company_sorts_newest_release_first():
    rows = [
        {"company_id": "x", "release_date": "2025-01-01", "model": "old"},
        {"company_id": "x", "release_date": "2026-06-01", "model": "new"},
        {"company_id": "x", "release_date": "2025-12-01", "model": "mid"},
    ]
    ordered = map_builder.board_rows_for_company("x", rows)
    assert [r["model"] for r in ordered] == ["new", "mid", "old"]


def test_has_open_weights_true_for_deepseek():
    assert map_builder.has_open_weights("deepseek", REAL_BOARD_ROWS) is True


def test_has_open_weights_false_for_a_company_with_no_open_weights_row():
    # Real seeded data: OpenAI's only Board row is access: "api".
    assert map_builder.has_open_weights("openai", REAL_BOARD_ROWS) is False


def test_has_open_weights_false_for_unknown_company():
    assert map_builder.has_open_weights("no-such-company", REAL_BOARD_ROWS) is False


# ---------------------------------------------------------------------------
# cards_for_company -- degrades gracefully with zero cards
# ---------------------------------------------------------------------------


def test_cards_for_company_empty_list_when_no_cards():
    assert map_builder.cards_for_company("anthropic", []) == []


def test_cards_for_company_filters_and_sorts_newest_first():
    cards = [
        {
            "id": "card-1",
            "date": "2026-06-01",
            "generated_at": "2026-06-01T00:00:00Z",
            "headline": "Old story",
            "status": "confirmed",
            "companies": ["anthropic"],
        },
        {
            "id": "card-2",
            "date": "2026-07-01",
            "generated_at": "2026-07-01T00:00:00Z",
            "headline": "New story",
            "status": "reported",
            "companies": ["anthropic", "openai"],
        },
        {
            "id": "card-3",
            "date": "2026-07-05",
            "generated_at": "2026-07-05T00:00:00Z",
            "headline": "Unrelated",
            "status": "confirmed",
            "companies": ["openai"],
        },
    ]
    views = map_builder.cards_for_company("anthropic", cards)
    assert [v["id"] for v in views] == ["card-2", "card-1"]
    assert views[0]["href"] == "/wire/2026-07/#card-card-2"
    assert views[0]["status_label"] == "REPORTED"


def test_cards_for_company_respects_the_limit():
    cards = [
        {
            "id": f"card-{i}",
            "date": f"2026-07-{i:02d}",
            "generated_at": f"2026-07-{i:02d}T00:00:00Z",
            "headline": f"Story {i}",
            "status": "confirmed",
            "companies": ["anthropic"],
        }
        for i in range(1, 6)
    ]
    views = map_builder.cards_for_company("anthropic", cards, limit=3)
    assert len(views) == 3
    assert views[0]["id"] == "card-5"


# ---------------------------------------------------------------------------
# build_markers / build_context -- against real seeded content
# ---------------------------------------------------------------------------


def test_build_markers_produces_one_marker_per_real_company():
    markers = map_builder.build_markers(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert len(markers) == len(REAL_COMPANIES) == 13


def test_build_markers_percentages_are_within_the_viewbox():
    markers = map_builder.build_markers(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    for marker in markers:
        assert -5.0 <= marker.pct_x <= 105.0
        assert -5.0 <= marker.pct_y <= 105.0


def test_build_markers_open_weights_flag_matches_real_board_data():
    markers = {m.id: m for m in map_builder.build_markers(REAL_COMPANIES, REAL_BOARD_ROWS, [])}
    assert markers["deepseek"].open_weights is True
    assert markers["mistral"].open_weights is True
    assert markers["openai"].open_weights is False
    assert markers["anthropic"].open_weights is False


def test_build_markers_skips_a_company_missing_hq_coordinates():
    companies = [{"id": "ghost", "name": "Ghost Labs", "hq_city": "Nowhere", "hq_country": "XX"}]
    markers = map_builder.build_markers(companies, [], [])
    assert markers == []


def test_build_markers_profile_href_points_at_the_companies_route():
    markers = {m.id: m for m in map_builder.build_markers(REAL_COMPANIES, REAL_BOARD_ROWS, [])}
    assert markers["anthropic"].profile_href == "/companies/anthropic/"


# ---------------------------------------------------------------------------
# MarkerView.anchor_right -- build-time left/right popover-anchor flip
# (map rebuild: bigger/full-bleed/pannable/zoomable canvas, requirement 5's
# build-time half of the mobile/edge popover-overflow fix)
# ---------------------------------------------------------------------------


def test_anchor_right_false_for_a_marker_left_of_center():
    companies = [
        {"id": "west", "name": "West Co", "hq_city": "X", "hq_country": "Y", "hq_lat": 0.0, "hq_lng": -170.0}
    ]
    markers = map_builder.build_markers(companies, [], [])
    assert markers[0].pct_x < 50.0
    assert markers[0].anchor_right is False


def test_anchor_right_true_for_a_marker_right_of_center():
    companies = [
        {"id": "east", "name": "East Co", "hq_city": "X", "hq_country": "Y", "hq_lat": 0.0, "hq_lng": 170.0}
    ]
    markers = map_builder.build_markers(companies, [], [])
    assert markers[0].pct_x > 50.0
    assert markers[0].anchor_right is True


def test_anchor_right_matches_pct_x_threshold_for_every_real_company():
    markers = map_builder.build_markers(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    for marker in markers:
        assert marker.anchor_right == (marker.pct_x > 50.0), marker.id


def test_anchor_right_real_china_region_companies_anchor_right():
    # Sanity check against real seeded data: every China-region company
    # (near the map's right edge) should anchor its popover rightward
    # (extending leftward, away from the page's own right edge).
    markers = {m.id: m for m in map_builder.build_markers(REAL_COMPANIES, REAL_BOARD_ROWS, [])}
    for company_id in ["deepseek", "alibaba-qwen", "moonshot-ai", "zhipu-ai", "bytedance-seed"]:
        assert markers[company_id].anchor_right is True, company_id


def test_build_context_shape():
    context = map_builder.build_context(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert context["total_markers"] == 13
    assert context["open_weights_count"] >= 1
    assert len(context["country_paths"]) > 150
    assert context["masthead_sparklines"] == []


# ---------------------------------------------------------------------------
# render_map_page / write_map_page -- real template, end to end
# ---------------------------------------------------------------------------


def test_render_map_page_against_the_real_template_does_not_crash():
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert "<h1>Where AI Is Moving</h1>" in html
    assert "Anthropic" in html
    assert 'href="/companies/anthropic/"' in html


def test_render_map_page_name_link_is_a_plain_anchor_independent_of_the_popover():
    # Structural no-JS guarantee: the marker glyph button and the
    # company-name link are two separate elements.
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert '<button type="button" class="map-marker__glyph"' in html
    assert '<a class="map-marker__name" href="/companies/anthropic/">Anthropic</a>' in html


def test_render_map_page_popover_starts_hidden_without_js():
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert 'id="map-popover-anthropic" hidden' in html


def test_render_map_page_empty_cards_state_shown_when_no_cards_match():
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert map_builder.EMPTY_CARDS_MESSAGE in html


# ---------------------------------------------------------------------------
# Map rebuild (bigger/full-bleed/pannable/zoomable canvas) -- server-
# rendered/build-time markup this task actually changed. The interactive
# pan/zoom/touch/drag behavior itself is client-side JS
# (site/static/js/map.js) that this Python test suite has no way to
# exercise -- see this task's own summary for the real-browser check that
# covers it instead.
# ---------------------------------------------------------------------------


def test_render_map_page_has_the_full_bleed_section_wrapping_the_viewport():
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert '<div class="map-section">' in html
    assert '<div class="map-viewport" id="map-viewport">' in html
    assert '<div class="map-wrap" id="map-wrap">' in html


def test_render_map_page_has_real_keyboard_operable_zoom_controls():
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    for button_id, label in [
        ("map-zoom-in", "Zoom in"),
        ("map-zoom-out", "Zoom out"),
        ("map-zoom-reset", "Reset map view"),
    ]:
        assert f'id="{button_id}"' in html
        assert f'aria-label="{label}"' in html
    # Real <button> elements, not <a>/<div> click-handlers -- keyboard-
    # operable (Enter/Space) with no script changes needed for that.
    assert html.count('<button type="button" class="map-zoom-btn"') >= 2
    assert '<button type="button" class="map-zoom-btn map-zoom-btn--reset"' in html


def test_render_map_page_china_region_marker_popover_anchors_right():
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert 'class="map-popover map-popover--anchor-right" id="map-popover-deepseek"' in html


def test_render_map_page_west_coast_marker_popover_does_not_anchor_right():
    html = map_builder.render_map_page(REAL_COMPANIES, REAL_BOARD_ROWS, [])
    assert 'class="map-popover" id="map-popover-anthropic"' in html
    assert 'map-popover--anchor-right" id="map-popover-anthropic"' not in html


def test_write_map_page_zero_companies_still_omits_the_map_viewport_markup(tmp_path):
    # No markers -> the `{% if markers %}` branch (which builds the whole
    # .map-section/.map-viewport/.map-wrap/zoom-controls markup) doesn't
    # render at all -- matches the pre-existing "No companies are tracked
    # yet" empty-state branch, unchanged by this rebuild. The page's
    # <style> block (shared, unconditional CSS) still mentions these
    # class names either way, so this checks for the actual markup tags,
    # not bare substring presence.
    env = map_builder.build_jinja_env()
    path = map_builder.write_map_page(env, [], [], [], tmp_path)
    html = path.read_text(encoding="utf-8")
    assert 'id="map-viewport"' not in html
    assert 'class="map-zoom-controls"' not in html
    assert "No companies are tracked yet" in html


def test_write_map_page_writes_index_html(tmp_path):
    env = map_builder.build_jinja_env()
    path = map_builder.write_map_page(env, REAL_COMPANIES, REAL_BOARD_ROWS, [], tmp_path)
    assert path == tmp_path / "index.html"
    assert path.is_file()


def test_write_map_page_handles_zero_companies(tmp_path):
    env = map_builder.build_jinja_env()
    path = map_builder.write_map_page(env, [], [], [], tmp_path)
    html = path.read_text(encoding="utf-8")
    assert "No companies are tracked yet" in html
