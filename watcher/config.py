"""Central constants for the Phase 1 watcher.

Every fetcher (HN, arXiv, each lab source) and every pure-code pipeline
stage (clustering, ranking, queue writing) imports its tunables from here
rather than hard-coding them locally -- one place to read, one place to
change. Values are taken verbatim from the approved build plan where the
plan states them explicitly; anything the plan is silent on gets the
simplest reasonable choice, logged in IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# HTTP fetch discipline (watcher/http.py)
# --------------------------------------------------------------------------

# Descriptive User-Agent naming the bot + a contact, per CLAUDE.md's
# anonymity rule (no personal identifiers -- the repo URL and the bot's
# noreply commit address are the contact, same identity used for commits).
USER_AGENT = (
    "AIFrontierWireBot/1.0 "
    "(+https://github.com/0xfanbase/AI-Radar; bot@users.noreply.github.com)"
)

# "3 attempts" per the approved plan's fetch-discipline bullet. Interpreted
# as 3 total GET attempts (1 initial + up to 2 retries), not 3 retries after
# an initial attempt -- see watcher/http.py's fetch() docstring for how this
# is actually enforced (an explicit status-based retry loop, not solely an
# adapter-embedded urllib3.Retry, because requests-mock -- used by our
# deterministic test suite -- replaces Session.send()/get_adapter()
# wholesale and never exercises an adapter's own embedded Retry object;
# logged in IMPROVEMENT_BACKLOG.md).
MAX_RETRIES = 3

# Exponential backoff base: sleep = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
# between retryable attempts, matching urllib3.Retry's own backoff formula.
BACKOFF_BASE_SECONDS = 1.0

# Always passed explicitly on every request -- no unbounded fetch.
REQUEST_TIMEOUT_SECONDS = 10

# HTTP statuses that trigger a retry (rate-limited or transient server
# failure). 4xx client errors other than 429 are never retried.
RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

# ETag/Last-Modified response cache, one JSON file per fetched URL, keyed by
# sha256(url). Gitignored (data/.cache/ in .gitignore) -- this is transient
# fetch state, never a committed artifact.
CACHE_DIR = REPO_ROOT / "data" / ".cache"

# --------------------------------------------------------------------------
# HN Algolia source (watcher/sources/hn.py)
# --------------------------------------------------------------------------

# Candidate pool: points >= threshold OR velocity (points/hour since post
# time) >= threshold, within the lookback window.
HN_POINTS_THRESHOLD = 50
HN_VELOCITY_THRESHOLD_PTS_PER_HOUR = 5.0
HN_LOOKBACK_HOURS = 48

# Keyword filter so HN's general front page doesn't flood the candidate
# pool with non-AI stories. Spec says only "keyword-filtered" with no exact
# list; this set is the simplest reasonable choice covering the project's
# own topic-tag vocabulary (models/research/chips/policy/products/safety/
# open-source/China/funding) plus the obvious top-level terms -- logged in
# IMPROVEMENT_BACKLOG.md.
HN_KEYWORDS = (
    "ai",
    "artificial intelligence",
    "llm",
    "large language model",
    "gpt",
    "chatgpt",
    "claude",
    "gemini",
    "anthropic",
    "openai",
    "deepmind",
    "deepseek",
    "mistral",
    "qwen",
    "machine learning",
    "neural network",
    "transformer",
    "diffusion model",
    "generative ai",
    "foundation model",
    "agentic",
    "chip export",
    "nvidia",
    "tpu",
    "gpu cluster",
)

# --------------------------------------------------------------------------
# arXiv source (watcher/sources/arxiv.py)
# --------------------------------------------------------------------------

ARXIV_CATEGORIES = ("cs.AI", "cs.CL", "cs.LG")

# --------------------------------------------------------------------------
# Clustering (watcher/clustering.py)
# --------------------------------------------------------------------------

# Below exact-URL-normalization match, cluster titles together when the
# Jaccard similarity of their stopword-stripped token sets is >= this.
JACCARD_SIMILARITY_THRESHOLD = 0.35

# --------------------------------------------------------------------------
# Ranking (watcher/ranking.py)
# --------------------------------------------------------------------------

# score = primary_source_weight(source_type) x cross_source_count x
#         hn_velocity_score(floor=HN_VELOCITY_SCORE_FLOOR)
PRIMARY_SOURCE_WEIGHTS = {
    "lab": 3.0,
    "arxiv": 2.0,
    "hn": 1.0,
}
HN_VELOCITY_SCORE_FLOOR = 0.05

# Top-N ranked clusters written to data/queue.json for the analyst.
MAX_QUEUE_SIZE = 8
