"""Tests for watcher/sources/arxiv.py.

`fixtures/arxiv_cs_ai_response.xml` is a REAL response captured live from
`https://export.arxiv.org/api/query` (cat:cs.AI, sortBy=submittedDate,
sortOrder=descending, max_results=12) on 2026-07-09 -- not fabricated.
Every test in this file is fixture/mock-based; `tests/conftest.py`'s
autouse guard would raise on any real network call here regardless.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from watcher import http
from watcher.models import Item
from watcher.sources import arxiv

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "arxiv_cs_ai_response.xml"


def _load_fixture_text() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# build_search_query / build_query_url
# --------------------------------------------------------------------------


def test_build_search_query_ors_all_three_categories():
    query = arxiv.build_search_query()
    assert query == "cat:cs.AI OR cat:cs.CL OR cat:cs.LG"


def test_build_query_url_sorts_by_submitted_date_descending_and_encodes_query():
    url = arxiv.build_query_url()

    assert url.startswith(f"{arxiv.ARXIV_API_BASE_URL}?")
    assert "search_query=cat%3Acs.AI+OR+cat%3Acs.CL+OR+cat%3Acs.LG" in url
    assert "sortBy=submittedDate" in url
    assert "sortOrder=descending" in url
    assert f"max_results={arxiv.ARXIV_MAX_RESULTS}" in url


def test_build_query_url_honors_custom_categories_and_max_results():
    url = arxiv.build_query_url(categories=("cs.CL",), max_results=5)

    assert "search_query=cat%3Acs.CL" in url
    assert "max_results=5" in url


# --------------------------------------------------------------------------
# parse_arxiv_atom -- against the real captured fixture
# --------------------------------------------------------------------------


def test_parse_arxiv_atom_returns_one_item_per_entry():
    items = arxiv.parse_arxiv_atom(_load_fixture_text())

    assert len(items) == 12
    assert all(isinstance(item, Item) for item in items)


def test_parse_arxiv_atom_normalizes_first_real_entry():
    items = arxiv.parse_arxiv_atom(_load_fixture_text())
    first = items[0]

    assert first.source_type == "arxiv"
    assert first.source_name == "arxiv"
    assert first.title == (
        "Accurate, Interdisciplinary and Transparent Structure-property "
        "Understanding with Deep Native Structural Reasoning"
    )
    assert first.url == "https://arxiv.org/abs/2607.07708v1"
    assert first.published_at == "2026-07-08T17:59:59Z"
    assert first.points is None
    assert first.num_comments is None
    assert first.extra["primary_category"] == "cs.CL"
    assert first.extra["categories"] == ["cs.CL", "cs.AI", "cs.CE", "cs.LG"]
    assert len(first.extra["authors"]) == 29
    assert first.extra["authors"][0] == "Chen Tang"
    assert first.extra["summary"].startswith("Structure-property relationships are foundational")


def test_parse_arxiv_atom_every_item_is_well_formed():
    items = arxiv.parse_arxiv_atom(_load_fixture_text())

    for item in items:
        assert item.title.strip() == item.title
        assert "  " not in item.title  # no double-spaces after normalization
        assert "\n" not in item.title
        assert item.url.startswith("https://arxiv.org/abs/")
        assert item.published_at  # non-empty ISO timestamp string
        assert item.extra["primary_category"] in item.extra["categories"] or (
            item.extra["primary_category"] is not None
        )


def test_parse_arxiv_atom_handles_single_author_and_non_ai_primary_category():
    # Real entry #4 in the fixture: single author, cs.AI is a secondary
    # category (primary is cs.GT-adjacent cross-listing scenario covered
    # by entry #3 below) -- exercises the "not every arXiv hit is
    # primarily cs.AI" shape.
    items = arxiv.parse_arxiv_atom(_load_fixture_text())
    institutional_red_teaming = items[3]

    assert institutional_red_teaming.extra["authors"] == ["Yujiao Chen"]
    assert institutional_red_teaming.extra["categories"] == ["cs.AI", "cs.GT", "cs.MA"]


def test_parse_arxiv_atom_handles_cross_listed_primary_category_outside_cs():
    # Real entry #3 in the fixture: cross-listed into cs.AI but its
    # primary_category is cs.DB -- confirms we record arXiv's own
    # primary_category rather than assuming it's always one of the three
    # queried categories.
    items = arxiv.parse_arxiv_atom(_load_fixture_text())
    database_bypass = items[2]

    assert database_bypass.extra["primary_category"] == "cs.DB"
    assert database_bypass.extra["categories"] == ["cs.DB", "cs.AI"]


def test_parse_arxiv_atom_skips_entries_missing_title_or_url():
    atom = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/9999.00001v1</id>
    <title></title>
    <link href="https://arxiv.org/abs/9999.00001v1" rel="alternate" type="text/html"/>
    <published>2026-07-09T00:00:00Z</published>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/9999.00002v1</id>
    <title>A Perfectly Fine Paper Title</title>
    <link href="https://arxiv.org/abs/9999.00002v1" rel="alternate" type="text/html"/>
    <published>2026-07-09T00:00:00Z</published>
    <arxiv:primary_category term="cs.AI"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""
    items = arxiv.parse_arxiv_atom(atom)

    assert len(items) == 1
    assert items[0].title == "A Perfectly Fine Paper Title"


def test_parse_arxiv_atom_falls_back_to_id_when_no_alternate_link():
    atom = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/9999.00003v1</id>
    <title>No Alternate Link Here</title>
    <published>2026-07-09T00:00:00Z</published>
  </entry>
</feed>
"""
    items = arxiv.parse_arxiv_atom(atom)

    assert len(items) == 1
    assert items[0].url == "http://arxiv.org/abs/9999.00003v1"


# --------------------------------------------------------------------------
# Whitespace normalization -- CLAUDE.md/plan requirement, exercised against
# a hand-built minimal Atom snippet mimicking arXiv's real-world
# line-wrapped titles (a small synthetic edge case, not a saved fixture --
# per CLAUDE.md, inline strings are fine where the payload genuinely isn't
# realistically sized).
# --------------------------------------------------------------------------


def test_whitespace_normalization_collapses_newlines_and_indentation_in_title():
    atom = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/9999.00004v1</id>
    <title>  A Title That Wraps
      Across Several
      Lines With   Extra   Spaces  </title>
    <link href="https://arxiv.org/abs/9999.00004v1" rel="alternate" type="text/html"/>
    <summary>An abstract that
      also wraps
      across lines.</summary>
    <published>2026-07-09T00:00:00Z</published>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""
    items = arxiv.parse_arxiv_atom(atom)

    assert len(items) == 1
    assert items[0].title == "A Title That Wraps Across Several Lines With Extra Spaces"
    assert items[0].extra["summary"] == "An abstract that also wraps across lines."


# --------------------------------------------------------------------------
# fetch_arxiv_items -- integration through watcher/http.py's shared fetch
# discipline layer, via requests_mock (no real network).
# --------------------------------------------------------------------------


def test_fetch_arxiv_items_proceeds_despite_robots_disallow_per_documented_api_exemption(
    requests_mock, tmp_path
):
    # This is the REAL robots.txt content live-fetched from
    # https://export.arxiv.org/robots.txt on 2026-07-09 -- it disallows
    # every path for every user agent. Per the Phase 1 PM checkpoint's
    # resolved decision (CLAUDE.md's narrow documented-API exception,
    # `watcher.config.ROBOTS_EXEMPT_API_HOSTS`), `export.arxiv.org` is
    # centrally exempted from the robots.txt gate because arXiv's own
    # published API Terms of Use -- not this crawl directive -- governs
    # this documented, keyless API endpoint. The fetcher must now proceed
    # and return parsed items even against this exact disallow body; no
    # request to this mocked robots.txt URL should even be made, since the
    # exemption short-circuits before it's ever fetched.
    requests_mock.get(
        "https://export.arxiv.org/robots.txt",
        text="User-agent: * \nDisallow: /\n",
    )
    query_url = arxiv.build_query_url()
    requests_mock.get(query_url, text=_load_fixture_text())

    session = http.build_session()
    items = arxiv.fetch_arxiv_items(session, cache_dir=tmp_path)

    assert len(items) == 12
    assert items[0].source_type == "arxiv"

    # The exemption short-circuits inside check_robots_allowed() before any
    # HTTP call -- confirm robots.txt itself was never actually fetched.
    robots_calls = [
        req for req in requests_mock.request_history
        if req.url.startswith("https://export.arxiv.org/robots.txt")
    ]
    assert robots_calls == []

    query_calls = [
        req for req in requests_mock.request_history
        if req.url.startswith(arxiv.ARXIV_API_BASE_URL)
    ]
    assert len(query_calls) == 1


def test_fetch_arxiv_items_parses_real_fixture_through_shared_http_layer(
    requests_mock, tmp_path
):
    requests_mock.get(
        "https://export.arxiv.org/robots.txt",
        text="User-agent: *\nAllow: /\n",
    )
    query_url = arxiv.build_query_url()
    requests_mock.get(query_url, text=_load_fixture_text())

    session = http.build_session()
    items = arxiv.fetch_arxiv_items(session, cache_dir=tmp_path)

    assert len(items) == 12
    assert items[0].title == (
        "Accurate, Interdisciplinary and Transparent Structure-property "
        "Understanding with Deep Native Structural Reasoning"
    )
    assert items[0].source_type == "arxiv"


def test_fetch_arxiv_items_sends_descriptive_user_agent(requests_mock, tmp_path):
    from watcher import config

    requests_mock.get(
        "https://export.arxiv.org/robots.txt",
        text="User-agent: *\nAllow: /\n",
    )
    query_url = arxiv.build_query_url()
    requests_mock.get(query_url, text=_load_fixture_text())

    session = http.build_session()
    arxiv.fetch_arxiv_items(session, cache_dir=tmp_path)

    query_request = next(
        req for req in requests_mock.request_history
        if req.url.startswith(arxiv.ARXIV_API_BASE_URL)
    )
    assert query_request.headers["User-Agent"] == config.USER_AGENT


@pytest.mark.live
def test_live_arxiv_api_is_reachable_and_returns_parseable_entries():
    """Opt-in live smoke test -- excluded by default (pytest.ini: -m "not
    live"); run explicitly with `python -m pytest -m live`. Confirms the
    real endpoint still returns a parseable Atom feed; does not assert on
    robots.txt policy (see the mocked tests above for that behavior).
    """
    session = http.build_session()
    query_url = arxiv.build_query_url(max_results=3)
    response = session.get(query_url, timeout=10)
    response.raise_for_status()

    items = arxiv.parse_arxiv_atom(response.text)

    assert len(items) >= 1
    assert all(item.title for item in items)
