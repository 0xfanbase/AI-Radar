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
