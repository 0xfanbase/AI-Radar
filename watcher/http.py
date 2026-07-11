"""Shared HTTP fetch layer used by every Phase 1 fetcher (HN, arXiv, labs).

Centralizes the fetch-discipline rules from CLAUDE.md's "Sources &
selection algorithm" section in one place: a descriptive User-Agent, a
bounded timeout on every request, exponential-backoff retries on
transient failures, an ETag/Last-Modified response cache under
``data/.cache/`` (gitignored), and a ``robots.txt`` gate that skips a
source outright rather than ever circumventing a disallow.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from watcher.config import (
    BACKOFF_BASE_SECONDS,
    CACHE_DIR,
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_STATUS_FORCELIST,
    ROBOTS_EXEMPT_API_HOSTS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


def build_session(user_agent: str = USER_AGENT) -> requests.Session:
    """Build the one shared ``requests.Session`` every fetcher should use.

    Mounts a urllib3 ``Retry``-backed ``HTTPAdapter`` for genuine
    connection-level resilience (DNS hiccups, dropped connections, read
    timeouts) via its ``total`` budget. Its own ``status_forcelist`` is
    deliberately left empty: HTTP-status-driven retries (429/5xx) are
    instead orchestrated explicitly by :func:`fetch`, not by this adapter.

    Why the split: ``requests-mock`` (used by this project's deterministic,
    non-live test suite) replaces ``Session.send``/``Session.get_adapter``
    wholesale, so a Retry object embedded in a mounted adapter's
    ``max_retries`` never actually runs against a mocked response --
    ``requests-mock`` intercepts one layer above it. Doing status-based
    retries as an explicit loop in :func:`fetch` keeps the behavior both
    real (still uses urllib3's own backoff formula) and deterministically
    testable. Logged in IMPROVEMENT_BACKLOG.md.
    """
    session = requests.Session()
    session.headers["User-Agent"] = user_agent

    retry = Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        redirect=0,
        status=0,
        status_forcelist=(),
        backoff_factor=BACKOFF_BASE_SECONDS,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# --------------------------------------------------------------------------
# ETag cache: data/.cache/<sha256(url)>.json = {etag, last_modified, body,
# fetched_at}
# --------------------------------------------------------------------------


def _cache_path(url: str, cache_dir: Path = CACHE_DIR) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def _load_cache_entry(url: str, cache_dir: Path = CACHE_DIR) -> dict | None:
    path = _cache_path(url, cache_dir)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # A corrupt/partial cache entry is treated as a cache miss, never a
        # crash -- the next fetch simply re-fetches and overwrites it.
        return None


def _store_cache_entry(
    url: str,
    *,
    etag: str | None,
    last_modified: str | None,
    body: str,
    fetched_at: str,
    cache_dir: Path = CACHE_DIR,
) -> None:
    path = _cache_path(url, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "etag": etag,
        "last_modified": last_modified,
        "body": body,
        "fetched_at": fetched_at,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(entry, f)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# fetch()
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchResult:
    """The outcome of one :func:`fetch` call."""

    url: str
    status_code: int
    text: str
    from_cache: bool
    headers: dict[str, str]


def fetch(
    session: requests.Session,
    url: str,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    max_retries: int = MAX_RETRIES,
    backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
    cache_dir: Path = CACHE_DIR,
) -> FetchResult:
    """GET ``url`` through ``session``, with retries, backoff, and ETag reuse.

    - Always passes ``timeout`` explicitly (never an unbounded request).
    - Sends ``If-None-Match``/``If-Modified-Since`` when a cache entry
      exists for this URL. A ``304`` response short-circuits: the cached
      body is returned as-is and nothing is re-parsed or re-stored.
    - Retries up to ``max_retries`` total GET attempts (matching the
      approved plan's "3 attempts" fetch-discipline rule) when the
      response status is in ``RETRY_STATUS_FORCELIST`` (429/5xx), sleeping
      ``backoff_base_seconds * 2 ** (attempt - 1)`` between attempts --
      urllib3's own exponential-backoff formula, computed explicitly here
      rather than via an adapter's embedded Retry object (see
      :func:`build_session`'s docstring for why).
    - On a final non-retryable error status, raises via
      ``response.raise_for_status()`` -- callers (each source fetcher)
      decide whether to skip that source for the run.
    - On success (2xx), (re)writes the cache entry for this URL.
    """
    cached = _load_cache_entry(url, cache_dir)
    headers: dict[str, str] = {}
    if cached:
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]

    response = None
    for attempt in range(1, max_retries + 1):
        response = session.get(url, headers=headers, timeout=timeout)
        if response.status_code not in RETRY_STATUS_FORCELIST:
            break
        if attempt < max_retries:
            sleep_for = backoff_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Retryable status %s from %s (attempt %d/%d); "
                "sleeping %.2fs before retry.",
                response.status_code, url, attempt, max_retries, sleep_for,
            )
            time.sleep(sleep_for)

    assert response is not None  # max_retries >= 1 guarantees at least one GET

    if response.status_code == 304 and cached is not None:
        return FetchResult(
            url=url,
            status_code=304,
            text=cached["body"],
            from_cache=True,
            headers=dict(response.headers),
        )

    response.raise_for_status()

    etag = response.headers.get("ETag")
    last_modified = response.headers.get("Last-Modified")
    _store_cache_entry(
        url,
        etag=etag,
        last_modified=last_modified,
        body=response.text,
        fetched_at=_utcnow_iso(),
        cache_dir=cache_dir,
    )

    return FetchResult(
        url=url,
        status_code=response.status_code,
        text=response.text,
        from_cache=False,
        headers=dict(response.headers),
    )


# --------------------------------------------------------------------------
# robots.txt gate
# --------------------------------------------------------------------------


def check_robots_allowed(url: str, user_agent: str = USER_AGENT) -> bool:
    """Return True iff ``robots.txt`` permits ``user_agent`` to fetch ``url``.

    Rules (never circumvented):
    - A ``404`` on ``robots.txt`` itself is treated as allow-all (no
      published policy = no restriction), the common convention.
    - Any other failure -- a non-2xx/404 status, a network error, or an
      unparseable body -- is treated as "skip this source for the run":
      returns False and logs why, rather than guessing allow-all.
    - An explicit disallow from a parseable ``robots.txt`` returns False.

    Documented-API exemption (CLAUDE.md's fetch-discipline exception,
    deliberately narrow): if ``url``'s host is in
    ``watcher.config.ROBOTS_EXEMPT_API_HOSTS``, this short-circuits to
    ``True`` without ever fetching that host's ``robots.txt`` at all --
    the host's own published API terms of use are the governing contract
    for these requests, not a crawl directive aimed at page-indexing
    crawlers. Every other host (including any HTML page on the same
    domain the exemption doesn't name) is unaffected and stays fully
    gated by the logic below.
    """
    parsed = urlsplit(url)

    if parsed.netloc in ROBOTS_EXEMPT_API_HOSTS:
        logger.info(
            "robots.txt check skipped for %s -- documented-API exemption "
            "per CLAUDE.md (host %r is in ROBOTS_EXEMPT_API_HOSTS).",
            url, parsed.netloc,
        )
        return True

    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))

    try:
        response = requests.get(
            robots_url,
            headers={"User-Agent": user_agent},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning(
            "robots.txt fetch failed for %s (%s) -- skipping source for this run.",
            robots_url, exc,
        )
        return False

    if response.status_code == 404:
        return True

    if not response.ok:
        logger.warning(
            "robots.txt returned HTTP %s for %s -- skipping source for this run.",
            response.status_code, robots_url,
        )
        return False

    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.parse(response.text.splitlines())
    except Exception as exc:  # pragma: no cover - defensive, malformed body
        logger.warning(
            "robots.txt unparseable for %s (%s) -- skipping source for this run.",
            robots_url, exc,
        )
        return False

    allowed = parser.can_fetch(user_agent, url)
    if not allowed:
        logger.warning(
            "robots.txt disallows %s for UA %r -- skipping source for this run.",
            url, user_agent,
        )
    return allowed
