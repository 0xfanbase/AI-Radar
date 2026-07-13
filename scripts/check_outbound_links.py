#!/usr/bin/env python3
"""CI gate: vet every outbound citation URL in the working-tree diff
against the frozen, human-curated ``data/trusted_domains.json`` allowlist.

Modeled on ``scripts/check_path_allowlist.py``'s own CLI/diff-reading
conventions (``git diff --name-only --no-renames HEAD``, print every
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
   ``roadmap[]``).
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
import subprocess
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
    """True if `path` is a real per-record content file this check reads
    citations from -- a company profile or a card, never either
    directory's own generated `index.json` manifest."""
    normalized = path.replace("\\", "/")
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
    if path.startswith(CARDS_PREFIX):
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


def classify_url(url: str, trusted: dict[str, Any]) -> UrlCheckResult:
    """Static (no-network) vetting of one URL against every rule in the
    module docstring's step 3. Never raises -- a URL that fails to parse
    at all (`urlsplit` itself never raises for a plain string, but an
    empty/garbage value can yield an empty hostname) is rejected with a
    clear reason rather than propagating an exception up to the CI gate's
    own top-level error handling.
    """
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
# get_changed_files -- identical mechanism to
# scripts/check_path_allowlist.py's own (see that module's docstring for
# why --no-renames matters).
# --------------------------------------------------------------------------


def get_changed_files(ref: str = "HEAD", repo_root: Path = REPO_ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# Orchestration
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
    for path in citation_files:
        for url in extract_citation_urls(path, repo_root=repo_root):
            url_to_files.setdefault(url, []).append(path)
            if url not in seen:
                seen.add(url)
                urls.append(url)

    violations: list[str] = []
    for url in urls:
        result = check_citation_url(session, url, trusted)
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
