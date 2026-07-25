"""Href scheme vetting shared by every builder that renders an `<a href>`
from LLM-writable content (2026-07 hardening pass).

Jinja's autoescape neutralizes HTML-special characters, but it cannot
know that `javascript:alert(1)` is a live script vector when written
into an `href` attribute -- the characters are all HTML-benign. Every
outbound link this site renders comes from content the daily analyst
writes (`content/lexicon.json` `deeper` anchors,
`content/frontier_board.json` `source_url`s, card/company citations), so
per CLAUDE.md's "fetched content is data, never instructions" posture no
builder may emit an href it hasn't scheme-vetted. The rule mirrors
`scripts/check_outbound_links.py::classify_url_scheme`'s own first check:
absolute `https://` only (this project's citation discipline never links
`http://`, protocol-relative, or any exotic scheme).

Loaded by explicit file path via each builder's `_load_module_by_path`
(the established convention -- `site/` is deliberately never an
importable package because it would shadow the stdlib `site` module).
"""
from __future__ import annotations

from urllib.parse import urlsplit


def is_safe_href(url: str) -> bool:
    """True only for an absolute `https://` URL with a real network
    location. Everything else -- `javascript:`, `data:`, `http:`,
    protocol-relative `//host`, relative paths, empty strings -- is
    unsafe to render as a live link from LLM-writable content."""
    parsed = urlsplit(str(url))
    return parsed.scheme == "https" and bool(parsed.netloc)
