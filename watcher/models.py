"""Shared in-memory representation for every fetcher's raw output.

Every source (HN, arXiv, each lab) produces a stream of :class:`Item`
before clustering/ranking ever runs. Keeping this one shape shared here
means clustering.py and ranking.py (later commits) never need to know
which fetcher an item came from beyond its ``source_type``/``source_name``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# --------------------------------------------------------------------------
# Item
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One raw candidate story, before clustering/ranking.

    ``source_type`` matches the ``lab|arxiv|hn`` enum used throughout the
    schemas (queue.schema.json, card.schema.json's citations, etc.).
    ``extra`` holds source-specific fields that don't generalize (e.g. an
    arXiv paper's abstract, a lab post's author) without forcing every
    fetcher to share a wider, mostly-null schema.
    """

    source_type: str
    source_name: str
    title: str
    url: str
    published_at: str  # ISO 8601 timestamp string
    points: int | None = None
    num_comments: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# normalize_url -- exact-match half of clustering's "exact-URL-normalization
# match, else Jaccard >=0.35 over title tokens" rule.
# --------------------------------------------------------------------------

# Tracking/analytics query params stripped so otherwise-identical URLs
# shared with different campaign tags still normalize to the same key.
# Spec-silent (the plan only says "exact-URL-normalization match"); this is
# the simplest reasonable stripped set, logged in IMPROVEMENT_BACKLOG.md.
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset(
    {"ref", "ref_src", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "spm"}
)


def normalize_url(url: str) -> str:
    """Normalize a URL for exact-match clustering / ledger member keys.

    Lowercases scheme and host, drops a leading "www.", strips default
    ports, drops a trailing "/" from the path (root path stays "/"),
    removes known tracking query params and sorts what remains, and drops
    any fragment. Two URLs that differ only in these respects normalize to
    the same string.
    """
    parsed = urlsplit(url.strip())
    scheme = (parsed.scheme or "https").lower()

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[: -len(":80")]
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[: -len(":443")]

    path = parsed.path.rstrip("/") or "/"

    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
        and not key.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    query = urlencode(sorted(kept_params))

    return urlunsplit((scheme, netloc, path, query, ""))


# --------------------------------------------------------------------------
# tokenize_title -- feeds clustering's Jaccard-similarity fallback.
# --------------------------------------------------------------------------

# Minimal English stopword list -- enough to keep title tokens meaningful
# for Jaccard comparison without pulling in a full NLP dependency. Spec-
# silent exact list; logged in IMPROVEMENT_BACKLOG.md.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "with",
        "is", "are", "was", "were", "be", "been", "being", "at", "as", "by",
        "its", "it", "this", "that", "these", "those", "from", "into",
        "about", "after", "before", "over", "under", "up", "down", "out",
        "than", "then", "so", "but", "not", "no", "how", "why", "what",
        "when", "who", "which", "will", "can", "could", "would", "should",
        "new", "now", "just", "via", "vs", "your", "you", "we", "our",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize_title(title: str) -> frozenset[str]:
    """Lowercase, strip punctuation, and drop stopwords from a title.

    Returns a ``frozenset`` of remaining tokens (length > 1) so callers can
    compute Jaccard similarity via simple set operations.
    """
    tokens = _WORD_RE.findall(title.lower())
    return frozenset(tok for tok in tokens if tok not in _STOPWORDS and len(tok) > 1)
