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
# Lab source recency window (watcher/sources/labs/registry.py)
# --------------------------------------------------------------------------

# Phase 1 PM checkpoint fix: OpenAI's live RSS feed (openai.com/news/rss.xml)
# serves its *entire* historical archive as <item> entries (1033 confirmed
# live on 2026-07-09, spanning back to 2023), not just recent releases --
# unlike DeepMind/Anthropic/DeepSeek, which are naturally newness-gated
# (DeepMind's feed is short-lived in practice, Anthropic is a live anchor-
# scrape of a small "recent news" index, and DeepSeek is a sitemap-diff that
# only ever yields newly-appeared URLs). Left unwindowed, that archive
# flooded the candidate pool and, worse, fed clustering.py's Jaccard pass a
# 2.5-year span of boilerplate-titled "Introducing GPT-..." announcements
# that chained into one 17-member mega-cluster occupying queue rank 1 --
# the "queue-sanity" defect flagged at the Phase 1 PM checkpoint. Every lab
# Item with a parseable ``published_at`` older than this window is dropped
# before clustering ever sees it; an unparseable/empty ``published_at``
# (DeepSeek's own Items always carry one -- see
# ``watcher/sources/labs/deepseek.py``) is *not* dropped by this filter,
# since DeepSeek's sitemap-diff technique already gates newness structurally
# (only newly-appeared sitemap URLs become Items at all -- there is nothing
# further to window). ~14 days comfortably covers this project's actual
# publication cadence for the three dated lab sources (confirmed against
# the real captured RSS/HTML fixtures: OpenAI/DeepMind/Anthropic all post
# multiple items well within any 14-day span) while being generous enough
# that a single slow news week never empties the lab candidate pool
# entirely. Spec-silent choice (the approved plan names no lab-side recency
# window at all); logged in IMPROVEMENT_BACKLOG.md.
LAB_RECENCY_WINDOW_DAYS = 14

# --------------------------------------------------------------------------
# Clustering (watcher/clustering.py)
# --------------------------------------------------------------------------

# Below exact-URL-normalization match, cluster titles together when the
# Jaccard similarity of their stopword-stripped token sets is >= this.
JACCARD_SIMILARITY_THRESHOLD = 0.35

# Stricter bar applied specifically when *both* the candidate item and the
# existing cluster's seed are ``source_type == "lab"``. Phase 1 PM
# checkpoint fix, alongside ``LAB_RECENCY_WINDOW_DAYS`` above: lab
# announcement titles are short and heavily templated ("Introducing
# GPT-5.4", "Introducing GPT-5.5", "Introducing gpt-oss-safeguard", ...),
# so two or three shared boilerplate tokens ("introducing", "gpt") alone
# are already enough to clear the general 0.35 bar even between titles
# about genuinely different releases -- confirmed against the real
# captured OpenAI RSS fixture's ~100 "Introducing ..." titles, where
# distinct-version pairs like "Introducing GPT-5" / "Introducing GPT-5.4"
# scored 0.4-0.75 under the general tokenizer. Cross-source pairs (a lab
# item vs. an HN/arXiv item) keep the general 0.35 bar unchanged --
# corroboration is exactly what that comparison exists to catch, and a
# non-lab title is not templated the same way. 0.65 was chosen (not a
# lower value like 0.5 or 0.6) because it is the smallest value, checked
# against the real fixture data above, that excludes every observed
# distinct-release pair (the highest false-positive score found was 0.75
# only for genuinely-same-version companion articles, e.g. "Introducing
# GPT-5.2" / "Introducing GPT-5.2-Codex") while still admitting those
# same-version companion pieces. Logged in IMPROVEMENT_BACKLOG.md,
# including the known residual limitation and the tokenizer fix
# (``watcher/models.py``'s ``tokenize_title``) made alongside it.
LAB_LAB_JACCARD_SIMILARITY_THRESHOLD = 0.65

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
