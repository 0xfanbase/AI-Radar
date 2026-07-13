"""Tests for auditor/linkrot.py -- the weekly link-rot check.

Covers, per this turn's task scope: HEAD-first checking with a GET
fallback only when HEAD itself signals "not supported" (405/501),
redirect-following on both verbs, and classification into exactly three
buckets (ok/dead/unreachable) across a realistic mix of 200/404/410/500/
timeout responses -- proving dead and unreachable are genuinely distinct
categories, not two names for the same bucket. Also covers
``collect_citation_urls``/``load_cards`` (the card -> URL plumbing) and
``audit_link_rot`` (the top-level entry point), using an explicit
``cards`` list throughout since ``content/cards/`` is empty in this repo
today.

Uses ``requests-mock`` (this project's established deterministic HTTP
test tool -- see tests/test_fetch_discipline.py) rather than any real
network call; ``tests/conftest.py``'s autouse fixture would block a real
call regardless.
"""
from __future__ import annotations

import requests
import requests_mock as requests_mock_lib

from auditor import linkrot
from watcher import http


# --------------------------------------------------------------------------
# classify_status_code
# --------------------------------------------------------------------------


def test_classify_2xx_is_ok():
    assert linkrot.classify_status_code(200) == "ok"
    assert linkrot.classify_status_code(204) == "ok"
    assert linkrot.classify_status_code(299) == "ok"


def test_classify_404_and_410_are_dead():
    assert linkrot.classify_status_code(404) == "dead"
    assert linkrot.classify_status_code(410) == "dead"


def test_classify_5xx_is_unreachable():
    assert linkrot.classify_status_code(500) == "unreachable"
    assert linkrot.classify_status_code(502) == "unreachable"
    assert linkrot.classify_status_code(503) == "unreachable"


def test_classify_other_4xx_is_unreachable_not_dead():
    # 403/401/429 mean "we couldn't verify" (often bot-blocking, per this
    # project's own real Frontier Board fetch history), not "confirmed
    # gone" -- only 404/410 are ever "dead".
    assert linkrot.classify_status_code(403) == "unreachable"
    assert linkrot.classify_status_code(401) == "unreachable"
    assert linkrot.classify_status_code(429) == "unreachable"


# --------------------------------------------------------------------------
# check_url -- HEAD/GET, classification, redirects, fallback
# --------------------------------------------------------------------------


def test_check_url_head_200_is_ok(requests_mock):
    requests_mock.head("https://example.test/ok", status_code=200)

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/ok")

    assert result.status == "ok"
    assert result.http_status == 200
    assert result.method == "HEAD"
    assert result.detail is None
    # Only HEAD was ever called -- no GET fallback for a plain 200.
    assert requests_mock.call_count == 1
    assert requests_mock.request_history[0].method == "HEAD"


def test_check_url_head_404_is_dead(requests_mock):
    requests_mock.head("https://example.test/gone", status_code=404)

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/gone")

    assert result.status == "dead"
    assert result.http_status == 404
    assert result.method == "HEAD"


def test_check_url_head_410_is_dead(requests_mock):
    requests_mock.head("https://example.test/gone-forever", status_code=410)

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/gone-forever")

    assert result.status == "dead"
    assert result.http_status == 410


def test_check_url_head_500_is_unreachable(requests_mock):
    requests_mock.head("https://example.test/broken", status_code=500)

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/broken")

    assert result.status == "unreachable"
    assert result.http_status == 500


def test_check_url_timeout_is_unreachable_with_no_http_status(requests_mock):
    requests_mock.head(
        "https://example.test/slow", exc=requests.exceptions.ConnectTimeout
    )

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/slow")

    assert result.status == "unreachable"
    assert result.http_status is None
    assert result.detail is not None
    assert "timeout" in result.detail.lower()


def test_check_url_connection_error_is_unreachable(requests_mock):
    requests_mock.head(
        "https://example.test/unreachable-host",
        exc=requests.exceptions.ConnectionError,
    )

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/unreachable-host")

    assert result.status == "unreachable"
    assert result.http_status is None
    assert "connection" in result.detail.lower()


def test_check_url_never_raises_on_connection_error(requests_mock):
    # The function itself must never raise -- callers rely on this to
    # check a whole batch of citation URLs without one bad link crashing
    # the run.
    requests_mock.head(
        "https://example.test/boom", exc=requests.exceptions.ConnectionError
    )
    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/boom")
    assert result.status == "unreachable"


def test_check_url_falls_back_to_get_when_head_not_supported(requests_mock):
    requests_mock.head("https://example.test/head-unsupported", status_code=405)
    requests_mock.get(
        "https://example.test/head-unsupported", status_code=200, text="ok body"
    )

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/head-unsupported")

    assert result.status == "ok"
    assert result.http_status == 200
    assert result.method == "GET"
    # Both verbs were actually exercised, HEAD first.
    assert requests_mock.call_count == 2
    assert requests_mock.request_history[0].method == "HEAD"
    assert requests_mock.request_history[1].method == "GET"


def test_check_url_falls_back_to_get_on_501_not_implemented(requests_mock):
    requests_mock.head("https://example.test/not-implemented", status_code=501)
    requests_mock.get(
        "https://example.test/not-implemented", status_code=404, text="missing"
    )

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/not-implemented")

    # The GET fallback's own status is what gets classified -- here it's a
    # real 404, proving the fallback path is fully wired through
    # classify_status_code too, not just short-circuited to "ok".
    assert result.status == "dead"
    assert result.method == "GET"


def test_check_url_does_not_fall_back_to_get_on_ordinary_404(requests_mock):
    # A plain 404 on HEAD is a complete answer on its own -- must not
    # trigger a GET fallback (that's reserved for 405/501 only).
    requests_mock.head("https://example.test/plain-404", status_code=404)

    session = http.build_session()
    linkrot.check_url(session, "https://example.test/plain-404")

    assert requests_mock.call_count == 1
    assert requests_mock.request_history[0].method == "HEAD"


def test_check_url_follows_redirects_to_final_status(requests_mock):
    # requests.Session.head() defaults allow_redirects to False (unlike
    # every other verb) -- this proves check_url overrides that so an
    # ordinary 301 (citation URL moved, not dead) resolves to the real
    # final status rather than being misclassified as some other bucket.
    requests_mock.head(
        "https://example.test/moved",
        status_code=301,
        headers={"Location": "https://example.test/moved-to"},
    )
    requests_mock.head("https://example.test/moved-to", status_code=200)

    session = http.build_session()
    result = linkrot.check_url(session, "https://example.test/moved")

    assert result.status == "ok"
    assert result.http_status == 200


# --------------------------------------------------------------------------
# check_links
# --------------------------------------------------------------------------


def test_check_links_checks_every_url_in_order(requests_mock):
    requests_mock.head("https://example.test/a", status_code=200)
    requests_mock.head("https://example.test/b", status_code=404)

    session = http.build_session()
    results = linkrot.check_links(
        session, ["https://example.test/a", "https://example.test/b"]
    )

    assert [r.url for r in results] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert [r.status for r in results] == ["ok", "dead"]


# --------------------------------------------------------------------------
# collect_citation_urls / load_cards
# --------------------------------------------------------------------------


def test_collect_citation_urls_dedupes_preserving_first_seen_order():
    cards = [
        {
            "citations": [
                {"url": "https://a.test/1", "outlet": "A", "quote": "x"},
                {"url": "https://b.test/1", "outlet": "B", "quote": "y"},
            ]
        },
        {
            "citations": [
                # Same URL as above -- must not be duplicated.
                {"url": "https://a.test/1", "outlet": "A", "quote": "z"},
                {"url": "https://c.test/1", "outlet": "C", "quote": "w"},
            ]
        },
    ]

    urls = linkrot.collect_citation_urls(cards)

    assert urls == ["https://a.test/1", "https://b.test/1", "https://c.test/1"]


def test_collect_citation_urls_handles_card_with_no_citations_key():
    cards = [{"headline": "no citations field at all"}]
    assert linkrot.collect_citation_urls(cards) == []


def test_collect_citation_urls_empty_cards_list():
    assert linkrot.collect_citation_urls([]) == []


def test_load_cards_returns_empty_list_when_cards_dir_missing(tmp_path):
    missing_dir = tmp_path / "does-not-exist"
    assert linkrot.load_cards(missing_dir) == []


def test_load_cards_skips_index_json_and_loads_real_cards(tmp_path):
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "index.json").write_text('{"version": 1, "cards": []}')
    (cards_dir / "2026-07-09-example.json").write_text(
        '{"id": "2026-07-09-example", "citations": []}'
    )

    cards = linkrot.load_cards(cards_dir)

    assert len(cards) == 1
    assert cards[0]["id"] == "2026-07-09-example"


# --------------------------------------------------------------------------
# audit_link_rot -- top-level entry point, realistic 200/404/410/500/timeout
# mix, proving dead vs unreachable are genuinely distinct categories.
# --------------------------------------------------------------------------


def _card_with_citation(url: str) -> dict:
    return {"citations": [{"url": url, "outlet": "Test", "quote": "q"}]}


def test_audit_link_rot_classifies_a_realistic_mix_correctly(requests_mock):
    requests_mock.head("https://example.test/live", status_code=200)
    requests_mock.head("https://example.test/removed", status_code=404)
    requests_mock.head("https://example.test/gone-forever", status_code=410)
    requests_mock.head("https://example.test/down", status_code=500)
    requests_mock.head(
        "https://example.test/timed-out", exc=requests.exceptions.Timeout
    )

    cards = [
        _card_with_citation("https://example.test/live"),
        _card_with_citation("https://example.test/removed"),
        _card_with_citation("https://example.test/gone-forever"),
        _card_with_citation("https://example.test/down"),
        _card_with_citation("https://example.test/timed-out"),
    ]

    report = linkrot.audit_link_rot(cards)

    assert report["total_urls"] == 5
    assert report["counts"] == {"ok": 1, "dead": 2, "unreachable": 2}

    by_url = {r["url"]: r for r in report["results"]}
    assert by_url["https://example.test/live"]["status"] == "ok"
    assert by_url["https://example.test/removed"]["status"] == "dead"
    assert by_url["https://example.test/gone-forever"]["status"] == "dead"
    assert by_url["https://example.test/down"]["status"] == "unreachable"
    assert by_url["https://example.test/timed-out"]["status"] == "unreachable"

    # Dead and unreachable really are distinct buckets, not aliases of one
    # another -- a 404/410 and a 500/timeout must never collapse together.
    dead_urls = {r["url"] for r in report["results"] if r["status"] == "dead"}
    unreachable_urls = {
        r["url"] for r in report["results"] if r["status"] == "unreachable"
    }
    assert dead_urls == {
        "https://example.test/removed",
        "https://example.test/gone-forever",
    }
    assert unreachable_urls == {
        "https://example.test/down",
        "https://example.test/timed-out",
    }
    assert dead_urls.isdisjoint(unreachable_urls)


def test_audit_link_rot_does_not_retry_a_5xx_within_the_same_run(requests_mock):
    # Only one HEAD response is registered for the always-down URL; if
    # audit_link_rot retried it within this same run, requests-mock would
    # keep replaying the single registered response (it doesn't raise on
    # "ran out of responses" the way a strict sequence mock would), so
    # this test instead asserts the call count directly: exactly one
    # attempt for a 5xx, never more.
    requests_mock.head("https://example.test/always-down", status_code=503)

    report = linkrot.audit_link_rot(
        [_card_with_citation("https://example.test/always-down")]
    )

    assert report["counts"]["unreachable"] == 1
    assert requests_mock.call_count == 1


def test_audit_link_rot_with_no_cards_and_no_urls_is_a_clean_zero_report(tmp_path):
    report = linkrot.audit_link_rot([], cards_dir=tmp_path / "nonexistent")

    assert report["total_urls"] == 0
    assert report["counts"] == {"ok": 0, "dead": 0, "unreachable": 0}
    assert report["results"] == []


def test_audit_link_rot_loads_cards_from_cards_dir_when_none_passed(
    requests_mock, tmp_path
):
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "2026-07-09-example.json").write_text(
        '{"id": "x", "citations": [{"url": "https://example.test/from-disk", '
        '"outlet": "Test", "quote": "q"}]}'
    )
    requests_mock.head("https://example.test/from-disk", status_code=200)

    report = linkrot.audit_link_rot(cards_dir=cards_dir)

    assert report["total_urls"] == 1
    assert report["counts"]["ok"] == 1


def test_audit_link_rot_builds_its_own_session_when_none_passed(requests_mock):
    # No explicit `session=` argument -- proves the default path really
    # does build a working watcher.http session rather than requiring the
    # caller to always supply one.
    requests_mock.head("https://example.test/default-session", status_code=200)

    report = linkrot.audit_link_rot(
        [_card_with_citation("https://example.test/default-session")]
    )

    assert report["counts"]["ok"] == 1


def test_audit_link_rot_reuses_the_real_watcher_http_build_session(
    requests_mock, monkeypatch
):
    # Proves this module actually reuses watcher.http.build_session()
    # (per this turn's own instruction), not a hand-rolled session --
    # wraps the real function and asserts it was actually called.
    calls = []
    original_build_session = http.build_session

    def spy_build_session(*args, **kwargs):
        calls.append((args, kwargs))
        return original_build_session(*args, **kwargs)

    monkeypatch.setattr(http, "build_session", spy_build_session)
    monkeypatch.setattr(linkrot.http, "build_session", spy_build_session)

    requests_mock.head("https://example.test/spy", status_code=200)
    linkrot.audit_link_rot([_card_with_citation("https://example.test/spy")])

    assert len(calls) == 1


# --------------------------------------------------------------------------
# Phase 8: check_hijack / check_hijacks / audit_hijacked_links -- the
# weekly post-publication hijack check.
# --------------------------------------------------------------------------

TRUSTED = {"hostnames": ["anthropic.com"], "path_scoped": []}


def test_check_hijack_trusted_no_redirect(requests_mock):
    requests_mock.head("https://anthropic.com/news/a", status_code=200)
    session = http.build_session()

    result = linkrot.check_hijack(session, "https://anthropic.com/news/a", TRUSTED)

    assert result.status == "trusted"
    assert result.final_url == "https://anthropic.com/news/a"
    assert result.detail is None


def test_check_hijack_trusted_redirect_stays_on_allowlist(requests_mock):
    requests_mock.head(
        "https://anthropic.com/moved",
        status_code=301,
        headers={"Location": "https://anthropic.com/moved-to"},
    )
    requests_mock.head("https://anthropic.com/moved-to", status_code=200)
    session = http.build_session()

    result = linkrot.check_hijack(session, "https://anthropic.com/moved", TRUSTED)

    assert result.status == "trusted"
    assert result.final_url == "https://anthropic.com/moved-to"


def test_check_hijack_flags_a_redirect_off_the_allowlist(requests_mock):
    # The citation was trusted at commit time (anthropic.com), but now
    # redirects somewhere off the allowlist -- a post-commit hijack.
    requests_mock.head(
        "https://anthropic.com/hijacked",
        status_code=301,
        headers={"Location": "https://squatted.example.com/landing"},
    )
    requests_mock.head("https://squatted.example.com/landing", status_code=200)
    session = http.build_session()

    result = linkrot.check_hijack(session, "https://anthropic.com/hijacked", TRUSTED)

    assert result.status == "hijacked"
    assert result.final_url == "https://squatted.example.com/landing"
    assert result.detail is not None


def test_check_hijack_unreachable_is_its_own_bucket_not_hijacked(requests_mock):
    requests_mock.head(
        "https://anthropic.com/down", exc=requests.exceptions.ConnectionError
    )
    session = http.build_session()

    result = linkrot.check_hijack(session, "https://anthropic.com/down", TRUSTED)

    assert result.status == "unreachable"
    assert result.final_url is None
    assert result.detail is not None


def test_check_hijacks_checks_every_url_in_order(requests_mock):
    requests_mock.head("https://anthropic.com/a", status_code=200)
    requests_mock.head(
        "https://anthropic.com/b",
        status_code=301,
        headers={"Location": "https://squatted.example.com/c"},
    )
    requests_mock.head("https://squatted.example.com/c", status_code=200)
    session = http.build_session()

    results = linkrot.check_hijacks(
        session, ["https://anthropic.com/a", "https://anthropic.com/b"], TRUSTED
    )

    assert [r.status for r in results] == ["trusted", "hijacked"]


def test_audit_hijacked_links_summary_shape(requests_mock):
    requests_mock.head("https://anthropic.com/ok", status_code=200)
    requests_mock.head(
        "https://anthropic.com/hijacked",
        status_code=301,
        headers={"Location": "https://squatted.example.com/x"},
    )
    requests_mock.head("https://squatted.example.com/x", status_code=200)
    requests_mock.head(
        "https://anthropic.com/down", exc=requests.exceptions.ConnectionError
    )

    cards = [
        _card_with_citation("https://anthropic.com/ok"),
        _card_with_citation("https://anthropic.com/hijacked"),
        _card_with_citation("https://anthropic.com/down"),
    ]

    report = linkrot.audit_hijacked_links(cards, trusted=TRUSTED)

    assert report["total_urls"] == 3
    assert report["counts"] == {"trusted": 1, "hijacked": 1, "unreachable": 1}
    assert set(report.keys()) == {"checked_at", "total_urls", "counts", "results"}
    by_url = {r["url"]: r for r in report["results"]}
    assert by_url["https://anthropic.com/hijacked"]["status"] == "hijacked"
    assert by_url["https://anthropic.com/hijacked"]["final_url"] == (
        "https://squatted.example.com/x"
    )


def test_audit_hijacked_links_with_no_cards_is_a_clean_zero_report(tmp_path):
    report = linkrot.audit_hijacked_links(
        [], cards_dir=tmp_path / "nonexistent", trusted=TRUSTED
    )

    assert report["total_urls"] == 0
    assert report["counts"] == {"trusted": 0, "hijacked": 0, "unreachable": 0}
    assert report["results"] == []


def test_audit_hijacked_links_loads_trusted_domains_from_disk_when_none_passed(
    requests_mock,
):
    # No explicit trusted= argument -- proves the default path really does
    # load the real, committed data/trusted_domains.json (which lists
    # anthropic.com) rather than requiring a caller to always supply one.
    requests_mock.head("https://anthropic.com/real-file", status_code=200)

    report = linkrot.audit_hijacked_links(
        [_card_with_citation("https://anthropic.com/real-file")]
    )

    assert report["counts"]["trusted"] == 1


# --------------------------------------------------------------------------
# Phase 9: collect_company_citation_urls / audit_company_hijacked_links --
# the same hijack re-check as Phase 8's audit_hijacked_links, but over
# content/companies/*.json profile citations instead of
# content/cards/*.json's, with each result attributable to a company_id.
# --------------------------------------------------------------------------


def _company_with_citations(company_id: str, urls: list[str]) -> dict:
    """Minimal `schemas/company.schema.json`-shaped dict carrying one
    citedText citation per url in `urls`, all under `profile.overview`
    plus one more under `profile.current_focus` (so a real call exercises
    more than one `citedText` field, matching
    `scripts.check_outbound_links.extract_citation_urls_from_company`'s
    own multi-field flattening) -- only the fields that function and
    `audit_company_hijacked_links` actually read."""
    citations = [{"url": url, "outlet": "Test", "quote": "q"} for url in urls]
    return {
        "id": company_id,
        "profile": {
            "overview": {"text": "x", "citations": citations[:1] or citations},
            "current_focus": {"text": "y", "citations": citations[1:] or citations[:0]},
        },
    }


def test_collect_company_citation_urls_pairs_each_url_with_its_company_id():
    companies = [
        _company_with_citations("anthropic", ["https://anthropic.com/a"]),
        _company_with_citations("openai", ["https://openai.com/b"]),
    ]
    pairs = linkrot.collect_company_citation_urls(companies)
    assert pairs == [
        ("anthropic", "https://anthropic.com/a"),
        ("openai", "https://openai.com/b"),
    ]


def test_collect_company_citation_urls_dedupes_within_one_company_only():
    same_url = "https://anthropic.com/a"
    companies = [
        {
            "id": "anthropic",
            "profile": {
                "overview": {
                    "text": "x",
                    "citations": [
                        {"url": same_url, "outlet": "Test", "quote": "q"}
                    ],
                },
                "current_focus": {
                    "text": "y",
                    "citations": [
                        {"url": same_url, "outlet": "Test", "quote": "q"}
                    ],
                },
            },
        },
        _company_with_citations("openai", [same_url]),
    ]
    pairs = linkrot.collect_company_citation_urls(companies)
    # Deduped within "anthropic" (same_url appears twice in its own
    # profile) but "openai" citing the identical URL is a separate,
    # independent pair -- see collect_company_citation_urls's own
    # docstring for why.
    assert pairs == [("anthropic", same_url), ("openai", same_url)]


def test_collect_company_citation_urls_empty_companies():
    assert linkrot.collect_company_citation_urls([]) == []


def test_audit_company_hijacked_links_summary_shape(requests_mock):
    requests_mock.head("https://anthropic.com/ok", status_code=200)
    requests_mock.head(
        "https://anthropic.com/hijacked",
        status_code=301,
        headers={"Location": "https://squatted.example.com/x"},
    )
    requests_mock.head("https://squatted.example.com/x", status_code=200)
    requests_mock.head(
        "https://openai.com/down", exc=requests.exceptions.ConnectionError
    )

    companies = [
        _company_with_citations(
            "anthropic", ["https://anthropic.com/ok", "https://anthropic.com/hijacked"]
        ),
        _company_with_citations("openai", ["https://openai.com/down"]),
    ]

    report = linkrot.audit_company_hijacked_links(companies, trusted=TRUSTED)

    assert report["total_urls"] == 3
    assert report["counts"] == {"trusted": 1, "hijacked": 1, "unreachable": 1}
    assert set(report.keys()) == {"checked_at", "total_urls", "counts", "results"}
    by_url = {r["url"]: r for r in report["results"]}
    assert by_url["https://anthropic.com/hijacked"]["status"] == "hijacked"
    assert by_url["https://anthropic.com/hijacked"]["company_id"] == "anthropic"
    assert by_url["https://anthropic.com/hijacked"]["final_url"] == (
        "https://squatted.example.com/x"
    )
    assert by_url["https://openai.com/down"]["company_id"] == "openai"
    assert by_url["https://openai.com/down"]["status"] == "unreachable"


def test_audit_company_hijacked_links_with_no_companies_is_a_clean_zero_report(
    tmp_path,
):
    report = linkrot.audit_company_hijacked_links(
        [], companies_dir=tmp_path / "nonexistent", trusted=TRUSTED
    )

    assert report["total_urls"] == 0
    assert report["counts"] == {"trusted": 0, "hijacked": 0, "unreachable": 0}
    assert report["results"] == []


def test_audit_company_hijacked_links_loads_company_registry_from_disk_when_none_passed(
    requests_mock,
):
    # No explicit companies= argument -- proves the default path really
    # does load the real, committed content/companies/*.json registry
    # (13 real profiles, each with real citation URLs across many
    # different domains). A catch-all mock (every HEAD returns 200, no
    # redirect) means every result resolves to its own original host --
    # this test only needs to prove real profiles were actually loaded
    # and checked (total_urls > 0, one result per real citation host),
    # not to assert a specific trusted/hijacked split against the real,
    # full registry (that would make this test brittle to future profile
    # edits unrelated to this function).
    requests_mock.head(requests_mock_lib.ANY, status_code=200)

    real_companies = linkrot.load_company_registry()
    expected_total = len(linkrot.collect_company_citation_urls(real_companies))

    report = linkrot.audit_company_hijacked_links(trusted=TRUSTED)

    assert expected_total > 0
    assert report["total_urls"] == expected_total
    assert sum(report["counts"].values()) == expected_total
