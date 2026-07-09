"""Fetch-discipline tests for watcher/http.py, using requests-mock.

Covers, per CLAUDE.md's fetch-discipline rules: the descriptive User-Agent
is always sent, a timeout is always passed on every request, exponential
backoff kicks in on retryable statuses, a cached ETag is sent as
If-None-Match and a 304 short-circuits reparse by reusing the cached body,
and a robots.txt disallow (or any other failure evaluating it) causes a
clean skip -- never a raise, never a crash, never a circumvention.

Every test passes an explicit `cache_dir` (pytest's `tmp_path`) so no test
ever touches the repo's real data/.cache/ directory.
"""
import requests

from watcher import config, http


def test_user_agent_header_is_sent(requests_mock, tmp_path):
    requests_mock.get("https://example.test/feed", text="ok")

    session = http.build_session()
    result = http.fetch(session, "https://example.test/feed", cache_dir=tmp_path)

    assert result.status_code == 200
    sent_headers = requests_mock.request_history[0].headers
    assert sent_headers["User-Agent"] == config.USER_AGENT


def test_timeout_is_always_passed(requests_mock, monkeypatch, tmp_path):
    requests_mock.get("https://example.test/feed", text="ok")

    captured_timeouts = []
    original_request = requests.Session.request

    def spy_request(self, method, url, *args, **kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        return original_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", spy_request)

    session = http.build_session()
    http.fetch(session, "https://example.test/feed", cache_dir=tmp_path)

    assert captured_timeouts == [config.REQUEST_TIMEOUT_SECONDS]
    assert all(t is not None for t in captured_timeouts)


def test_exponential_backoff_on_503_503_200(requests_mock, monkeypatch, tmp_path):
    requests_mock.get(
        "https://example.test/flaky",
        [
            {"status_code": 503, "text": "unavailable"},
            {"status_code": 503, "text": "unavailable"},
            {"status_code": 200, "text": "recovered"},
        ],
    )

    sleeps = []
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))

    session = http.build_session()
    result = http.fetch(session, "https://example.test/flaky", cache_dir=tmp_path)

    assert result.status_code == 200
    assert result.text == "recovered"
    assert requests_mock.call_count == 3
    # Exponential backoff: base * 2**(attempt-1) for each retried attempt.
    assert sleeps == [
        config.BACKOFF_BASE_SECONDS * (2 ** 0),
        config.BACKOFF_BASE_SECONDS * (2 ** 1),
    ]


def test_backoff_exhausted_raises_on_final_failure(requests_mock, monkeypatch, tmp_path):
    requests_mock.get(
        "https://example.test/always-503",
        [
            {"status_code": 503, "text": "unavailable"},
            {"status_code": 503, "text": "unavailable"},
            {"status_code": 503, "text": "unavailable"},
        ],
    )
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    session = http.build_session()
    try:
        http.fetch(session, "https://example.test/always-503", cache_dir=tmp_path)
        raised = False
    except requests.HTTPError:
        raised = True

    assert raised, "exhausting all retries on a persistent failure should raise"
    assert requests_mock.call_count == config.MAX_RETRIES


def test_etag_stored_then_sent_as_if_none_match_and_304_reuses_cached_body(
    requests_mock, tmp_path
):
    url = "https://example.test/page"
    requests_mock.get(
        url,
        [
            {
                "status_code": 200,
                "text": "original body",
                "headers": {"ETag": '"abc123"'},
            },
            {"status_code": 304, "text": ""},
        ],
    )

    session = http.build_session()

    first = http.fetch(session, url, cache_dir=tmp_path)
    assert first.status_code == 200
    assert first.from_cache is False
    assert first.text == "original body"
    # No cache entry existed yet, so no conditional header should be sent.
    assert "If-None-Match" not in requests_mock.request_history[0].headers

    second = http.fetch(session, url, cache_dir=tmp_path)
    assert second.status_code == 304
    assert second.from_cache is True
    # Short-circuits: the cached body is reused, never the (empty) 304 body.
    assert second.text == "original body"
    assert requests_mock.request_history[1].headers["If-None-Match"] == '"abc123"'
    assert requests_mock.call_count == 2


def test_robots_disallow_causes_clean_skip_not_raise(requests_mock):
    requests_mock.get(
        "https://example.test/robots.txt",
        text="User-agent: *\nDisallow: /private/\n",
    )

    allowed = http.check_robots_allowed(
        "https://example.test/private/page", user_agent="TestBot/1.0"
    )

    assert allowed is False


def test_robots_allows_when_permitted(requests_mock):
    requests_mock.get(
        "https://example.test/robots.txt",
        text="User-agent: *\nDisallow: /private/\n",
    )

    allowed = http.check_robots_allowed(
        "https://example.test/public/page", user_agent="TestBot/1.0"
    )

    assert allowed is True


def test_robots_404_is_treated_as_allow_all(requests_mock):
    requests_mock.get("https://example.test/robots.txt", status_code=404)

    allowed = http.check_robots_allowed(
        "https://example.test/anything", user_agent="TestBot/1.0"
    )

    assert allowed is True


def test_robots_server_error_causes_clean_skip_not_raise(requests_mock):
    requests_mock.get("https://example.test/robots.txt", status_code=500)

    allowed = http.check_robots_allowed(
        "https://example.test/anything", user_agent="TestBot/1.0"
    )

    assert allowed is False


def test_robots_network_error_causes_clean_skip_not_raise(requests_mock):
    requests_mock.get(
        "https://example.test/robots.txt", exc=requests.exceptions.ConnectionError
    )

    # Must not raise -- a fetch failure on robots.txt itself is a skip, not
    # a crash of the whole run.
    allowed = http.check_robots_allowed(
        "https://example.test/anything", user_agent="TestBot/1.0"
    )

    assert allowed is False


# --------------------------------------------------------------------------
# Documented-API exemption (CLAUDE.md's narrow fetch-discipline exception,
# resolved at the Phase 1 PM checkpoint): a central allowlist
# (`watcher.config.ROBOTS_EXEMPT_API_HOSTS`) lets a provider's own
# documented public API bypass the robots.txt gate entirely, without
# weakening that gate for every other host.
# --------------------------------------------------------------------------


def test_robots_exempt_host_is_allowed_without_fetching_robots_txt(requests_mock):
    # export.arxiv.org is the one host in ROBOTS_EXEMPT_API_HOSTS today.
    # Deliberately register NO robots.txt mock at all for this host -- if
    # check_robots_allowed() tried to fetch it, requests_mock would raise
    # a NoMockAddress error, so a passing test proves the exemption
    # short-circuits before any HTTP call is made.
    allowed = http.check_robots_allowed(
        "https://export.arxiv.org/api/query?search_query=cat:cs.AI",
        user_agent="TestBot/1.0",
    )

    assert allowed is True
    assert requests_mock.request_history == []


def test_robots_exemption_does_not_apply_to_non_allowlisted_host_with_same_disallow_body(
    requests_mock,
):
    # The exact disallow body real export.arxiv.org/robots.txt serves,
    # re-served here for a DIFFERENT, non-allowlisted host -- proves the
    # exemption is host-specific (an allowlist keyed on netloc), not a
    # blanket "any host serving this body" special case.
    requests_mock.get(
        "https://not-an-exempt-api-host.test/robots.txt",
        text="User-agent: * \nDisallow: /\n",
    )

    allowed = http.check_robots_allowed(
        "https://not-an-exempt-api-host.test/api/query",
        user_agent="TestBot/1.0",
    )

    assert allowed is False


def test_robots_exempt_hosts_allowlist_contains_exactly_arxiv_today():
    # Pins the narrow-exception scope CLAUDE.md documents ("today exactly
    # one") -- any future addition must be an explicit, logged decision,
    # not a silent expansion this test would then need to notice.
    assert config.ROBOTS_EXEMPT_API_HOSTS == frozenset({"export.arxiv.org"})
