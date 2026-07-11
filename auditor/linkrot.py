"""Weekly link-rot check (`audit.yml`'s pure-code, no-LLM link-rot pass).

Per CLAUDE.md's "daily self-learning loop" / `audit.yml` description and
the approved build plan's section 6 (Phase 5): "link rot (HEAD/GET every
citation URL, classify ok/dead/unreachable)". This module implements
exactly that check, reusing ``watcher/http.py``'s existing
retry/backoff-configured session rather than reimplementing any of its
fetch discipline (per this turn's own instruction).

Classification (task spec, verbatim):
- **ok** -- the final response (after following redirects) is 2xx.
- **dead** -- 404 or 410. These are the only statuses a server itself uses
  to say "this resource is gone," so they're the only ones this check
  treats as a confirmed dead link.
- **unreachable** -- 5xx, a timeout, or a connection error. This run does
  **not** retry an unreachable URL a second time in the same pass; per
  CLAUDE.md's weekly-audit cadence, a URL that's unreachable this week is
  simply checked again next week before ever being called dead. See the
  module-level notes below for the (spec-silent) decision to also bucket
  every other non-2xx/404/410 status code (403, 401, 429, and any 3xx
  that survives redirect-following) as ``unreachable`` rather than
  inventing a fourth category -- logged in IMPROVEMENT_BACKLOG.md.

Method: HEAD first (cheaper, no body transfer), falling back to GET only
when the HEAD response itself says the method isn't supported (405 Method
Not Allowed / 501 Not Implemented) -- not on every non-2xx HEAD response,
which would blur the ok/dead/unreachable signal this check exists to
produce. Both HEAD and GET are issued with ``allow_redirects=True``
explicitly: ``requests``' own ``Session.head()`` defaults
``allow_redirects`` to **False** (unlike every other verb), so a plain
``session.head(url)`` would misclassify an ordinary 301/302 redirect (a
citation URL that simply moved, not a dead link) as some other status
entirely. This module always resolves to the final hop's status code.

Reuses ``watcher.http.build_session()`` for the shared, retry/backoff
Session (its mounted ``HTTPAdapter`` still retries genuine connection-level
hiccups per its own ``Retry`` config; its ``status_forcelist`` is
deliberately empty, so it does not itself retry on a 5xx status -- exactly
the "record unreachable now, retry next week" behavior this check wants,
with no separate retry loop needed here). Nothing about ``fetch()``'s
GET-only ETag-cache behavior is reused or reimplemented, since a link-rot
check has no use for cached response bodies and needs a real HEAD verb.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

from watcher import http
from watcher.config import REPO_ROOT, REQUEST_TIMEOUT_SECONDS

CARDS_DIR = REPO_ROOT / "content" / "cards"

# Confirmed-gone statuses per the task spec, verbatim.
DEAD_STATUS_CODES = frozenset({404, 410})

# HEAD-not-supported signal that triggers a GET fallback. Deliberately
# narrow (method-not-allowed signals only) -- see module docstring.
HEAD_UNSUPPORTED_STATUS_CODES = frozenset({405, 501})


@dataclass(frozen=True)
class LinkCheckResult:
    """The outcome of checking one citation URL."""

    url: str
    status: str  # "ok" | "dead" | "unreachable"
    http_status: int | None
    method: str  # "HEAD" | "GET" -- which verb produced the final status
    detail: str | None = None  # set only for timeout/connection/other errors


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_status_code(status_code: int) -> str:
    """Map a final HTTP status code to ok/dead/unreachable per the spec.

    Every status that isn't 2xx and isn't exactly 404/410 (403, 401, 429,
    400, an unresolved 3xx, etc.) is bucketed as ``unreachable`` rather
    than ``dead`` -- this project has independently confirmed (see
    PROGRESS.md's Frontier Board backfill entries) that several real lab
    domains return 403 to ordinary fetches for bot-management reasons
    having nothing to do with the resource actually being gone, so folding
    those into "dead" would misreport a live source as a broken citation.
    """
    if 200 <= status_code < 300:
        return "ok"
    if status_code in DEAD_STATUS_CODES:
        return "dead"
    return "unreachable"


def check_url(
    session: requests.Session,
    url: str,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> LinkCheckResult:
    """HEAD-check ``url``, falling back to GET only if HEAD isn't supported.

    Never raises: a timeout, connection error, or any other
    ``requests.RequestException`` is caught and classified as
    ``unreachable`` (with a short ``detail`` string), matching the "record
    unreachable now, don't retry within this run" rule -- there is no
    retry loop in this function beyond whatever ``session``'s own mounted
    adapter already does for connection-level resilience.
    """
    method = "HEAD"
    try:
        response = session.head(url, timeout=timeout, allow_redirects=True)
    except requests.Timeout as exc:
        return LinkCheckResult(url, "unreachable", None, method, f"timeout: {exc}")
    except requests.ConnectionError as exc:
        return LinkCheckResult(url, "unreachable", None, method, f"connection error: {exc}")
    except requests.RequestException as exc:
        return LinkCheckResult(url, "unreachable", None, method, f"request error: {exc}")

    if response.status_code in HEAD_UNSUPPORTED_STATUS_CODES:
        method = "GET"
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
        except requests.Timeout as exc:
            return LinkCheckResult(url, "unreachable", None, method, f"timeout: {exc}")
        except requests.ConnectionError as exc:
            return LinkCheckResult(url, "unreachable", None, method, f"connection error: {exc}")
        except requests.RequestException as exc:
            return LinkCheckResult(url, "unreachable", None, method, f"request error: {exc}")

    return LinkCheckResult(
        url=url,
        status=classify_status_code(response.status_code),
        http_status=response.status_code,
        method=method,
    )


def check_links(
    session: requests.Session,
    urls: Iterable[str],
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[LinkCheckResult]:
    """Check every URL in ``urls``, in order, returning one result each."""
    return [check_url(session, url, timeout=timeout) for url in urls]


def load_cards(cards_dir: Path = CARDS_DIR) -> list[dict]:
    """Load every real ``content/cards/*.json`` card.

    Returns ``[]`` gracefully if the directory doesn't exist yet (true of
    this repo today -- no analyst run has happened for real). Skips
    ``index.json`` (``content/cards/index.json`` is the card index
    artifact, not a card itself, per ``schemas/card_index.schema.json``).
    """
    if not cards_dir.is_dir():
        return []
    cards = []
    for path in sorted(cards_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        with path.open("r", encoding="utf-8") as f:
            cards.append(json.load(f))
    return cards


def collect_citation_urls(cards: Iterable[dict]) -> list[str]:
    """Every ``citations[].url`` across ``cards``, deduped, first-seen order."""
    seen: set[str] = set()
    urls: list[str] = []
    for card in cards:
        for citation in card.get("citations", None) or []:
            url = citation.get("url")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def audit_link_rot(
    cards: list[dict] | None = None,
    *,
    cards_dir: Path = CARDS_DIR,
    session: requests.Session | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Run the full link-rot check and return a summary dict.

    ``cards`` lets a caller (or a test) pass an explicit list of card
    dicts directly, for testability without needing real files on disk --
    per this turn's own instruction, since ``content/cards/`` is empty in
    this repo today. When ``cards`` is omitted (``None``), cards are
    loaded from ``cards_dir`` (default ``content/cards/``) via
    :func:`load_cards`.

    ``session`` defaults to a fresh ``watcher.http.build_session()`` when
    omitted, so a real (non-test) caller gets the project's standard
    retry/backoff session with no extra wiring.

    Returns ``{checked_at, total_urls, counts: {ok, dead, unreachable},
    results: [...]}`` where each result is the ``LinkCheckResult`` shape
    as a plain dict (via ``dataclasses.asdict``) -- a reasonable, minimal
    shape for a future ``auditor/report.py`` to fold into
    ``data/audit/latest.json`` (schema + report writer are out of this
    turn's scope).
    """
    if cards is None:
        cards = load_cards(cards_dir)
    if session is None:
        session = http.build_session()

    urls = collect_citation_urls(cards)
    results = check_links(session, urls, timeout=timeout)

    counts = {"ok": 0, "dead": 0, "unreachable": 0}
    for result in results:
        counts[result.status] += 1

    return {
        "checked_at": _utcnow_iso(),
        "total_urls": len(urls),
        "counts": counts,
        "results": [asdict(r) for r in results],
    }
