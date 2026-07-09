# IMPROVEMENT_BACKLOG.md

This file has two jobs:

1. **Decision log.** The build spec is detailed but not exhaustive. Every
   time the spec is silent on a concrete detail and a judgment call has to
   be made to keep building, the simplest reasonable choice is picked and
   logged here, as it is made — never left ambiguous in code without an
   entry explaining the choice. Entries are one line where possible:
   what was decided, and why.
2. **Findings log.** Once the weekly `audit.yml` exists, it auto-appends
   its findings here (link rot, lexicon coverage gaps, verifier pass-rate
   trend concerns, missed-story checks, duplicate-topic detections). The
   fortnightly `improve.yml` picks the highest-severity unaddressed item
   from this file (oldest first on ties) as its next self-improvement
   target.

Newest entries at the bottom of each section, in commit order.

---

## Decisions (spec-silent judgment calls)

- **2026-07-09 — Unpinned dependency versions in `requirements.txt` /
  `requirements-dev.txt`.** The spec doesn't call for pinned versions; kept
  the dependency list minimal and unpinned for now (`requests`,
  `feedparser`, `beautifulsoup4`, `jsonschema`; dev adds `pytest`,
  `requests-mock`). CI running `pytest` on every push will surface any
  breaking upstream release quickly; pinning can be revisited later if that
  becomes a problem.
- **2026-07-09 — `tests/conftest.py` blocks the network at the
  `requests.sessions.Session.request` seam specifically**, rather than at a
  lower-level socket/urllib3 layer. This is deliberate: the watcher's HTTP
  layer (arriving in a later commit) is designed to funnel every fetcher
  (labs, arXiv, HN) through one shared `requests.Session`, so this single
  choke point will cover all of them once that layer lands. Flagged here so
  it isn't forgotten if a future fetcher bypasses `requests` (e.g. calling
  `feedparser.parse(url)` directly, which uses its own `urllib` fetch path
  rather than a shared session) — any such fetcher must be wired to fetch
  raw bytes via the shared session first and hand the bytes to `feedparser`,
  not let `feedparser` fetch on its own, or it will silently escape this
  guard.
- **2026-07-09 — Quota-degradation ladder detail (manual toggle vs.
  automatic) left unspecified in this scaffolding commit.** `CLAUDE.md`
  documents the ladder's four levels per the literal spec text (8 → 5 →
  every-other-day → weekly digest) plus the unconditional empty-queue-skip
  rule, but the concrete trigger/toggle mechanism is explicitly deferred to
  whichever commit builds the analyst, where it will be decided and logged
  as its own entry.
- **2026-07-09 — `ledger.schema.json` shape.** Spec sec-4 only says
  `ledger.json (cluster hash → card id)`. Chose a top-level object
  `{version, entries}` (matching the required seed `{"version":1,"entries":{}}`)
  where `entries` maps `cluster_hash` → `{card_id, status, first_seen,
  last_seen, member_urls, verifier_outcome?}`. `status` enum is
  `queued|published|dropped`; `card_id` is nullable and stays permanently
  null for dropped clusters, matching the plan's stated behavior that a
  later corroborating source produces a fresh `cluster_hash` rather than
  reviving a stuck entry.
- **2026-07-09 — `queue.schema.json` shape.** Spec sec-4 only says
  `data/queue.json (≤8 clusters, each with ALL source URLs)`. Chose a
  top-level array (`maxItems: 8`), each item `{cluster_hash, rank, score,
  sources[]}` with `sources[]` = `{url, source_type: lab|arxiv|hn, title,
  outlet?, points?}` — enough for the Phase 2 analyst to fetch every
  member source without inventing structure, and `cluster_hash` doubles as
  the join key back into `ledger.json`.
- **2026-07-09 — `whats_moving.schema.json` shape.** Spec names the
  artifact (sec-2.5, sec-5) as an HN-mention-count topic-velocity strip but
  gives no fields. Chose `{generated_at, window_days: 7, topics: [{topic,
  daily_counts[7], trend}]}`, reusing the card schema's exact topic enum
  and pre-computing both the 7 daily counts (feeds `site/lib/svg_sparkline.py`
  directly) and a `trend` label (`accelerating|cooling|flat`) so the
  frontend never has to infer "accelerating vs. cooling" from color/slope
  alone (accessibility rule in the design spec).
- **2026-07-09 — `corrections.schema.json` shape.** Uses the exact minimal
  shape already fixed in the approved build plan's Phase 2 section —
  `{id, card_id, original_claim, corrected_claim, reason, source_url,
  corrected_at}` — as a top-level array, so this one is a direct transcription
  rather than a new judgment call.
- **2026-07-09 — Card schema `citations[]` cardinality.** Spec sec-5 lists
  `citations:[{url,outlet,quote}]` with no minimum count, while sec-0's
  elevator pitch says cards carry "≥2 sources"; the corroboration rule
  itself allows a single strong primary source to justify CONFIRMED.
  Resolved by setting `minItems: 1` on the schema (the loosest reading that
  still requires at least one real citation) and leaving the ≥2-sources
  norm as an analyst-prompt-level target rather than a hard schema
  constraint, since a schema requiring 2 would wrongly reject a
  single-primary-source CONFIRMED card.
- **2026-07-09 — Date vs. datetime granularity on `frontier_board`'s
  `release_date`/`last_verified`.** Both use `format: date` (day
  granularity, not `date-time`) since model releases and fact-check passes
  are naturally day-granular events and the Board's "pulse in last 7 days"
  rule only needs day resolution.
- **2026-07-09 — `tests/conftest.py` network-block seam moved from
  `Session.request` to `Session.send` (correction of the 2026-07-09 entry
  above).** Building `watcher/http.py`'s requests-mock test suite surfaced
  that patching `Session.request` shadows `requests-mock` itself:
  `requests-mock` works by patching `Session.send`/`Session.get_adapter`,
  which `Session.request` never reaches if something upstream already
  raises before calling it. Verified empirically (a probe test hung on the
  original seam even for a fully mocked, non-live call). Moved the guard to
  `Session.send` — the same choke point every fetcher still funnels
  through, but one layer lower, so `requests-mock`'s own patch (applied
  after this autouse fixture's, since it's the fixture the test function
  explicitly requests) composes correctly: a genuine outbound call still
  raises immediately unless `@pytest.mark.live`, but a `requests-mock`
  mocked call now returns its canned response as intended. No test files
  outside `tests/conftest.py` needed to change.
- **2026-07-09 — `watcher/http.py`'s retry/backoff is an explicit loop in
  `fetch()`, not solely a `Retry` object handed to `HTTPAdapter(max_retries=)`.**
  The task's shape ("a requests.Session with a urllib3 Retry adapter") is
  the standard idiom, but `requests-mock` replaces `Session.send`/
  `get_adapter` wholesale, so a Retry object embedded in a *mounted
  adapter* never actually runs against a mocked response — confirmed by a
  failing probe (a `[503, 503, 200]` mock sequence returned only the first
  503, with zero automatic retries, since requests-mock's adapter fully
  substitutes for ours). Resolution: the mounted `HTTPAdapter`'s `Retry`
  still guards genuine connection-level failures (DNS, dropped
  connections, read timeouts) via `total`/`connect`/`read`, with
  `status_forcelist=()` so it never double-retries on HTTP status; status-
  based retries (429/5xx) are instead an explicit loop in `fetch()` using
  urllib3's own exponential-backoff formula
  (`backoff_base * 2**(attempt-1)`). This keeps retries genuinely
  urllib3-flavored while being deterministically testable with
  requests-mock, which the task specifies as the test tool.
- **2026-07-09 — `USER_AGENT` string.** No exact wording specified beyond
  "naming the bot + a contact." Chose
  `AIFrontierWireBot/1.0 (+https://github.com/0xfanbase/AI-Radar; bot@users.noreply.github.com)`
  — repo URL + the same bot noreply address already used for commit
  identity, consistent with the anonymity rule (no personal identifiers).
- **2026-07-09 — `HN_KEYWORDS` list, `normalize_url()`'s stripped tracking
  params, and `tokenize_title()`'s stopword list (`watcher/config.py`,
  `watcher/models.py`).** The spec says HN is "keyword-filtered" and
  clustering is "exact-URL-normalization match, else Jaccard ≥0.35 over
  stopword-stripped title tokens" but gives no exact word lists. Picked
  simple, obviously-reasonable sets (common AI/lab/product terms; common
  URL tracking params like `utm_*`/`fbclid`/`gclid`; a short common English
  stopword list) rather than leaving them ambiguous — easy to extend later
  once real HN/lab titles are seen in Phase 1's live-verification runs.
- **2026-07-09 — Retry count semantics: `MAX_RETRIES=3` means 3 total GET
  attempts (1 initial + up to 2 retries), not 3 retries after an initial
  attempt.** Matches the plan's literal wording ("`urllib3.Retry` (3
  attempts, exponential backoff)") and the test scenario ("simulated
  503->503->200 sequence" = exactly 3 calls).
