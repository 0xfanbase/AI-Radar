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

**Phase 8 addition -- the post-publication hijack check
(:func:`audit_hijacked_links`).** ``scripts/check_outbound_links.py`` (the
commit-time CI gate) vets a citation's redirect target against
``data/trusted_domains.json`` the moment a card/company profile is
written, but a citation that was trusted at commit time can still be
hijacked *after* the fact -- a domain lapses and gets squatted, a lab page
starts redirecting somewhere new, etc. This function re-runs that exact
same redirect-resolution + allowlist check (:func:`scripts.
check_outbound_links.resolve_final_url` / ``classify_url``, imported and
reused verbatim rather than re-implemented a second time -- matching
``auditor.trend``'s own precedent of importing ``scripts.reconcile_run
.rolling_pass_rate`` rather than duplicating it) against every citation
URL already published in ``content/cards/*.json``, on `audit.yml`'s
weekly cadence, and emits one new finding type
(:class:`HijackCheckResult`, ``status`` one of ``trusted``/``hijacked``/
``unreachable``) via the exact same ``{checked_at, total_urls, counts,
results}`` shape :func:`audit_link_rot` already established -- "consistent
with this file's existing finding-emission pattern," per this turn's own
instruction, not a new shape invented from scratch.

**Phase 9 addition -- the same hijack re-check, over company-profile
citations (:func:`audit_company_hijacked_links`).** Phase 8's
:func:`audit_hijacked_links` only ever reads `content/cards/*.json`
citations -- `schemas/company.schema.json`'s own nested `profile.*
.citations[]` shape (the exact `citedText` shape
`scripts.check_outbound_links.extract_citation_urls_from_company` already
knows how to flatten, reused verbatim here) is a distinct universe of
already-published citations this repo's commit-time gate
(`scripts/check_outbound_links.py`) already vets but the weekly audit
never re-checked. Rather than widening :func:`audit_hijacked_links`'s own
committed `cards=` signature and `{checked_at, total_urls, counts,
results}` shape (both already locked down by Phase 8's own tests, per
this turn's own "never weaken an existing test" rule), this is a sibling
function with its own per-company result shape (`company_id` attached to
each result, since a citation's *owning company* -- not just its own
URL/status -- is exactly what `auditor/corrections_feed.py` needs to turn
a `"hijacked"` finding into a `target_type: "company"` pending-correction
candidate). Reuses :func:`check_hijack` directly (the one real network-
touching primitive both functions share), never a second implementation
of the redirect-resolution/allowlist logic.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

from scripts.check_outbound_links import (
    TRUSTED_DOMAINS_PATH,
    classify_url,
    extract_citation_urls_from_company,
    load_trusted_domains,
    resolve_final_url,
)
from scripts.plan_run import COMPANIES_DIR, load_company_registry
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


# --------------------------------------------------------------------------
# Phase 8: post-publication hijack check -- see module docstring's own
# "Phase 8 addition" section for the full rationale.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HijackCheckResult:
    """The outcome of re-resolving one already-published citation URL's
    redirect chain and re-checking the final URL against
    ``data/trusted_domains.json``."""

    url: str
    status: str  # "trusted" | "hijacked" | "unreachable"
    final_url: str | None
    detail: str | None = None


def check_hijack(
    session: requests.Session,
    url: str,
    trusted: dict[str, Any],
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> HijackCheckResult:
    """Re-resolve ``url``'s current redirect chain
    (:func:`scripts.check_outbound_links.resolve_final_url`) and classify
    the final URL against ``trusted``
    (:func:`scripts.check_outbound_links.classify_url`) -- the exact same
    two functions the commit-time CI gate runs, reused verbatim here for
    the weekly, after-the-fact re-check.

    - ``"trusted"`` -- the redirect chain resolved and the final URL still
      clears the allowlist. The common case; no finding.
    - ``"hijacked"`` -- the redirect chain resolved, but the final URL now
      fails the allowlist check (a domain that was trusted at commit time
      now redirects somewhere it shouldn't -- a post-commit hijack).
    - ``"unreachable"`` -- the redirect chain itself couldn't be resolved
      (timeout, connection error, any other network failure). Unlike
      ``scripts/check_outbound_links.py``'s own commit-time gate (which
      fails closed -- treats an unresolvable URL as a hard violation,
      since it's blocking a brand-new citation from ever being published),
      this weekly, after-the-fact check downgrades an unresolvable URL to
      its own distinct bucket rather than conflating it with a confirmed
      ``"hijacked"`` finding: a transient network hiccup on an
      already-published, previously-vetted citation shouldn't itself read
      as evidence of a hijack. It's simply checked again next week, same
      spirit as :func:`classify_status_code`'s own "record unreachable
      now, don't retry within this run" rule.
    """
    final_url, error = resolve_final_url(session, url, timeout=timeout)
    if error is not None:
        return HijackCheckResult(url=url, status="unreachable", final_url=None, detail=error)

    result = classify_url(final_url, trusted)
    if result.ok:
        return HijackCheckResult(url=url, status="trusted", final_url=final_url)
    return HijackCheckResult(
        url=url, status="hijacked", final_url=final_url, detail=result.reason
    )


def check_hijacks(
    session: requests.Session,
    urls: Iterable[str],
    trusted: dict[str, Any],
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[HijackCheckResult]:
    """Check every URL in ``urls``, in order, returning one
    :class:`HijackCheckResult` each -- mirrors :func:`check_links`'s own
    shape for the ok/dead/unreachable check."""
    return [check_hijack(session, url, trusted, timeout=timeout) for url in urls]


def audit_hijacked_links(
    cards: list[dict] | None = None,
    *,
    cards_dir: Path = CARDS_DIR,
    trusted: dict[str, Any] | None = None,
    trusted_domains_path: Path = TRUSTED_DOMAINS_PATH,
    session: requests.Session | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Run the full post-publication hijack check and return a summary
    dict, same ``{checked_at, total_urls, counts, results}`` shape
    :func:`audit_link_rot` already established (see module docstring).

    ``cards``/``session`` follow :func:`audit_link_rot`'s own defaulting
    convention exactly (an explicit list for testability, else loaded/built
    fresh). ``trusted`` likewise lets a caller (or a test) pass an explicit
    allowlist dict directly; when omitted, it's loaded fresh from
    ``trusted_domains_path`` (default: the real, committed
    ``data/trusted_domains.json``) via :func:`scripts.check_outbound_links
    .load_trusted_domains`.
    """
    if cards is None:
        cards = load_cards(cards_dir)
    if trusted is None:
        trusted = load_trusted_domains(trusted_domains_path)
    if session is None:
        session = http.build_session()

    urls = collect_citation_urls(cards)
    results = check_hijacks(session, urls, trusted, timeout=timeout)

    counts = {"trusted": 0, "hijacked": 0, "unreachable": 0}
    for result in results:
        counts[result.status] += 1

    return {
        "checked_at": _utcnow_iso(),
        "total_urls": len(urls),
        "counts": counts,
        "results": [asdict(r) for r in results],
    }


# --------------------------------------------------------------------------
# Phase 9: post-publication hijack check over company-profile citations --
# see module docstring's own "Phase 9 addition" section for the full
# rationale (a sibling function, not a widened audit_hijacked_links, to
# keep that function's own Phase 8-locked signature/shape untouched).
# --------------------------------------------------------------------------


def collect_company_citation_urls(
    companies: Sequence[dict[str, Any]] = (),
) -> list[tuple[str, str]]:
    """Every `(company_id, url)` pair across `companies`' own
    `profile.*.citations[].url` fields (`extract_citation_urls_from_company`,
    reused verbatim from `scripts.check_outbound_links`), deduped
    *per company* but not across companies -- two different companies
    citing the identical URL are two separate, independent findings if
    that URL turns out hijacked (each has its own profile page/reader to
    correct), not one. Preserves first-seen order within each company.
    """
    pairs: list[tuple[str, str]] = []
    for company in companies:
        company_id = str(company.get("id") or "")
        seen: set[str] = set()
        for url in extract_citation_urls_from_company(company):
            if url in seen:
                continue
            seen.add(url)
            pairs.append((company_id, url))
    return pairs


def audit_company_hijacked_links(
    companies: list[dict[str, Any]] | None = None,
    *,
    companies_dir: Path = COMPANIES_DIR,
    trusted: dict[str, Any] | None = None,
    trusted_domains_path: Path = TRUSTED_DOMAINS_PATH,
    session: requests.Session | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    """Run :func:`audit_hijacked_links`'s exact same weekly re-check
    (:func:`check_hijack`, reused directly), but over every
    `content/companies/*.json` profile's own citations instead of
    `content/cards/*.json`'s.

    `companies`/`session` follow `audit_hijacked_links`'s own defaulting
    convention exactly (an explicit list for testability, else loaded/
    built fresh -- here via `scripts.plan_run.load_company_registry`,
    reused, not reimplemented). `trusted` likewise.

    Returns `{checked_at, total_urls, counts, results}` -- the same
    top-level shape `audit_hijacked_links` and `audit_link_rot` both
    already establish -- but each result additionally carries
    `company_id` (which `content/companies/<id>.json` profile the citation
    came from), which `audit_hijacked_links`'s own card-citation results
    have no equivalent field for (a card's `citations[]` isn't itself
    attributable to one single company the way a company profile's own
    citations self-evidently are).
    """
    if companies is None:
        companies = load_company_registry(companies_dir)
    if trusted is None:
        trusted = load_trusted_domains(trusted_domains_path)
    if session is None:
        session = http.build_session()

    pairs = collect_company_citation_urls(companies)

    counts = {"trusted": 0, "hijacked": 0, "unreachable": 0}
    results: list[dict[str, Any]] = []
    for company_id, url in pairs:
        result = check_hijack(session, url, trusted, timeout=timeout)
        counts[result.status] += 1
        entry = asdict(result)
        entry["company_id"] = company_id
        results.append(entry)

    return {
        "checked_at": _utcnow_iso(),
        "total_urls": len(pairs),
        "counts": counts,
        "results": results,
    }
