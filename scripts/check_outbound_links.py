#!/usr/bin/env python3
"""CI gate: vet every outbound citation URL in the working-tree diff
against the frozen, human-curated ``data/trusted_domains.json`` allowlist.

Modeled on ``scripts/check_path_allowlist.py``'s own CLI/diff-reading
conventions (the shared ``scripts/_git_changes.py`` helper: tracked diff
against ``HEAD`` plus untracked, non-ignored files; print every
violation, exit nonzero) -- this is a sibling CI gate, not a replacement
for it. Where ``check_path_allowlist.py`` protects *which files* an
automated run may touch, this script protects *what a card/company
profile may link out to*, per ``data/trusted_domains.json``'s own stated
job ("the outbound-link-vetting allowlist, a different concern with a
different owner" from ``schemas/company.schema.json``'s
``official_domains``/``official_repos`` PRIMARY-classification-only
fields).

What this script does, for the working-tree diff against ``HEAD``:

1. **Frozen-file guard, checked first and unconditionally.** If the diff
   touches ``data/trusted_domains.json`` *at all* -- any change, additive
   or not -- the whole check hard-fails immediately, before anything else
   runs. That file is human-curated and frozen (its own ``_meta.curation``
   field says so); the automated analyst/verifier pipeline (or any other
   automated committer) must never be able to widen its own link budget
   by editing the allowlist in the same commit that uses a new domain.
2. **Collect every citation URL** from every changed
   ``content/companies/<slug>.json`` and ``content/cards/<id>.json`` file
   (never the generated ``index.json`` manifests, which carry no
   citations of their own) -- both card ``citations[]`` and every nested
   ``profile.*.citations[]`` a company record can carry
   (``schemas/company.schema.json``'s ``citedText`` shape, reused across
   ``overview``/``what_theyve_done[]``/``strengths[]``/``current_focus``/
   ``roadmap[]``) -- plus, since the 2026-07 hardening pass, the two
   other LLM-writable content files that carry outbound hrefs:
   ``content/frontier_board.json`` (every row's ``source_url``, fully
   vetted like card citations -- the Board only ever cites PRIMARY /
   confirmed-card OUTLET sources, all inside this allowlist's own stated
   curation scope) and ``content/lexicon.json`` (every ``deeper`` field's
   inline ``<a href>``, vetted against the *scheme-level* static checks
   only -- see ``SCHEME_ONLY_DIFF_PATHS`` for why the hostname-allowlist
   membership check deliberately does not apply there).
3. **Static vetting** (:func:`classify_url`, no network): reject
   ``http://`` (and any non-``https`` scheme), an IP-literal host,
   userinfo embedded in the URL (``user:pass@host``), a punycode
   (``xn--``) hostname label, and a small named URL-shortener denylist
   (bit.ly, t.co, tinyurl.com, goo.gl) -- then require the (lowercased,
   ``www.``-insensitive) hostname to either exact-match one of
   ``data/trusted_domains.json``'s ``hostnames[]``, or match one of its
   ``path_scoped[]`` entries (``{hostname, path_prefix}``) with the URL's
   path actually starting with that prefix.
4. **Redirect-chain vetting** (:func:`resolve_final_url`): for a URL that
   passes step 3, follow its real redirect chain (HEAD, falling back to
   GET only on a 405/501 HEAD-not-supported response -- same fallback
   rule ``auditor/linkrot.py::check_url`` already uses) via the shared,
   retry/backoff-configured session from ``watcher.http.build_session()``
   (reused, not reimplemented), and re-apply the exact same
   :func:`classify_url` checks to the *final* resolved URL. This is what
   catches a post-approval hijack: a citation that was written against a
   trusted domain but whose target has since started redirecting
   somewhere untrusted. Run at commit time, on every commit touching
   these files -- not only as part of the weekly audit (see
   ``auditor/linkrot.py``'s own separate, complementary weekly
   re-resolution check for already-published citations).

A URL whose redirect chain cannot be resolved at all (timeout, connection
error, any other network failure) is treated as a violation, not silently
skipped -- this is a security gate, and this project's own established
convention for "can't confirm this is safe" is to fail closed
(``watcher/http.py::check_robots_allowed``'s "any other failure ...
skip this source" rule is the same instinct applied to a different
check). Logged in IMPROVEMENT_BACKLOG.md.

Exits nonzero, printing every violation, on any failure. Exits 0 if the
diff touches neither ``data/trusted_domains.json`` nor any citation-
carrying file, or if every citation URL found clears every check.
"""
from __future__ import annotations

import ipaddress
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

# Allow running as `python scripts/check_outbound_links.py` (no package
# install / no `-m` needed) -- same sys.path trick every other script in
# this repo uses (scripts/plan_run.py, scripts/reconcile_run.py, ...).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts._git_changes import get_changed_files  # noqa: E402, F401
from watcher import http  # noqa: E402
from watcher.config import REQUEST_TIMEOUT_SECONDS  # noqa: E402

TRUSTED_DOMAINS_PATH = REPO_ROOT / "data" / "trusted_domains.json"
TRUSTED_DOMAINS_DIFF_PATH = "data/trusted_domains.json"

# Small, named denylist of URL-shortener domains -- a shortener hides its
# real destination from this check's own static hostname match, so it is
# rejected outright regardless of what it currently redirects to. Kept
# short and explicit, same discipline CLAUDE.md's own reputable-outlet
# table is held to ("named explicitly rather than left to discretion").
URL_SHORTENER_DENYLIST = frozenset({"bit.ly", "t.co", "tinyurl.com", "goo.gl"})

# HEAD-not-supported signal that triggers a GET fallback -- identical
# convention to auditor/linkrot.py::check_url (reused rule, not reused
# code: this module's own failure handling needs -- fail-closed on error
# -- differ from that module's "record unreachable, don't raise").
HEAD_UNSUPPORTED_STATUS_CODES = frozenset({405, 501})

# The two content subtrees this check ever looks at for citation URLs.
# content/companies/index.json and content/cards/index.json are generated
# manifests with no citations[] of their own and are deliberately excluded
# below, not swept up by these prefixes alone.
COMPANIES_PREFIX = "content/companies/"
CARDS_PREFIX = "content/cards/"

# The two other LLM-writable content files that carry outbound hrefs
# (2026-07 hardening pass: a `javascript:` href in either previously
# rendered live on the built site, with no gate anywhere in its path).
LEXICON_DIFF_PATH = "content/lexicon.json"
FRONTIER_BOARD_DIFF_PATH = "content/frontier_board.json"

# Files whose URLs get the scheme-level static checks (https-only, no
# userinfo/IP-literal/punycode/shortener) but NOT the hostname-allowlist
# membership check or the network redirect-chain re-check. Today exactly
# one: content/lexicon.json. Its `deeper` citations legitimately point at
# hosts outside data/trusted_domains.json's own stated curation scope
# (which covers the outlet table + company official domains + board/
# company citation hosts -- 4 of the 30 seed lexicon entries cite e.g.
# github.com/bis.gov/cdn.openai.com today), so requiring allowlist
# membership here would hard-block the daily pipeline's routine
# lexicon.json touches (the auto-growth rule appends seen_in[] ids every
# run) until an owner curation pass -- while the injection-relevant
# defense (scheme shape) applies in full. Widening the frozen allowlist
# to cover lexicon hosts is an owner-checkpoint decision, not this
# gate's.
SCHEME_ONLY_DIFF_PATHS = frozenset({LEXICON_DIFF_PATH})

# Mirrors site/builders/lexicon.py::_ANCHOR_RE's href half: the one
# narrow, literal anchor shape a lexicon entry's `deeper` field carries.
# Kept as a literal twin (not an import) because scripts/ never imports
# from site/ -- site/ is deliberately not an importable package.
_DEEPER_ANCHOR_HREF_RE = re.compile(r'<a href="([^"]*)"')


@dataclass(frozen=True)
class UrlCheckResult:
    """The outcome of vetting one citation URL."""

    url: str
    ok: bool
    reason: str = ""
    final_url: str | None = None


# --------------------------------------------------------------------------
# Step 1: the frozen-file guard.
# --------------------------------------------------------------------------


def diff_touches_trusted_domains(changed_files: list[str]) -> bool:
    """True if `data/trusted_domains.json` appears anywhere in the diff --
    checked as an exact repo-relative path, matching every other exact-path
    check in this pipeline (`scripts/validate_changed_schemas.py`'s own
    `EXACT_PATH_SCHEMAS` table)."""
    normalized = {f.replace("\\", "/") for f in changed_files}
    return TRUSTED_DOMAINS_DIFF_PATH in normalized


# --------------------------------------------------------------------------
# Step 2: collect changed citation-carrying files + extract citation URLs.
# --------------------------------------------------------------------------


def is_citation_bearing_path(path: str) -> bool:
    """True if `path` is a content file this check reads outbound URLs
    from -- a company profile, a card, the frontier board, or the
    lexicon; never the card/company directories' own generated
    `index.json` manifests."""
    normalized = path.replace("\\", "/")
    if normalized in (LEXICON_DIFF_PATH, FRONTIER_BOARD_DIFF_PATH):
        return True
    if normalized.startswith(COMPANIES_PREFIX) and normalized.endswith(".json"):
        return normalized != COMPANIES_PREFIX + "index.json"
    if normalized.startswith(CARDS_PREFIX) and normalized.endswith(".json"):
        return normalized != CARDS_PREFIX + "index.json"
    return False


def changed_citation_files(changed_files: list[str]) -> list[str]:
    """Every changed path this check should read citations[] from,
    preserving original diff order."""
    return [f for f in changed_files if is_citation_bearing_path(f)]


def _collect_cited_text_urls(cited_text: dict[str, Any] | None, urls: list[str]) -> None:
    if not cited_text:
        return
    for citation in cited_text.get("citations", None) or []:
        url = citation.get("url")
        if url:
            urls.append(url)


def extract_citation_urls_from_card(card: dict[str, Any]) -> list[str]:
    """Every `citations[].url` in one loaded `content/cards/<id>.json`
    (`schemas/card.schema.json` shape)."""
    return [c["url"] for c in card.get("citations", None) or [] if c.get("url")]


def extract_citation_urls_from_company(company: dict[str, Any]) -> list[str]:
    """Every citation URL across one loaded `content/companies/<slug>.json`
    full profile (`schemas/company.schema.json`'s `profile.*` fields,
    every one of which is either a single `citedText` object or an array
    of them)."""
    urls: list[str] = []
    profile = company.get("profile", None) or {}
    _collect_cited_text_urls(profile.get("overview"), urls)
    for item in profile.get("what_theyve_done", None) or []:
        _collect_cited_text_urls(item, urls)
    for item in profile.get("strengths", None) or []:
        _collect_cited_text_urls(item, urls)
    _collect_cited_text_urls(profile.get("current_focus"), urls)
    for item in profile.get("roadmap", None) or []:
        _collect_cited_text_urls(item, urls)
    return urls


def extract_citation_urls_from_lexicon(entries: Any) -> list[str]:
    """Every inline `<a href>` target across every lexicon entry's
    `deeper` field (`schemas/lexicon.schema.json` shape: a top-level
    array of entries)."""
    urls: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        for href in _DEEPER_ANCHOR_HREF_RE.findall(str(entry.get("deeper", ""))):
            if href:
                urls.append(href)
    return urls


def extract_citation_urls_from_board(rows: Any) -> list[str]:
    """Every row's `source_url` in the loaded `content/frontier_board.json`
    (`schemas/frontier_board.schema.json` shape: a top-level array of
    rows)."""
    urls: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        url = row.get("source_url")
        if url:
            urls.append(str(url))
    return urls


def extract_citation_urls(path: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """Load `path` (a repo-relative path already confirmed by
    :func:`is_citation_bearing_path`) and return every citation URL it
    carries. Returns `[]` for a path that no longer exists on disk (a
    deletion in this diff, or the old half of a rename reported via
    `--no-renames` -- nothing to check for a file that's gone) or that
    fails to parse as JSON (a malformed file is a schema-validation gate's
    job to catch, not this one's -- this check simply has nothing to read
    in that case)."""
    file_path = repo_root / path
    if not file_path.is_file():
        return []
    try:
        with file_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError:
        return []
    normalized = path.replace("\\", "/")
    if normalized == LEXICON_DIFF_PATH:
        return extract_citation_urls_from_lexicon(data)
    if normalized == FRONTIER_BOARD_DIFF_PATH:
        return extract_citation_urls_from_board(data)
    if normalized.startswith(CARDS_PREFIX):
        return extract_citation_urls_from_card(data)
    return extract_citation_urls_from_company(data)


# --------------------------------------------------------------------------
# Step 3: static vetting (no network).
# --------------------------------------------------------------------------


def load_trusted_domains(path: Path = TRUSTED_DOMAINS_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _normalized_hostname(hostname: str) -> str:
    """Lowercase, `www.`-prefix-stripped hostname -- matches
    `site/builders/board.py::source_host`'s own normalization so a
    citation's exact-hostname check isn't defeated by a bare
    `www.` difference from how a domain is listed in
    `data/trusted_domains.json`'s `hostnames[]`."""
    host = hostname.lower()
    if host.startswith("www."):
        host = host[len("www.") :]
    return host


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _has_punycode_label(hostname: str) -> bool:
    return any(label.lower().startswith("xn--") for label in hostname.split("."))


def _hostname_trusted(hostname: str, path: str, trusted: dict[str, Any]) -> bool:
    normalized = _normalized_hostname(hostname)
    trusted_hostnames = {h.lower() for h in trusted.get("hostnames", [])}
    if normalized in trusted_hostnames:
        return True
    for entry in trusted.get("path_scoped", None) or []:
        entry_host = _normalized_hostname(str(entry.get("hostname", "")))
        prefix = str(entry.get("path_prefix", ""))
        if normalized == entry_host and path.startswith(prefix):
            return True
    return False


def classify_url_scheme(url: str) -> UrlCheckResult:
    """The scheme-level static checks alone -- everything in the module
    docstring's step 3 *except* hostname-allowlist membership: https-only
    scheme, no embedded userinfo, a parseable hostname that is neither an
    IP literal nor punycode, and not a denylisted URL shortener. This is
    the tier applied on its own to `SCHEME_ONLY_DIFF_PATHS` files (see
    that constant for why), and the first stage of :func:`classify_url`
    for everything else. Never raises."""
    parsed = urlsplit(url)

    if parsed.scheme != "https":
        return UrlCheckResult(url, False, f"scheme {parsed.scheme!r} is not https")

    if parsed.username is not None or parsed.password is not None:
        return UrlCheckResult(url, False, "URL embeds userinfo (user:pass@host)")

    hostname = parsed.hostname or ""
    if not hostname:
        return UrlCheckResult(url, False, "URL has no parseable hostname")

    if _is_ip_literal(hostname):
        return UrlCheckResult(url, False, f"host {hostname!r} is an IP literal")

    if _has_punycode_label(hostname):
        return UrlCheckResult(url, False, f"host {hostname!r} has a punycode (xn--) label")

    normalized = _normalized_hostname(hostname)
    if normalized in URL_SHORTENER_DENYLIST:
        return UrlCheckResult(url, False, f"host {hostname!r} is a denylisted URL shortener")

    return UrlCheckResult(url, True)


def classify_url(url: str, trusted: dict[str, Any]) -> UrlCheckResult:
    """Full static (no-network) vetting of one URL against every rule in
    the module docstring's step 3: the scheme-level checks of
    :func:`classify_url_scheme` plus hostname-allowlist membership.
    Never raises -- a URL that fails to parse at all (`urlsplit` itself
    never raises for a plain string, but an empty/garbage value can yield
    an empty hostname) is rejected with a clear reason rather than
    propagating an exception up to the CI gate's own top-level error
    handling.
    """
    scheme_result = classify_url_scheme(url)
    if not scheme_result.ok:
        return scheme_result

    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if not _hostname_trusted(hostname, parsed.path, trusted):
        return UrlCheckResult(url, False, f"host {hostname!r} is not in data/trusted_domains.json")

    return UrlCheckResult(url, True)


# --------------------------------------------------------------------------
# Step 4: redirect-chain vetting (network, via the shared session).
# --------------------------------------------------------------------------


def resolve_final_url(
    session: requests.Session, url: str, *, timeout: float = REQUEST_TIMEOUT_SECONDS
) -> tuple[str | None, str | None]:
    """Follow `url`'s real redirect chain and return `(final_url, None)`,
    or `(None, error_detail)` if the chain could not be resolved at all.

    HEAD first (cheaper), falling back to GET only when HEAD itself
    reports method-not-allowed/not-implemented (405/501) -- identical
    fallback rule to `auditor/linkrot.py::check_url`, reused as a
    convention (not as shared code, since that module's own error
    handling classifies failures as "unreachable, retry next week" while
    this one must fail closed as a hard CI violation instead). Both calls
    pass `allow_redirects=True` explicitly, since `requests`' own
    `Session.head()` defaults it to `False` unlike every other verb.
    """
    try:
        response = session.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code in HEAD_UNSUPPORTED_STATUS_CODES:
            response = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.Timeout as exc:
        return None, f"timeout: {exc}"
    except requests.ConnectionError as exc:
        return None, f"connection error: {exc}"
    except requests.RequestException as exc:
        return None, f"request error: {exc}"
    return response.url, None


def check_citation_url(
    session: requests.Session,
    url: str,
    trusted: dict[str, Any],
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> UrlCheckResult:
    """The full step-3 + step-4 vetting pipeline for one citation URL.

    Step 4 (the network redirect-chain check) only ever runs if step 3
    already passed -- a URL that's already rejected statically (e.g. a
    bare `http://` link, or an off-allowlist host) needs no network call
    to also fail, and this project's fetch discipline (CLAUDE.md's
    "Sources & selection algorithm") never issues a request that has no
    chance of mattering.
    """
    static_result = classify_url(url, trusted)
    if not static_result.ok:
        return static_result

    final_url, error = resolve_final_url(session, url, timeout=timeout)
    if error is not None:
        return UrlCheckResult(
            url, False, f"could not resolve redirect chain: {error}"
        )

    final_result = classify_url(final_url, trusted)
    if not final_result.ok:
        return UrlCheckResult(
            url,
            False,
            f"redirects to a URL that fails vetting ({final_result.reason}): {final_url}",
            final_url=final_url,
        )

    return UrlCheckResult(url, True, final_url=final_url)


# --------------------------------------------------------------------------
# Orchestration -- get_changed_files comes from the shared
# scripts/_git_changes.py helper (tracked diff + untracked files; see that
# module's docstring for why untracked files must be included pre-commit).
# --------------------------------------------------------------------------


def collect_violations(
    changed_files: list[str],
    *,
    repo_root: Path = REPO_ROOT,
    trusted: dict[str, Any] | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """Run the full check against `changed_files` and return every
    violation as a human-readable string (empty list = pass).

    The frozen-file guard (step 1) is checked first and, if it trips,
    short-circuits everything else -- `data/trusted_domains.json` being
    touched at all is itself the entire violation; there's no value in
    also vetting citation URLs from the same diff.
    """
    if diff_touches_trusted_domains(changed_files):
        return [
            "data/trusted_domains.json is frozen and human-only -- this diff "
            "must not modify it at all (see that file's own _meta.curation "
            "field). Revert this file and open a separate, explicitly "
            "human-reviewed change for any allowlist addition/removal."
        ]

    citation_files = changed_citation_files(changed_files)
    if not citation_files:
        return []

    trusted = trusted if trusted is not None else load_trusted_domains()
    session = session if session is not None else http.build_session()

    urls: list[str] = []
    seen: set[str] = set()
    url_to_files: dict[str, list[str]] = {}
    fully_vetted_urls: set[str] = set()
    for path in citation_files:
        normalized_path = path.replace("\\", "/")
        for url in extract_citation_urls(path, repo_root=repo_root):
            url_to_files.setdefault(url, []).append(path)
            if normalized_path not in SCHEME_ONLY_DIFF_PATHS:
                fully_vetted_urls.add(url)
            if url not in seen:
                seen.add(url)
                urls.append(url)

    violations: list[str] = []
    for url in urls:
        if url in fully_vetted_urls:
            # Full pipeline: static allowlist vetting + redirect-chain
            # re-check (cards, company profiles, the frontier board).
            result = check_citation_url(session, url, trusted)
        else:
            # Scheme-only tier (lexicon `deeper` hrefs -- see
            # SCHEME_ONLY_DIFF_PATHS): static scheme checks, no allowlist
            # membership, no network.
            result = classify_url_scheme(url)
        if not result.ok:
            files = ", ".join(url_to_files[url])
            violations.append(f"{url} (cited in {files}): {result.reason}")
    return violations


def main() -> int:
    changed_files = get_changed_files()
    violations = collect_violations(changed_files)
    if violations:
        print("Outbound link check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
