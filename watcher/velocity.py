"""What's Moving (watcher/velocity.py): the pure-code, no-AI 7-day
topic-velocity strip CLAUDE.md's daily loop describes as "compute
whats_moving.json from HN counts."

Given this run's fetched HN items (``watcher.sources.hn.fetch_hn_items``'s
output -- already AI-relevant per its own keyword/points/velocity gates)
and an explicit ``now``, buckets each item's mention into one calendar day
(0-6 days old, relative to ``now``) and one or more of the project's nine
closed-set topic tags (the same enum ``schemas/card.schema.json``'s
``topics[]`` and ``schemas/whats_moving.schema.json``'s ``topic`` use), via
a whole-word/phrase keyword classifier (see :data:`TOPIC_KEYWORDS`) -- the
same style of pattern ``watcher/sources/hn.py`` already uses for its own
AI-relevance keyword filter, just against a finer, closed set of topics
instead of one broad "is this AI-related" gate.

No live network calls and no ``now`` from ``datetime.now()`` inside this
module's own computation -- every function here takes ``now`` explicitly,
so a caller (``watcher/cli.py`` in production, ``tests/test_velocity.py``
in the suite) supplies it, matching this project's "no live Date/time calls
inside pure-code logic, no freezegun needed" testing convention.

The trend label (``accelerating``/``cooling``/``flat``, per
``schemas/whats_moving.schema.json``'s enum -- the spec's own prose calls
this "rising/falling/flat"; the schema's actual enum values are used
verbatim here since those are what gets validated and rendered) is
pre-computed here rather than left for the frontend to infer from color or
slope alone -- CLAUDE.md's accessibility rule that trend must always be
visible as text, never color/slope-only.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from watcher.config import REPO_ROOT
from watcher.schema_validate import validate

logger = logging.getLogger(__name__)

WHATS_MOVING_PATH = REPO_ROOT / "data" / "whats_moving.json"
WINDOW_DAYS = 7

__all__ = [
    "WHATS_MOVING_PATH",
    "WINDOW_DAYS",
    "TOPIC_KEYWORDS",
    "classify_topics",
    "compute_whats_moving",
    "load_whats_moving",
    "save_whats_moving",
]

# --------------------------------------------------------------------------
# Topic classification -- closed set, keyword/word-boundary matching.
# --------------------------------------------------------------------------
#
# Spec-silent (the approved plan names the nine topic tags via
# card.schema.json/whats_moving.schema.json's shared enum but never defines
# how a pure-code, no-AI pass buckets an HN story title into them). This is
# the simplest reasonable keyword classifier: same whole-word/phrase
# regex-matching approach watcher/sources/hn.py already uses for its own
# "is this AI-relevant at all" gate, just against nine finer buckets
# instead of one broad one. A single title may match zero, one, or several
# topics (mirroring how a real card's own topics[] is a non-empty set, not
# a single label) -- logged in IMPROVEMENT_BACKLOG.md. Dict order matches
# the schema enum's own order, so output topics always render in a stable,
# predictable sequence.
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "models": (
        "model", "models", "gpt", "llm", "large language model", "claude",
        "gemini", "llama", "chatbot", "multimodal", "release",
    ),
    "research": (
        "paper", "research", "arxiv", "benchmark", "study", "dataset",
        "researchers",
    ),
    "chips/compute": (
        "gpu", "gpus", "chip", "chips", "tpu", "nvidia", "compute",
        "data center", "silicon", "wafer", "semiconductor",
    ),
    "policy": (
        "regulation", "regulate", "policy", "export control", "export controls",
        "legislation", "government", "ban", "sanction", "sanctions", "lawsuit",
    ),
    "products": (
        "app", "product", "launch", "feature", "api", "assistant", "copilot",
        "plugin", "subscription",
    ),
    "safety": (
        "safety", "alignment", "red team", "red-team", "jailbreak",
        "guardrail", "existential risk",
    ),
    "open-source": (
        "open source", "open-source", "open weights", "open-weights",
        "github", "weights released",
    ),
    "China": (
        "china", "chinese", "beijing", "deepseek", "alibaba", "qwen",
        "baidu", "tencent", "moonshot", "zhipu", "bytedance",
    ),
    "funding": (
        "funding", "raises", "raised", "valuation", "series a", "series b",
        "series c", "investment", "ipo", "acquisition", "acquire", "acquires",
    ),
}

_TOPIC_PATTERNS: dict[str, tuple[re.Pattern, ...]] = {
    topic: tuple(
        re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
        for keyword in keywords
    )
    for topic, keywords in TOPIC_KEYWORDS.items()
}


def classify_topics(title: str) -> list[str]:
    """Every topic (schema enum order) whose keyword set matches ``title``.

    Whole-word/phrase matching (not naive substring), same rationale as
    ``watcher/sources/hn.py``'s own ``_matches_keywords``: avoids
    false-positiving the short keyword ``"ai"``-style tokens against
    ordinary words that merely contain the same letters. Returns ``[]``
    if the title matches none of the nine topics -- that mention simply
    doesn't contribute to any topic's daily count.
    """
    return [
        topic
        for topic, patterns in _TOPIC_PATTERNS.items()
        if any(pattern.search(title) for pattern in patterns)
    ]


# --------------------------------------------------------------------------
# Timestamp parsing (tolerant, never raises -- same posture as
# watcher/ranking.py's own _parse_iso8601).
# --------------------------------------------------------------------------


def _parse_iso8601(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day_index(published_at: datetime, today: date, window_days: int) -> int | None:
    """This item's index into a ``window_days``-length ``daily_counts``
    array (0 = oldest, ``window_days - 1`` = today), or ``None`` if the
    item falls outside the window (older than ``window_days - 1`` days
    ago, or dated in the future relative to ``today``).
    """
    age_days = (today - published_at.date()).days
    if age_days < 0 or age_days >= window_days:
        return None
    return (window_days - 1) - age_days


# --------------------------------------------------------------------------
# Trend classification
# --------------------------------------------------------------------------
#
# Spec-silent exact rule (the plan/schema name the three trend labels but
# never define the threshold). Simplest reasonable choice, logged in
# IMPROVEMENT_BACKLOG.md: compare the sum of the most recent 3 days against
# the sum of the oldest 3 days (the middle day is excluded from either
# side, so a single mid-week spike alone can't flip the label); more
# recent > older -> "accelerating," less -> "cooling," exactly equal
# (including the common all-zero case) -> "flat."


def _classify_trend(daily_counts: list[int]) -> str:
    half = len(daily_counts) // 2
    older = sum(daily_counts[:half])
    recent = sum(daily_counts[-half:])
    if recent > older:
        return "accelerating"
    if recent < older:
        return "cooling"
    return "flat"


# --------------------------------------------------------------------------
# compute_whats_moving
# --------------------------------------------------------------------------


def compute_whats_moving(
    hn_items: Iterable[Any],
    *,
    now: datetime,
    window_days: int = WINDOW_DAYS,
) -> dict[str, Any]:
    """Build the ``whats_moving.schema.json``-shaped payload from this
    run's HN items and an explicit ``now`` (no live clock calls here).

    Always emits exactly one entry per canonical topic (``TOPIC_KEYWORDS``'s
    nine keys, in schema-enum order) -- including topics with an all-zero
    ``daily_counts`` -- rather than only topics some item happened to
    match this week. Matches the schema's own description ("one entry per
    card topic tag"), read literally: the strip is a fixed nine-row
    weekly snapshot, not a data-dependent subset. Logged in
    IMPROVEMENT_BACKLOG.md.

    Non-HN items (if ``hn_items`` is ever fed a mixed pool by mistake) are
    silently ignored -- this is a HN-mention-count strip specifically, per
    CLAUDE.md's own "compute whats_moving.json from HN counts" wording.
    Items with a missing/unparseable ``published_at``, or one that falls
    outside the ``window_days`` window, are skipped rather than raising.
    """
    today = now.date()
    counts: dict[str, list[int]] = {topic: [0] * window_days for topic in TOPIC_KEYWORDS}

    for item in hn_items:
        if getattr(item, "source_type", None) != "hn":
            continue

        published = _parse_iso8601(item.published_at)
        if published is None:
            logger.warning(
                "Skipping HN item with unparseable published_at for "
                "whats_moving: %r",
                getattr(item, "url", item),
            )
            continue

        index = _day_index(published, today, window_days)
        if index is None:
            continue

        for topic in classify_topics(item.title):
            counts[topic][index] += 1

    topics_payload = [
        {
            "topic": topic,
            "daily_counts": counts[topic],
            "trend": _classify_trend(counts[topic]),
        }
        for topic in TOPIC_KEYWORDS
    ]

    return {
        "generated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": window_days,
        "topics": topics_payload,
    }


# --------------------------------------------------------------------------
# load / save -- schema-valid round trip (same pattern as watcher/ledger.py)
# --------------------------------------------------------------------------


def load_whats_moving(path: Path | str = WHATS_MOVING_PATH) -> dict[str, Any] | None:
    """Load and schema-validate ``whats_moving.json`` at ``path``.

    Returns ``None`` if the file doesn't exist yet -- the very first
    watcher run has none -- rather than raising.
    """
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    validate(payload, "whats_moving")
    return payload


def save_whats_moving(
    hn_items: Iterable[Any],
    *,
    now: datetime,
    path: Path | str = WHATS_MOVING_PATH,
) -> dict[str, Any]:
    """Compute (:func:`compute_whats_moving`), schema-validate, and write
    ``data/whats_moving.json``. Returns the written payload. Same
    indent=2/sort_keys=True/trailing-newline formatting as
    ``watcher/ledger.py``'s ``save_ledger`` for legible, deterministic
    diffs on this committed data artifact.
    """
    payload = compute_whats_moving(hn_items, now=now)
    validate(payload, "whats_moving")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    return payload
