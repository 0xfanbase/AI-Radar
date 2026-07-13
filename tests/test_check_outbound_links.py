"""Tests for scripts/check_outbound_links.py -- the outbound citation-link
CI gate (Phase 8, world-map UI reshape).

Uses ``requests-mock`` (this project's established deterministic HTTP test
tool -- see ``tests/test_auditor_linkrot.py``) for every redirect-chain
check; ``tests/conftest.py``'s autouse fixture would block a real network
call regardless. Pure functions (``classify_url``,
``diff_touches_trusted_domains``, URL extraction) are exercised directly
against fixture inputs, matching ``tests/test_check_path_allowlist.py``'s
own "fixture diff lists, no real git" convention where possible.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_outbound_links as mod  # noqa: E402
from watcher import http  # noqa: E402


TRUSTED = {
    "hostnames": ["anthropic.com", "arxiv.org", "reuters.com"],
    "path_scoped": [
        {"hostname": "github.com", "path_prefix": "/anthropics/"},
    ],
}


# --------------------------------------------------------------------------
# classify_url -- static, no-network vetting
# --------------------------------------------------------------------------


def test_classify_url_allowed_hostname_passes():
    result = mod.classify_url("https://anthropic.com/news/example", TRUSTED)
    assert result.ok is True


def test_classify_url_www_prefix_is_normalized():
    result = mod.classify_url("https://www.anthropic.com/news/example", TRUSTED)
    assert result.ok is True


def test_classify_url_disallowed_hostname_fails():
    result = mod.classify_url("https://not-trusted.example.com/page", TRUSTED)
    assert result.ok is False
    assert "not in data/trusted_domains.json" in result.reason


def test_classify_url_http_scheme_rejected():
    result = mod.classify_url("http://anthropic.com/news/example", TRUSTED)
    assert result.ok is False
    assert "https" in result.reason


def test_classify_url_ip_literal_host_rejected():
    result = mod.classify_url("https://192.168.1.1/page", TRUSTED)
    assert result.ok is False
    assert "IP literal" in result.reason


def test_classify_url_ipv6_literal_host_rejected():
    result = mod.classify_url("https://[::1]/page", TRUSTED)
    assert result.ok is False
    assert "IP literal" in result.reason


def test_classify_url_userinfo_rejected():
    result = mod.classify_url("https://user:pass@anthropic.com/page", TRUSTED)
    assert result.ok is False
    assert "userinfo" in result.reason


def test_classify_url_punycode_host_rejected():
    result = mod.classify_url("https://xn--80ak6aa92e.com/page", TRUSTED)
    assert result.ok is False
    assert "punycode" in result.reason


@pytest.mark.parametrize(
    "shortener_url",
    [
        "https://bit.ly/abc123",
        "https://t.co/abc123",
        "https://tinyurl.com/abc123",
        "https://goo.gl/abc123",
    ],
)
def test_classify_url_denylisted_shortener_rejected(shortener_url):
    result = mod.classify_url(shortener_url, TRUSTED)
    assert result.ok is False
    assert "shortener" in result.reason


def test_classify_url_path_scoped_match_passes():
    result = mod.classify_url("https://github.com/anthropics/claude-code", TRUSTED)
    assert result.ok is True


def test_classify_url_path_scoped_wrong_prefix_fails():
    result = mod.classify_url("https://github.com/someone-else/repo", TRUSTED)
    assert result.ok is False


# --------------------------------------------------------------------------
# diff_touches_trusted_domains -- the unconditional frozen-file guard
# --------------------------------------------------------------------------


def test_diff_touches_trusted_domains_true_when_present():
    assert mod.diff_touches_trusted_domains(
        ["content/companies/anthropic.json", "data/trusted_domains.json"]
    )


def test_diff_touches_trusted_domains_false_when_absent():
    assert not mod.diff_touches_trusted_domains(["content/companies/anthropic.json"])


def test_diff_touches_trusted_domains_regardless_of_other_changes():
    # A one-line, seemingly-benign addition to trusted_domains.json still
    # trips the guard -- there is no "safe" diff to this file.
    assert mod.diff_touches_trusted_domains(["data/trusted_domains.json"])


# --------------------------------------------------------------------------
# is_citation_bearing_path / changed_citation_files
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "content/companies/anthropic.json",
        "content/companies/deepseek.json",
        "content/cards/2026-07-09-example.json",
    ],
)
def test_is_citation_bearing_path_true(path):
    assert mod.is_citation_bearing_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "content/companies/index.json",
        "content/cards/index.json",
        "content/lexicon.json",
        "data/trusted_domains.json",
        "schemas/company.schema.json",
    ],
)
def test_is_citation_bearing_path_false(path):
    assert not mod.is_citation_bearing_path(path)


def test_changed_citation_files_filters_and_preserves_order():
    diff = [
        "content/companies/index.json",
        "content/companies/anthropic.json",
        "content/lexicon.json",
        "content/cards/2026-07-09-a.json",
        "content/cards/index.json",
    ]
    assert mod.changed_citation_files(diff) == [
        "content/companies/anthropic.json",
        "content/cards/2026-07-09-a.json",
    ]


# --------------------------------------------------------------------------
# extract_citation_urls_from_card / from_company
# --------------------------------------------------------------------------


def test_extract_citation_urls_from_card():
    card = {
        "citations": [
            {"url": "https://anthropic.com/news/a", "outlet": "Anthropic", "quote": "x"},
            {"url": "https://reuters.com/article", "outlet": "Reuters", "quote": "y"},
        ]
    }
    assert mod.extract_citation_urls_from_card(card) == [
        "https://anthropic.com/news/a",
        "https://reuters.com/article",
    ]


def test_extract_citation_urls_from_company_covers_every_profile_field():
    company = {
        "profile": {
            "overview": {
                "text": "x",
                "citations": [{"url": "https://anthropic.com/1", "outlet": "A", "quote": "q"}],
            },
            "what_theyve_done": [
                {
                    "text": "y",
                    "citations": [{"url": "https://anthropic.com/2", "outlet": "A", "quote": "q"}],
                }
            ],
            "strengths": [
                {
                    "text": "z",
                    "citations": [{"url": "https://anthropic.com/3", "outlet": "A", "quote": "q"}],
                }
            ],
            "current_focus": {
                "text": "w",
                "citations": [{"url": "https://anthropic.com/4", "outlet": "A", "quote": "q"}],
            },
            "roadmap": [
                {
                    "text": "v",
                    "citations": [{"url": "https://anthropic.com/5", "outlet": "A", "quote": "q"}],
                }
            ],
        }
    }
    urls = mod.extract_citation_urls_from_company(company)
    assert urls == [
        "https://anthropic.com/1",
        "https://anthropic.com/2",
        "https://anthropic.com/3",
        "https://anthropic.com/4",
        "https://anthropic.com/5",
    ]


def test_extract_citation_urls_from_company_handles_empty_roadmap():
    company = {
        "profile": {
            "overview": {"text": "x", "citations": []},
            "what_theyve_done": [],
            "strengths": [],
            "current_focus": {"text": "w", "citations": []},
            "roadmap": [],
        }
    }
    assert mod.extract_citation_urls_from_company(company) == []


# --------------------------------------------------------------------------
# resolve_final_url / check_citation_url -- network via requests_mock
# --------------------------------------------------------------------------


def test_resolve_final_url_no_redirect(requests_mock):
    requests_mock.head("https://anthropic.com/news/a", status_code=200)
    session = http.build_session()
    final_url, error = mod.resolve_final_url(session, "https://anthropic.com/news/a")
    assert error is None
    assert final_url == "https://anthropic.com/news/a"


def test_resolve_final_url_follows_redirect_chain(requests_mock):
    requests_mock.head(
        "https://anthropic.com/moved",
        status_code=301,
        headers={"Location": "https://anthropic.com/moved-to"},
    )
    requests_mock.head("https://anthropic.com/moved-to", status_code=200)
    session = http.build_session()
    final_url, error = mod.resolve_final_url(session, "https://anthropic.com/moved")
    assert error is None
    assert final_url == "https://anthropic.com/moved-to"


def test_resolve_final_url_head_unsupported_falls_back_to_get(requests_mock):
    requests_mock.head("https://anthropic.com/no-head", status_code=405)
    requests_mock.get("https://anthropic.com/no-head", status_code=200)
    session = http.build_session()
    final_url, error = mod.resolve_final_url(session, "https://anthropic.com/no-head")
    assert error is None
    assert final_url == "https://anthropic.com/no-head"


def test_resolve_final_url_network_error_returns_error_detail(requests_mock):
    requests_mock.head(
        "https://anthropic.com/slow", exc=requests.exceptions.ConnectTimeout
    )
    session = http.build_session()
    final_url, error = mod.resolve_final_url(session, "https://anthropic.com/slow")
    assert final_url is None
    assert error is not None


def test_check_citation_url_allowed_url_passes(requests_mock):
    requests_mock.head("https://anthropic.com/news/a", status_code=200)
    session = http.build_session()
    result = mod.check_citation_url(session, "https://anthropic.com/news/a", TRUSTED)
    assert result.ok is True


def test_check_citation_url_static_failure_never_hits_network(requests_mock):
    session = http.build_session()
    result = mod.check_citation_url(session, "http://anthropic.com/news/a", TRUSTED)
    assert result.ok is False
    assert requests_mock.call_count == 0


def test_check_citation_url_allowlisted_url_that_redirects_off_allowlist_fails(requests_mock):
    # The citation itself points at a trusted host, but that host's
    # current redirect target is off the allowlist -- a post-approval
    # hijack, exactly what step 4 exists to catch.
    requests_mock.head(
        "https://anthropic.com/moved-away",
        status_code=301,
        headers={"Location": "https://not-trusted.example.com/landing"},
    )
    requests_mock.head("https://not-trusted.example.com/landing", status_code=200)
    session = http.build_session()
    result = mod.check_citation_url(
        session, "https://anthropic.com/moved-away", TRUSTED
    )
    assert result.ok is False
    assert "redirects to a URL that fails vetting" in result.reason
    assert result.final_url == "https://not-trusted.example.com/landing"


def test_check_citation_url_unresolvable_redirect_fails_closed(requests_mock):
    requests_mock.head(
        "https://anthropic.com/broken", exc=requests.exceptions.ConnectionError
    )
    session = http.build_session()
    result = mod.check_citation_url(session, "https://anthropic.com/broken", TRUSTED)
    assert result.ok is False
    assert "could not resolve redirect chain" in result.reason


# --------------------------------------------------------------------------
# collect_violations -- the full orchestration, against real tmp files
# --------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_collect_violations_trusted_domains_diff_short_circuits_regardless_of_content(
    tmp_path, requests_mock
):
    # A completely benign, additive-looking change to trusted_domains.json
    # still hard-fails -- no citation content is even inspected.
    changed = ["data/trusted_domains.json", "content/companies/anthropic.json"]
    violations = mod.collect_violations(changed, repo_root=tmp_path, trusted=TRUSTED)
    assert len(violations) == 1
    assert "frozen and human-only" in violations[0]
    assert requests_mock.call_count == 0


def test_collect_violations_no_citation_files_passes():
    changed = ["content/lexicon.json", "schemas/company.schema.json"]
    assert mod.collect_violations(changed, repo_root=REPO_ROOT, trusted=TRUSTED) == []


def test_collect_violations_all_urls_pass(tmp_path, requests_mock):
    requests_mock.head("https://anthropic.com/news/a", status_code=200)
    _write_json(
        tmp_path / "content" / "companies" / "anthropic.json",
        {
            "profile": {
                "overview": {
                    "text": "x",
                    "citations": [
                        {"url": "https://anthropic.com/news/a", "outlet": "A", "quote": "q"}
                    ],
                },
                "what_theyve_done": [],
                "strengths": [],
                "current_focus": {"text": "w", "citations": []},
                "roadmap": [],
            }
        },
    )
    violations = mod.collect_violations(
        ["content/companies/anthropic.json"], repo_root=tmp_path, trusted=TRUSTED
    )
    assert violations == []


def test_collect_violations_reports_disallowed_domain(tmp_path, requests_mock):
    _write_json(
        tmp_path / "content" / "cards" / "2026-07-09-example.json",
        {
            "citations": [
                {"url": "https://not-trusted.example.com/x", "outlet": "X", "quote": "q"}
            ]
        },
    )
    violations = mod.collect_violations(
        ["content/cards/2026-07-09-example.json"], repo_root=tmp_path, trusted=TRUSTED
    )
    assert len(violations) == 1
    assert "not-trusted.example.com" in violations[0]
    assert "content/cards/2026-07-09-example.json" in violations[0]
    assert requests_mock.call_count == 0


def test_collect_violations_deleted_file_yields_no_urls(tmp_path):
    # The file named in the diff no longer exists on disk (a deletion) --
    # nothing to check, not an error.
    violations = mod.collect_violations(
        ["content/companies/deleted-co.json"], repo_root=tmp_path, trusted=TRUSTED
    )
    assert violations == []


# --------------------------------------------------------------------------
# main() -- exit code / stderr wiring
# --------------------------------------------------------------------------


def test_main_returns_zero_when_no_changed_citation_files(monkeypatch):
    monkeypatch.setattr(mod, "get_changed_files", lambda: ["content/lexicon.json"])
    assert mod.main() == 0


def test_main_returns_nonzero_when_trusted_domains_touched(monkeypatch, capsys):
    monkeypatch.setattr(
        mod, "get_changed_files", lambda: ["data/trusted_domains.json"]
    )
    exit_code = mod.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "frozen and human-only" in captured.err
