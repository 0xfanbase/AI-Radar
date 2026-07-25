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
import re
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


# Only the ETag/body entries this module itself writes -- 64 lowercase hex
# chars (sha256) + ".json". Other state files that share data/.cache/
# (e.g. the DeepSeek fetcher's sitemap-seen list) are never touched by
# pruning.
_CACHE_ENTRY_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")

# How long an unused ETag cache entry survives before pruning. Two weeks
# comfortably covers every polling cadence in this project (once/twice
# daily) while keeping data/.cache/ from accumulating one orphaned entry
# per never-again-requested URL forever.
CACHE_MAX_AGE_DAYS = 14


def prune_cache(
    cache_dir: Path = CACHE_DIR,
    *,
    max_age_days: int = CACHE_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> int:
    """Delete ETag cache entries not fetched within ``max_age_days``.

    Nothing previously evicted anything from ``data/.cache/`` -- and
    because several cached URLs embed run timestamps (see
    ``watcher/sources/hn.py``), the directory would otherwise grow by a
    handful of never-hit-again entries every single run, forever. An
    entry with a missing/unparseable ``fetched_at`` is deleted too (the
    loader already treats such an entry as a cache miss, so it serves no
    purpose). Returns the number of files removed; never raises for an
    absent cache directory or a file deleted underneath it.
    """
    now = now or datetime.now(timezone.utc)
    max_age_seconds = max_age_days * 24 * 3600
    removed = 0
    if not cache_dir.is_dir():
        return 0
    for path in cache_dir.iterdir():
        if not _CACHE_ENTRY_NAME_RE.match(path.name):
            continue
        entry = _load_cache_entry_path(path)
        stale = True
        if entry is not None:
            fetched_at = entry.get("fetched_at")
            if isinstance(fetched_at, str):
                try:
                    fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                except ValueError:
                    fetched = None
                if fetched is not None:
                    if fetched.tzinfo is None:
                        fetched = fetched.replace(tzinfo=timezone.utc)
                    stale = (now - fetched).total_seconds() > max_age_seconds
        if stale:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        logger.info("Pruned %d stale ETag cache entr%s from %s.",
                    removed, "y" if removed == 1 else "ies", cache_dir)
    return removed


def _load_cache_entry_path(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


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

    if response.status_code == 304:
        if cached is not None:
            return FetchResult(
                url=url,
                status_code=304,
                text=cached["body"],
                from_cache=True,
                headers=dict(response.headers),
            )
        # A 304 with no local cache entry is a server anomaly (this
        # request sent no validators to be conditional on). Previously
        # this fell through to the success path and CACHED THE 304's
        # empty body as if it were real content -- poisoning every later
        # fetch of this URL. Refetch once, unconditionally; a second 304
        # is a hard error for the caller's own skip-this-source handling.
        logger.warning(
            "Unexpected 304 for %s with no local cache entry -- "
            "refetching unconditionally.", url,
        )
        response = session.get(url, timeout=timeout)
        if response.status_code == 304:
            raise requests.HTTPError(
                f"server keeps answering 304 for {url} despite an "
                "unconditional request", response=response,
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


# Per-run memo of each host's robots.txt policy, keyed by
# (scheme, netloc): a parsed RobotFileParser for a real policy, True for
# "no policy published" (404 = allow-all), False for "couldn't confirm"
# (fetch failure / unparseable / error status = skip for the run).
# Previously robots.txt was refetched for EVERY candidate URL -- the
# DeepSeek fetcher alone re-downloads the same file once per new article
# -- and via a bare requests.get that bypassed the shared session's
# UA/retry configuration entirely. The watcher is a process-per-run CLI,
# so module lifetime == run lifetime; tests reset via
# clear_robots_cache() (autouse fixture in tests/conftest.py).
_ROBOTS_POLICY_CACHE: dict[tuple[str, str], "RobotFileParser | bool"] = {}


def clear_robots_cache() -> None:
    """Reset the per-run robots.txt memo (test isolation hook)."""
    _ROBOTS_POLICY_CACHE.clear()


def _fetch_robots_policy(
    scheme: str,
    netloc: str,
    user_agent: str,
    session: requests.Session | None,
) -> "RobotFileParser | bool":
    robots_url = urlunsplit((scheme, netloc, "/robots.txt", "", ""))
    getter = session.get if session is not None else requests.get
    try:
        response = getter(
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

    return parser


def check_robots_allowed(
    url: str,
    user_agent: str = USER_AGENT,
    session: requests.Session | None = None,
) -> bool:
    """Return True iff ``robots.txt`` permits ``user_agent`` to fetch ``url``.

    Rules (never circumvented):
    - A ``404`` on ``robots.txt`` itself is treated as allow-all (no
      published policy = no restriction), the common convention.
    - Any other failure -- a non-2xx/404 status, a network error, or an
      unparseable body -- is treated as "skip this source for the run":
      returns False and logs why, rather than guessing allow-all.
    - An explicit disallow from a parseable ``robots.txt`` returns False.

    Each host's policy is fetched at most once per run (memoized per
    ``(scheme, netloc)``; the allow/deny verdict is still evaluated per
    full URL against the memoized parser). Pass the shared ``session``
    (from :func:`build_session`) so the robots fetch itself carries the
    project's own UA/retry configuration instead of a bare default GET.

    Documented-API exemption (CLAUDE.md's fetch-discipline exception,
    deliberately narrow): if ``url``'s host is in
    ``watcher.config.ROBOTS_EXEMPT_API_HOSTS`` AND its path is under
    ``/api/``, this short-circuits to ``True`` without ever fetching that
    host's ``robots.txt`` at all -- the host's own published API terms of
    use are the governing contract for these requests, not a crawl
    directive aimed at page-indexing crawlers. The path scoping is
    load-bearing: CLAUDE.md promises the exemption "never applies to
    HTML/website fetching," and today's one exempt host
    (``export.arxiv.org``) serves its documented API under ``/api/``
    exactly -- a non-API URL on the same host stays fully robots-gated
    like every other URL.
    """
    parsed = urlsplit(url)

    if parsed.netloc in ROBOTS_EXEMPT_API_HOSTS and parsed.path.startswith("/api/"):
        logger.info(
            "robots.txt check skipped for %s -- documented-API exemption "
            "per CLAUDE.md (host %r is in ROBOTS_EXEMPT_API_HOSTS, path "
            "under /api/).",
            url, parsed.netloc,
        )
        return True

    key = (parsed.scheme, parsed.netloc)
    if key not in _ROBOTS_POLICY_CACHE:
        _ROBOTS_POLICY_CACHE[key] = _fetch_robots_policy(
            parsed.scheme, parsed.netloc, user_agent, session
        )

    policy = _ROBOTS_POLICY_CACHE[key]
    if policy is True:
        return True
    if policy is False:
        # Reason already logged when the failed fetch was memoized; keep
        # later same-host calls quiet-but-consistent.
        return False

    allowed = policy.can_fetch(user_agent, url)
    if not allowed:
        logger.warning(
            "robots.txt disallows %s for UA %r -- skipping source for this run.",
            url, user_agent,
        )
    return allowed
