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
- **2026-07-09 — `watcher/sources/hn.py`: the live HN Algolia API now
  rejects `numericFilters` on `points`/`num_comments` outright** (`HTTP
  400 "invalid numeric attribute(points), attribute not specified in
  numericAttributesForFiltering setting"`), confirmed via real calls to
  both `search_by_date` and `search`, tags on or off — the plan's
  original "broad pool via search_by_date with points>=20" assumed
  server-side points filtering was still available. Only `created_at_i`
  still filters server-side, so the points threshold (and the final
  points>=50-OR-velocity>=5.0 candidacy check) is applied entirely
  client-side instead, after a broad time-bounded pull.
- **2026-07-09 — `watcher/sources/hn.py`: windowed `search_by_date`
  queries instead of one query across the full 48h lookback.** Algolia
  caps any single query at 1000 accessible hits
  (`paginationLimitedTo`) — confirmed live: a plain 48h `created_at_i`
  filter matched 2326 stories but page 1 came back empty with an explicit
  "you can only fetch the first 1000 hits" message, meaning an
  un-windowed query would have silently truncated the lookback to
  whatever the newest 1000 stories reach back to (well under 48h on a
  busy day). Resolved by splitting the lookback into four non-overlapping
  12h sub-windows, queried separately and merged/de-duplicated by
  `objectID` — each comfortably clears the cap (verified live:
  574/591/576/585 hits per window, summing exactly to the single query's
  own `nbHits`).
- **2026-07-09 — `watcher/sources/hn.py`'s `HN_KEYWORDS` matching uses
  whole-word regex (`\bkeyword\b`), not a naive `keyword in
  title.lower()` substring check.** A naive substring check on the
  single-token keyword `"ai"` false-positives on ordinary English words
  that merely contain that letter pair — confirmed against a real HN
  title fetched live while building this fetcher: "Chat Control 1.0 and
  2.0 Explained" contains "ai" inside "Expl-ai-ned" but is not
  AI-relevant. Word-boundary matching avoids this while still matching
  the list's multi-word phrase keywords (e.g. "chip export") correctly.
- **2026-07-09 — `BROAD_POOL_POINTS_THRESHOLD = 20` (the plan's
  "broad pool via search_by_date with points>=20" pre-filter) is kept
  local to `watcher/sources/hn.py`, not added to `watcher/config.py`.**
  `config.py`'s own docstring already reserves `HN_POINTS_THRESHOLD`
  specifically for the final-candidacy points bar (50), and this turn's
  file scope was `hn.py` only — same pattern `watcher/sources/arxiv.py`
  already established for its locally-scoped `ARXIV_MAX_RESULTS`.
  Interaction note for whoever tunes these later: because the broad-pool
  prefilter (points>=20) runs before the final-candidacy check
  (points>=50 OR velocity>=5.0), a very fresh, sub-20-point story could
  in principle hit velocity>=5.0 (e.g. 6 points at 1h old) yet never
  reach the final check because it's filtered out at stage 1 first. This
  is the literal, as-specified algorithm rather than a bug worth
  silently working around; flagged here in case a future pass wants the
  two thresholds reconciled.
- **2026-07-09 — `hn.algolia.com/robots.txt` currently 404s** (confirmed
  live), which `watcher.http.check_robots_allowed()` treats as allow-all
  — unlike `export.arxiv.org` (see the entry above), there is no policy
  conflict to flag for this source; the gate is still called on every
  fetch rather than assumed to stay that way.
- **2026-07-09 — `watcher/sources/hn.py` public function name:
  `fetch_hn_items(session, ...)`,** matching the
  `fetch_<source>_items(session, ...)` shape already used by
  `watcher/sources/arxiv.py`'s `fetch_arxiv_items` for a consistent
  per-source-module interface.
- **2026-07-09 — `watcher/sources/arxiv.py`: "daily top papers" has no
  popularity signal on arXiv itself, so it's interpreted as "most recently
  submitted"** — one combined query OR-ing cs.AI/cs.CL/cs.LG, sorted by
  `submittedDate` descending, capped at `ARXIV_MAX_RESULTS = 50`. Matches
  the plan's own note that arXiv's "unusual velocity" is instead captured
  via cross-source corroboration at the ranking stage, not by this
  fetcher. A single combined OR-query (rather than one request per
  category) is also politer per the fetch-discipline rules and needs no
  client-side merge/dedupe, since arXiv itself returns a cross-listed
  paper only once. `ARXIV_MAX_RESULTS` is kept local to `arxiv.py` rather
  than added to `watcher/config.py`, since this commit's file scope was
  limited to the arXiv fetcher itself; worth consolidating into
  `config.py` alongside the existing `ARXIV_CATEGORIES` in a later pass.
- **2026-07-09 — `watcher/sources/arxiv.py` item URL is the Atom entry's
  `<link rel="alternate">` (the `https://arxiv.org/abs/...vN` page),
  falling back to `<id>` only if no alternate link is present** — kept
  with its version suffix as arXiv provides it, rather than stripped to a
  version-less canonical form; the plan is silent on this and stripping
  would diverge from what the source actually returned for no clear
  benefit at this stage.
- **2026-07-09 — IMPORTANT DISCOVERY, needs an explicit owner/next-phase
  decision: `https://export.arxiv.org/robots.txt` (the arXiv Atom API
  host) currently returns `User-agent: *\nDisallow: /` — a blanket
  disallow of every path for every user agent — confirmed via a real,
  live fetch on 2026-07-09 (not assumed).** Per CLAUDE.md's fetch-
  discipline rule, applied uniformly ("if a source blocks fetching, drop
  that source; never circumvent a disallow"), `watcher/sources/arxiv.py`
  calls `watcher.http.check_robots_allowed()` before every query exactly
  like every other fetcher, and — because this subagent's scope was
  limited to `watcher/sources/arxiv.py` and its tests/fixture only, with
  no authority to reinterpret or amend CLAUDE.md's stated policy — it
  honors that disallow rather than special-casing it. **The practical
  consequence: as implemented, this fetcher returns `[]` when run against
  the real network today**, even though `export.arxiv.org` is arXiv's own
  documented, keyless, rate-limited public API endpoint (governed by its
  own published Terms of Use at arxiv.org/help/api/tou, a separate
  contract from robots.txt) rather than a crawlable website — many API
  hosts blanket-disallow `robots.txt` specifically to keep generic search
  crawlers from indexing raw API responses, not to prohibit the
  documented API usage the host exists to serve. This fetcher's own tests
  (`tests/test_arxiv_fetch.py`) cover both the real disallow-skip behavior
  (using the exact live-fetched robots.txt content) and the parsing/
  normalization logic under a mocked allow, so the code itself is fully
  correct either way — but whoever integrates this fetcher into the
  watcher CLI / `watch.yml` should explicitly decide, and log: (a) accept
  that arXiv is currently a non-functional source under this policy as
  written, (b) amend CLAUDE.md's fetch-discipline rule to distinguish
  "crawling a website" from "calling a documented, ToU-governed public
  API" (and apply that distinction consistently to any other API-only
  sources, e.g. HN Algolia), or (c) some other explicit resolution — not
  something this commit should decide unilaterally given the "never
  circumvent a disallow" rule is stated as project-wide, load-bearing
  policy.
- **2026-07-09 — Integration pass across the three parallel fetcher
  commits: `watcher/sources/__init__.py` (the shared package marker for
  both `hn.py` and `arxiv.py`, siblings under `watcher/sources/`) is
  committed alongside the arXiv fetcher rather than the HN fetcher.**
  Neither sibling commit's designated file set named it explicitly;
  picked the arXiv commit since it's the second of the two root-level
  `watcher/sources/*.py` modules to land, so the package marker arrives
  no later than any code that needs it importable. (`python -m pytest`
  stayed green throughout regardless, since Python 3 treats a directory
  with no `__init__.py` as an implicit namespace package -- this is a
  commit-hygiene choice, not a functional fix.) `watcher/sources/labs/__init__.py`
  needed no equivalent call: it ships as part of the `labs/` fetchers'
  own commit, which already owns everything under that subpackage.
- **2026-07-09 — Alibaba Qwen dropped as a Phase 1 lab source, per the
  approved build plan's own note.** Its legacy blog redirects to a
  JS-only SPA with no RSS feed and no server-rendered content to scrape
  or diff. The four labs actually registered
  (`watcher/sources/labs/registry.py`) — OpenAI, Google DeepMind,
  Anthropic, DeepSeek — are the full Phase 1 lab set; re-adding Qwen
  later would need either a headless-browser fetch (out of scope for a
  pure-code, dependency-light watcher) or discovery of an
  undocumented feed/API.
- **2026-07-09 — Anthropic's news-index anchor-scrape
  (`watcher/sources/labs/html_common.py::extract_news_anchors`) targets
  any element whose class *contains* the substring "title" (case-
  insensitive) as the headline, rather than a fixed tag/class.**
  Confirmed live on 2026-07-09: `https://www.anthropic.com/news` nests a
  category label, a `<time>`, a headline element, and (for "featured"
  cards) a body-preview paragraph all inside one `<a>`, and marks the
  headline with a hashed CSS-module class name ending in `__title` in
  every card layout seen (an `h4` for the featured grid, a `span` for
  the plain list) — substring match on hashed-but-still-semantically-
  named classes is more resilient to a rebuild changing the hash prefix
  than depending on the exact tag name. Falls back to the anchor's own
  text (minus any `<time>` descendant) if no title-classed element is
  found, so a future redesign degrades to a slightly messier title
  rather than crashing or returning nothing — exercised by the fixture's
  own plain footer link to the Responsible Scaling Policy story, which
  has no title-classed child at all.
- **2026-07-09 — Anthropic anchor dates are parsed from `<time>` text in
  the exact `"Mon D, YYYY"` format the live page uses (e.g. "Jun 30,
  2026") into a day-granularity ISO 8601 UTC timestamp at midnight.**
  The source page gives no time-of-day, so midnight-UTC is the simplest
  reasonable placeholder; an unparseable or missing `<time>` yields `""`
  rather than raising or guessing a fabricated date.
- **2026-07-09 — DeepSeek's "previously-seen sitemap" state
  (`watcher/sources/labs/deepseek.py`) is a dedicated persisted file
  (`data/.cache/deepseek_sitemap_seen.json`), not a reuse of
  `watcher/http.py`'s own ETag response cache.** That cache is keyed by
  `sha256(url)` and is overwritten with the new body as an inherent part
  of every successful fetch's own conditional-GET bookkeeping, so by the
  time a caller could read it back the "previous" sitemap body would
  already be gone; a dedicated file, read before today's sitemap fetch
  runs, keeps the diff correct without reaching into another module's
  private (`_`-prefixed) cache internals.
- **2026-07-09 — DeepSeek article Items carry `published_at=""`.** The
  approved plan's DeepSeek technique is explicitly "sitemap-diff plus an
  h1-extraction of the article page" — neither step yields a
  publish date (confirmed live: the real captured article page's `<h1>`,
  `<title>`, and visible body carry no machine-parseable date; only an
  in-sidebar breadcrumb string embeds one informally, which is not part
  of the specified extraction technique). Left empty rather than
  fabricated or guessed from the slug's `newsYYMMDD`-ish convention
  (confirmed inconsistent across older slugs, e.g. `news0725`,
  `news1120`, which don't fit that shape).
- **2026-07-09 — `watcher/sources/labs/registry.py`'s
  `fetch_all_lab_items` wraps every individual lab fetcher call in a
  broad `except Exception`, extending the "skip a source cleanly rather
  than crash the whole run" fetch-discipline rule from robots.txt
  disallows (already handled per-fetcher) to any unexpected failure** (a
  genuine network error surviving retries, an upstream response so
  malformed even the lenient parser chokes). Spec-silent extension,
  logged here rather than left as an implicit assumption; one lab's
  outage should never take down the other three.
- **2026-07-09 — Live-verified during this fixture-recording pass (not
  reused from an earlier planning pass), 2026-07-09: `openai.com`,
  `deepmind.google`, and `www.anthropic.com` all currently return
  allow-all `robots.txt` policies for these fetch paths, and
  `api-docs.deepseek.com/robots.txt` 404s (treated as allow-all per the
  existing arXiv/HN convention).** No policy conflicts to flag for any
  of the four lab sources, unlike `export.arxiv.org`'s blanket disallow
  noted above. The robots.txt-disallow *skip* behavior itself is still
  covered for all four lab fetchers using synthetic disallow fixtures in
  `tests/test_lab_fetch_rss.py`/`tests/test_lab_fetch_html_diff.py`,
  since the real policies currently allow everything and a test can't
  exercise a disallow path against real, permissive robots.txt content.
- **2026-07-09 — `watcher/clustering.py`: a candidate item's Jaccard
  similarity is checked only against each existing cluster's *seed*
  (earliest-sorted) member, not every member.** The plan says only
  "Jaccard similarity >= 0.35 over ... title tokens", not which item in
  a multi-member cluster to compare a new candidate against. Comparing
  against the seed keeps the pass genuinely single-pass
  (O(items x clusters), not O(items x cluster_size)) and deterministic
  (the seed never depends on join order within a run); a later pass
  could revisit this to compare against every member (max similarity)
  if seed-only comparison proves too strict in practice.
- **2026-07-09 — `watcher/clustering.py` computes `cluster_hash` on
  each `Cluster` (`sha256` of its sorted normalized member URLs,
  newline-joined) rather than leaving that to `watcher/ledger.py`.**
  `schemas/ledger.schema.json`'s own description already states
  "sha256 of sorted normalized member URLs, per watcher/clustering.py",
  and `watcher/ranking.py` (built concurrently) duck-types every
  cluster it scores as already exposing a `.cluster_hash` string
  attribute — so this module is the natural, already-implied owner.
  It's a pure function of a cluster's own membership (no ledger state
  needed), exposed both as `Cluster.cluster_hash` and as a standalone
  `compute_cluster_hash()` so `watcher/ledger.py` can re-derive the same
  hash from a persisted `member_urls` list without needing a live
  `Cluster` instance. The exact serialization (sorted list, newline-
  joined, UTF-8 encoded before hashing) is itself a spec-silent pick —
  the plan states "sha256 of sorted normalized member URLs" without
  specifying how a list becomes hashable bytes.
- **2026-07-09 — `watcher/ranking.py`'s cluster interface is duck-typed
  (an `.items` iterable), not an import of `watcher.clustering.Cluster`.**
  Built in parallel with `watcher/clustering.py`; rather than block on
  that module's exact shape, ranking.py only assumes a cluster exposes
  `.items` (Item-alikes with `source_type`/`source_name`/`published_at`/
  `points`/`url`). Verified after the fact (both modules landed in the
  same tree) that real `Cluster` instances from `watcher/clustering.py`
  work with `watcher/ranking.py`'s `rank_clusters()` unmodified.
- **2026-07-09 — `watcher/ranking.py`'s tie-break prefers a cluster's own
  `.cluster_hash` attribute when present, else computes the identical
  `sha256`-of-sorted-normalized-member-URLs formula itself from
  `cluster.items`.** Keeps ranking.py's own tie-break/`RankedCluster.
  cluster_hash` correct and fully deterministic without a hard import of
  `watcher.clustering`, while exactly matching (confirmed via a live
  smoke test against real `Cluster` instances) the canonical
  `Cluster.cluster_hash` that module now computes — no second, silently-
  diverging hash implementation.
- **2026-07-09 — `RankedCluster` (ranking.py's return type) is a small
  `{rank, score, cluster_hash, cluster}` wrapper, not bare clusters.**
  `queue.schema.json` requires `cluster_hash`/`rank`/`score` per queue
  entry; wrapping lets a later `queue_writer.py` (out of this commit's
  scope) write those fields straight from `rank_clusters()`'s output
  without recomputing anything.
- **2026-07-09 — Ranking's `hn_velocity_score` age floor (`max(age_hours,
  1)`, per the approved formula verbatim) is a distinct constant from
  `watcher/sources/hn.py`'s own fetch-time age floor (1/60 hour).** The
  two floors serve different stages (HN's own points-vs-velocity
  candidacy filter at fetch time, vs. this ranking-stage score formula)
  and are deliberately not shared, to avoid one stage's tuning silently
  changing the other's behavior.
- **2026-07-09 — Added `tests/test_clustering_ranking_integration.py`, an
  explicit end-to-end proof that `watcher.clustering.cluster_items()`
  output feeds directly into `watcher.ranking.rank_clusters()` (and its
  per-cluster scoring helpers) with no adaptation layer.** Formalizes,
  beyond a one-off manual check, the claim in the duck-typing bullet
  above: covers merged/exact-URL-matched clusters, `RankedCluster.
  cluster_hash` coming from the real `Cluster.cluster_hash` attribute
  (not a coincidentally-matching fallback), top-`MAX_QUEUE_SIZE`
  selection from a larger real-cluster pool, and determinism of the
  combined pipeline across input shuffles.
- **2026-07-09 — `watcher/ledger.py`'s upsert does *not* persist a
  `times_seen` counter, even though the task description it was built
  against mentions one.** `schemas/ledger.schema.json` was already fixed
  in an earlier commit with `additionalProperties: false` and only
  `card_id`/`status`/`first_seen`/`last_seen`/`member_urls`/
  `verifier_outcome` — no counter field — and touching that schema is
  outside this commit's scope. `first_seen`/`last_seen` (bumped on every
  re-run, never duplicated) already fully satisfy the actual idempotency
  guarantee (a second identical run adds zero new ledger keys); a "how
  many times seen" count isn't needed to prove that, and inventing an
  unschemad field would break every schema-valid load/save round trip.
- **2026-07-09 — `watcher/ledger.py` reuses `watcher.clustering.
  compute_cluster_hash` rather than re-implementing cluster-hash
  computation.** `schemas/ledger.schema.json`'s own description already
  names `watcher/clustering.py` as that formula's owner; ledger.py only
  needs it to recover a hash from a plain `member_urls` list (e.g. this
  commit's own tests), never as a second, independently-maintained hash.
- **2026-07-09 — `watcher/ledger.py`'s `apply_run` upserts only the
  clusters that survive the "already published" filter — an
  already-published cluster's ledger entry is left completely untouched
  by a re-run, not even `last_seen`-bumped.** Spec-silent (the plan only
  says a re-run "adds zero new keys"); this reading keeps
  `first_seen`/`last_seen` meaningfully scoped to "still being tracked
  toward publication," and avoids rewriting settled history on every
  run for no benefit.
- **2026-07-09 — `watcher/ledger.py`'s `save_ledger` writes
  indent=2/sort_keys=True/trailing-newline JSON, unlike `watcher/http.py`'s
  compact `data/.cache/` entries.** `data/ledger.json` is a committed,
  human-reviewed data artifact (unlike the gitignored HTTP cache), so
  legible diffs are worth the extra bytes; the transient cache files have
  no such reason to expand.
- **2026-07-09 — `watcher/cli.py`'s `run()` calls `rank_clusters(...,
  limit=len(clusters))` (effectively uncapped), not the default
  `MAX_QUEUE_SIZE`.** The plan's own daily-loop ordering is "rank -> diff
  vs ledger -> write queue.json (<=8)" — capping at 8 *before* the ledger
  diff would let an already-published cluster occupying a top-8 score
  slot crowd out a fresh, still-unpublished story from that day's queue.
  `watcher/queue_writer.py` applies the real `MAX_QUEUE_SIZE` cap itself,
  after excluding already-carded clusters.
- **2026-07-09 — `watcher/cli.py`'s `run()` upserts ledger entries
  (`watcher.ledger.apply_run`) for *every* surviving unpublished cluster,
  not just the <=8 written to `data/queue.json`.** A story that doesn't
  make today's cut still gets its `first_seen` tracked, so a later run
  where it resurfaces (or gains cross-source corroboration) isn't
  mistaken for brand-new. `watcher/queue_writer.py`'s own cap only bounds
  what the analyst sees each day, not what the ledger remembers.
- **2026-07-09 — `watcher/queue_writer.py`'s `sources[].url` is the raw,
  un-normalized URL each fetcher captured, not `normalize_url`'s
  clustering/ledger dedup key.** The analyst needs a real, followable
  link (tracking params and all); normalization is an internal-only
  dedup detail.
- **2026-07-09 — `watcher/queue_writer.py`'s `sources[].outlet` is derived
  as the URL's own domain (netloc, lowercased, leading "www." stripped)
  for `source_type == "hn"` entries only, and `null` for `lab`/`arxiv`.**
  Matches `queue.schema.json`'s own field description ("e.g. HN's linked
  domain; null for lab/arxiv sources") read literally — a lab/arXiv item's
  URL already *is* the primary source, so there's no separate outlet name
  to surface.
- **2026-07-09 — `watcher/queue_writer.py` re-numbers each queue entry's
  `rank` 1..N *after* excluding already-carded clusters and applying the
  <=8 cap**, rather than keeping `RankedCluster.rank`'s original pre-filter
  position. A queue the analyst reads top-to-bottom should have a
  contiguous, meaningful rank within *that* queue, not gaps left behind by
  clusters that were filtered out.
- **2026-07-09 — `watcher/velocity.py`'s topic classifier
  (`TOPIC_KEYWORDS`) is a closed-set, whole-word/phrase keyword match**
  (same regex-word-boundary technique `watcher/sources/hn.py` already uses
  for its own AI-relevance gate), bucketing each HN item's title into zero
  or more of the nine `card.schema.json`/`whats_moving.schema.json` topic
  tags. The plan names the nine tags but never defines how a pure-code,
  no-AI pass should assign them to a headline; the exact keyword lists
  chosen are the simplest reasonable set covering each tag's obvious
  vocabulary (e.g. "gpu"/"tpu"/"nvidia" for chips/compute, "china"/
  "deepseek"/"alibaba" for China).
- **2026-07-09 — `watcher/velocity.py`'s trend classification
  (accelerating/cooling/flat) compares the sum of the most recent 3 days
  against the sum of the oldest 3 days of a topic's 7-day window,
  excluding the middle day from either side; an exact tie (including the
  common all-zero case) is "flat."** The spec/schema name the three
  labels (as "rising/falling/flat" in prose, `accelerating/cooling/flat`
  in the schema's actual enum, which is what's used verbatim) but never
  define a threshold; this is the simplest reasonable rule that isn't
  swayed by a single mid-week spike alone.
- **2026-07-09 — `data/whats_moving.json` always contains all nine
  canonical topics, zero-filled where there were no HN mentions that
  week, rather than only the topics with any activity.** Matches
  `whats_moving.schema.json`'s own description ("one entry per card topic
  tag") read literally: it's a fixed nine-row weekly strip, not a
  data-dependent subset — simpler for the frontend's masthead sparkline
  strip to render consistently every day.
- **2026-07-09 — `scripts/run_watcher_live.py`'s "new items per source"
  reporting is an in-process URL-set diff between consecutive `run()`
  calls in the same process** (via new `hn_urls`/`arxiv_urls`/`lab_urls`
  fields on `watcher.cli.RunResult`), not a second independent fetch or
  persisted "seen before" state. No fetcher exposes a "new since last
  call" concept of its own beyond the ETag cache/ledger, so comparing this
  run's fetched URL set against the previous run's (held in memory across
  the script's `--runs N` loop) is the simplest way to report a per-source
  delta without fetching twice.
- **2026-07-09 — `.github/workflows/watch.yml`'s cron is `17 6 * * *`
  (06:17 UTC), not CLAUDE.md's documented "daily 07:00 HKT" (23:00 UTC)
  target.** An off-hour minute avoids GitHub Actions' well-documented
  top-of-hour scheduling congestion; the exact hour is a Phase 1
  placeholder rather than a precise match to the eventual 07:00 HKT
  target, since Phase 2 will need to revisit this cron anyway once
  `analyze.yml` has to chain off of it. Worth tightening to an exact HKT
  match in a later phase; not a correctness issue for Phase 1's own
  acceptance criterion (live double-run idempotency), which doesn't
  depend on wall-clock timing at all.
- **2026-07-09 — `watch.yml`'s auto-commit message ends with `[skip ci]`
  and `actions/cache`'s key is `watcher-cache-${{ github.run_id }}` with a
  `watcher-cache-` restore-keys prefix.** Neither is spec-mandated;
  `[skip ci]` avoids `ci.yml` (which runs on every `push`) re-running
  pytest against a commit that only changes generated `data/*.json`, and
  the run-id-suffixed cache key is the standard idiom for a cache that
  should always re-save (an exact key hit skips the post-job save step,
  which would otherwise freeze `data/.cache/`'s ETag store at its first
  successful run forever).

## Phase 2, commit 12: ledger extension + new schemas (2026-07-09)

- **`schemas/ledger.schema.json`'s `status` enum is kept exactly as Phase 1
  already built it (`queued|published|dropped`), not replaced with a new
  `active|dropped` two-value enum.** This turn's task framing (written from
  the plan's generic description) asked for an *added* optional
  `status: active|dropped` field, but Phase 1 already shipped a *required*
  `status` field under this same key with a three-value enum whose
  `dropped` value already matches the plan's own literal ledger-drop
  wording (`status: "dropped"`) verbatim. A schema can only define one
  `status` property per object, and swapping the enum to `active|dropped`
  would invalidate every one of the real, committed `data/ledger.json`'s
  104 entries (all `status: "queued"`, not in `{active, dropped}`) —
  directly violating this turn's own hard requirement that the existing
  ledger keep validating unchanged. Resolved by treating `queued`/
  `published` as the two flavors of "active" (not permanently dropped) and
  leaving the enum untouched; logged here rather than silently ignoring
  the task text's literal wording.
- **`verifier_outcome` (already an optional, fully free-form
  `["object","null"]` field since Phase 1) now has a defined structure**:
  `last_attempted_at` (required within the object, `date-time`),
  `dropped_reason` (optional string), `demoted_from_confirmed` (optional
  boolean) — matching this turn's task spec verbatim. Still optional at
  the entry level (absent on every pre-Phase-2 entry) and still nullable,
  so no existing entry (none of which set this key) is affected.
- **Added an `if`/`then` to `ledger.schema.json`'s entry schema: `status ==
  "dropped"` implies `card_id` must be `null`.** This is the schema-level
  enforcement of the plan's stated invariant ("card_id stays null
  permanently for a dropped cluster_hash") rather than leaving it as an
  unenforced convention; safe to add now since no real entry is currently
  `dropped`.
- **`schemas/card_index.schema.json`'s top-level list key is named
  `cards`** (plan says only "a version + list of {...} entries," no field
  name given) — matches the artifact's own purpose
  (`content/cards/index.json`, one entry per published card) and the
  existing repo convention of naming a top-level array field after what it
  contains (cf. `whats_moving.schema.json`'s `topics`,
  `run_plan.schema.json`'s `clusters` below).
- **`card_index.schema.json`'s `status` enum reuses `card.schema.json`'s
  three values (`confirmed|reported|corrected`) verbatim, not a fourth
  `dropped` value.** A verifier-dropped cluster never gets a card in the
  first place (per `schemas/ledger.schema.json`'s own design), so this
  index — which only ever lists cards that were actually published — has
  no `dropped` case to represent; that state lives exclusively in
  `data/ledger.json`.
- **`schemas/run_plan.schema.json`'s `cards_cap` allows `0`** (not just
  `>=1`), since a `run_mode: "skip"`/empty-queue run still needs a
  representable "zero cards this run" value distinct from `null`
  (`null` is used when the field is simply not meaningful, e.g. paired
  with an empty `clusters[]`); the schema doesn't force a specific
  null-vs-zero convention between the two, leaving that to whichever
  commit builds `scripts/plan_run.py`.
- **No `data/run_plan.json` seed file is created**, per this turn's
  explicit instruction — `scripts/plan_run.py` (a later Phase 2 commit)
  is what will produce real ones; a placeholder null-equivalent file
  would just be dead weight until that script exists to write/validate
  against it.
- **`data/verifier_stats.json` = `{"version":1,"runs":[]}` and
  `data/pending_corrections.json` = `{"version":1,"pending":[]}`** seeded
  exactly as specified, written with the same `indent=2, sort_keys=True`
  + trailing-newline convention `watcher/ledger.py::save_ledger` already
  established for every other committed `data/*.json` artifact.
- **New schema tests live in a new `tests/test_p2_schemas.py`** (the
  instruction offered either extending `tests/test_schemas.py` or adding a
  new file) rather than folding into the existing file, so this turn's
  ledger-extension-specific fixtures/tests (`ledger_dropped.json` valid/
  invalid pairs, the real-committed-ledger-still-validates check) sit
  together with the four new schemas' own tests, without growing
  `test_schemas.py`'s already-established `SCHEMA_NAMES` parametrize list
  for a schema (`ledger`) it already covers.

## Phase 2, commit 13: relocate content/ artifacts (2026-07-09)

- **`data/lexicon.json` moved to `content/lexicon.json` (`git mv`), and
  `content/frontier_board.json`/`content/corrections.json` seeded fresh
  directly in `content/`, per the plan's §1 `/content` vs `/data` boundary
  rule and §3's explicit "relocate from `data/` to `content/`" instruction.**
  Phase 1 put `lexicon.json` in `data/` only because it was inert
  schema-only scaffolding before an analyst existed to make it real,
  LLM-authored, CC-BY-licensed editorial output; now that Phase 2 is
  underway, the boundary rule (content = LLM-authored editorial output;
  data = pure-code pipeline state) puts it, `frontier_board.json`, and
  `corrections.json` in `content/` where the analyst will actually write
  to them. `frontier_board.json` never had a `data/` instance in Phase 1
  (schema-only until this commit); `corrections.json` is seeded here for
  the same reason. All three still validate against their existing
  schemas (`schemas/lexicon.schema.json`, `schemas/frontier_board.schema.json`,
  `schemas/corrections.schema.json`) unchanged -- no seeding of real
  content happens yet, that's Phase 3. `schemas/lexicon.schema.json`'s
  description field (the only stale `data/lexicon.json` prose reference
  found repo-wide) is updated to name the new path and this migration;
  no code, test, or other doc referenced the old `data/lexicon.json` or
  `data/frontier_board.json` paths directly (confirmed via a repo-wide
  grep), so no other file needed a change.
- **`content/corrections.json` is seeded as a plain empty array (`[]`),
  not the `{"version":1,"corrections":[]}` object form this turn's task
  description named.** `schemas/corrections.schema.json` -- already
  built and merged in Phase 1, matching the approved plan's own §2
  minimal shape (`[{id, card_id, original_claim, corrected_claim,
  reason, source_url, corrected_at}]`) -- fixes this artifact's top level
  as `"type": "array"`, not an object with a `version`/`corrections`
  wrapper; the real fixture pair
  (`fixtures/schema_examples/{valid,invalid}/corrections.json`) confirms
  the same. An object-wrapped seed would fail `validate(instance,
  "corrections")` outright. Since this turn's hard rule requires the
  seed file to actually validate against the real schema, and changing
  the schema itself is outside this turn's scope, the array form is used
  instead -- the simplest resolution that satisfies both "seed
  `content/corrections.json`" and "validate against
  `schemas/corrections.schema.json`" without touching the schema.
- **Both new seed files use the same `json.dump(..., indent=2,
  sort_keys=True)` + trailing-newline convention** already established by
  `watcher/ledger.py::save_ledger` and the other committed `data/*.json`/
  `content/lexicon.json` artifacts, for consistent, legible diffs.

## Phase 1 PM checkpoint, round 1 (2026-07-09)

The PM review found one real acceptance-criterion defect (queue sanity) and
one backlog-completeness gap. Both are addressed below; a third item (the
arXiv robots.txt question) is deliberately *not* resolved here per the
PM's own instruction to carry it forward as an explicit owner decision.

- **2026-07-09 — `LAB_RECENCY_WINDOW_DAYS = 14` (`watcher/config.py`), a
  new recency window applied to the combined lab-item pool in
  `watcher/sources/labs/registry.py::fetch_all_lab_items`.** Fixes the
  queue-sanity defect the PM checkpoint flagged: `openai.com/news/rss.xml`
  serves its entire historical archive (1033 `<item>`s confirmed live,
  spanning back to 2023) rather than just recent releases, so an
  un-windowed run fed clustering.py's Jaccard pass a 2.5-year span of
  boilerplate "Introducing GPT-..." titles that chained into a single
  17-member mega-cluster occupying queue rank 1 (`cross_source_count=17`
  inflating its score to the top spot on *every* future run, since
  `queue_writer.py` only excludes already-carded clusters, never
  already-seen-but-garbage ones). Any lab Item with a *parseable*
  `published_at` older than 14 days is dropped before clustering ever
  sees it; an unparseable/empty one (DeepSeek's own Items always carry
  `published_at=""`, per its own entry above) is never dropped by this
  filter, since DeepSeek's sitemap-diff already gates newness structurally
  -- there is nothing further for a date-based window to check, and
  dropping on a missing date it was never going to have would silently
  zero out that entire source. 14 days is a spec-silent choice (the
  approved plan names no lab-side recency window at all): comfortably
  wider than the real observed publication cadence of the three dated lab
  sources (confirmed against the real captured RSS/HTML fixtures) so a
  single slow news week can't empty the lab candidate pool, while being
  far too short for a stale multi-year archive entry to ever qualify.
  `~14 days` was the PM's own suggested figure; adopted as-is rather than
  independently re-derived, since no sharper number is implied by any
  other Phase 1 constant.
- **2026-07-09 — Verified (see the live re-run captured in `PROGRESS.md`)
  that windowing alone collapses the reported mega-cluster from 17 members
  down to none of that shape** -- the OpenAI RSS pool drops from 1033
  archive items to whatever a 14-day window actually contains (15 in the
  real fixture snapshot used to design this fix), which structurally
  cannot span 2.5 years of "Introducing GPT-..." titles anymore.
- **2026-07-09 — Residual risk analyzed: even *within* one 14-day window,
  short/templated lab titles can still boilerplate-chain, so a second,
  independent fix was made** (both changes together, not either alone):
  1. **`LAB_LAB_JACCARD_SIMILARITY_THRESHOLD = 0.65`
     (`watcher/config.py`), applied in `watcher/clustering.py` via a new
     `_merge_threshold(item, seed)` helper** -- when *both* the candidate
     item and the existing cluster's seed are `source_type == "lab"`, the
     merge bar is 0.65 instead of the general 0.35; a lab-vs-non-lab
     comparison is unaffected. Chosen (not, e.g., 0.5 or 0.6) as the
     smallest value that, checked against the real captured OpenAI RSS
     fixture's ~100 "Introducing ..." titles, excludes every observed
     distinct-release pair (score range 0.4-0.6, e.g. "Introducing
     GPT-5.3-Codex" vs. "Introducing GPT-5.2-Codex" at 0.6) while still
     admitting genuinely-same-version companion articles (0.75-0.8, e.g.
     "Introducing GPT-5.2" vs. "Introducing GPT-5.2-Codex"). Known
     tradeoff, accepted: this also stops two real same-day companion
     posts sharing no version number from merging (e.g. the real fixture
     pair "Inside GeneBench-Pro" / "Introducing GeneBench-Pro", Jaccard
     0.5) -- a precision-over-recall choice, favoring not resurrecting the
     mega-cluster defect over catching every same-story companion piece.
  2. **`watcher/models.py::tokenize_title`'s `_WORD_RE` now keeps a
     dotted point-release number ("5.5", "4.1", "5.3") as one token
     instead of splitting at the dot.** This was **not** one of the two
     options the PM checkpoint named (raise the lab-lab bar / switch
     seed-only to max-over-members) -- it's logged here as a scope
     addition made because analysis showed the two named options *alone*
     cannot fix the worst case: the old regex split "5.5" into two "5"
     tokens that a `frozenset` collapses into the same single "5" token
     "GPT-5" already produces, so "Introducing GPT-5" and "Introducing
     GPT-5.5" tokenized to *identical* sets -- Jaccard 1.0, un-fixable by
     any merge-bar threshold (a threshold can only ever reject a bar
     lower than 1.0). Fixing the tokenizer to keep "5.5" distinct from
     "5" brings that pair down to Jaccard 0.5 -- excluded by the new 0.65
     lab-lab bar. Verified against the real fixture data (see
     `tests/test_models.py`); does not change any Phase 1 test's already-
     documented expected Jaccard value (none of the existing fixtures use
     dotted version numbers).
  3. **Why "seed-only vs. max-over-members" was *not* the chosen fix, and
     was not implemented:** analysis showed switching the comparison from
     "candidate vs. cluster's seed only" to "candidate vs. the max
     similarity over all existing members" can only make merging *more*
     permissive, never less -- the seed is itself always one of "all
     members," so anything that already matched under seed-only still
     matches under max-over-members, plus now anything that matches any
     *other* member too. It therefore cannot be a fix for an over-merging
     defect on its own, and was explicitly not implemented; this
     reasoning itself is the logged decision the PM checkpoint asked for.
- **2026-07-09 — Ranking weight/threshold constants' provenance, logged
  per the approved plan's own §8 commitment ("constants are logged"),
  which a prior pass omitted:**
  - `PRIMARY_SOURCE_WEIGHTS = {"lab": 3.0, "arxiv": 2.0, "hn": 1.0}`
    (`watcher/ranking.py`'s scoring formula, `watcher/config.py`) --
    values taken verbatim from the approved build plan's ranking formula;
    not independently chosen by this project, transcribed as specified.
  - `HN_VELOCITY_SCORE_FLOOR = 0.05` (`watcher/config.py`, used by
    `watcher/ranking.py::hn_velocity_score` for a cluster with no HN
    item) -- taken verbatim from the approved plan's ranking formula
    ("else `HN_VELOCITY_SCORE_FLOOR`"); the plan states the constant's
    *name* and role, and this is the exact value specified.
  - `JACCARD_SIMILARITY_THRESHOLD = 0.35` (`watcher/clustering.py`) --
    taken verbatim from the approved plan's clustering bullet ("Jaccard
    similarity >= 0.35 over ... title tokens"), already logged in
    `watcher/clustering.py`'s own docstring/comments but not previously
    given its own entry in this file; recorded here for completeness per
    plan §8.
  - `HN_POINTS_THRESHOLD = 50`, `HN_VELOCITY_THRESHOLD_PTS_PER_HOUR = 5.0`,
    `HN_LOOKBACK_HOURS = 48` (`watcher/config.py`, `watcher/sources/hn.py`'s
    candidacy filter) -- taken verbatim from the approved plan's HN
    source bullet (points>=50 OR velocity>=5.0 pts/hr, 48h lookback);
    likewise already implicit in `watcher/sources/hn.py`'s own comments
    but not previously given a standalone entry here.
  - All five values above are plan-stated, not spec-silent judgment
    calls this project invented -- they are logged here purely for
    discoverability/completeness per plan §8's own commitment, not
    because their derivation was ambiguous.
- **2026-07-09 — arXiv `robots.txt` blanket-disallow: carried forward to
  the Phase 2 checkpoint as an explicit owner decision, per the PM
  checkpoint's own instruction, and deliberately *not* resolved here.**
  The finding (`export.arxiv.org/robots.txt` disallows every path;
  `watcher/sources/arxiv.py` correctly honors it and returns `[]` against
  the live network) was already logged in this file before this
  checkpoint round; this entry only records that the PM's round-1
  directives explicitly instructed against resolving it unilaterally
  mid-build (accept arXiv as a dead source vs. amend CLAUDE.md's
  fetch-discipline rule to distinguish a documented, ToU-governed API
  from website crawling are both live options) -- no code or policy
  change was made for this item in this round, by design.

## Phase 1 PM checkpoint, round 2 (2026-07-09)

- **2026-07-09 — arXiv `robots.txt` question resolved: decision is
  `amend_fetch_discipline_for_documented_apis`, not
  `accept_dead_source`.** Resolves the item carried forward from round 1
  (the two entries directly above: the original "IMPORTANT DISCOVERY" and
  round 1's "carried forward ... deliberately not resolved here"). Four
  reasons, in full:
  1. **The project charter itself mandates this source.** CLAUDE.md's
     source table (spec sec-3) lists "arXiv API -- cs.AI, cs.CL, cs.LG" as
     a tier-1 Primary (research) source with "Free API, no key" access,
     and the ranking formula assigns arXiv a plan-specified primary-source
     weight of 2.0 (`PRIMARY_SOURCE_WEIGHTS["arxiv"]`). Accepting a
     permanently-dead arXiv source would silently delete an entire tier of
     the approved architecture and distort the specified ranking formula --
     HN+lab corroboration covers news, but nothing else supplies the
     research-paper leg the daily wire is chartered to carry.
  2. **The two rules were never in genuine conflict -- the generic rule
     was simply misfiring.** `robots.txt` (the Robots Exclusion Protocol)
     is a directive to web crawlers about indexing/crawling pages. arXiv
     separately publishes documented Terms of Use for its API
     (arxiv.org/help/api/tou) that explicitly invite and govern exactly
     this kind of programmatic access (rate limits, single connection,
     attribution). API hosts commonly blanket-disallow `robots.txt`
     precisely to keep generic search crawlers from indexing raw API
     responses, not to prohibit the documented API usage the host exists
     to serve -- honoring the disallow here misread a crawl directive as
     an access-control policy that contradicts arXiv's actual published
     policy. "Never circumvent" is about not scraping what a publisher
     doesn't want scraped; arXiv affirmatively wants this access, on terms
     this fetcher already satisfies (one combined request per
     once/twice-daily run, descriptive UA, vastly under the arXiv ToU's
     1-request-per-3-seconds limit).
  3. **The carve-out is narrow enough to preserve the rule's protective
     intent.** It names the arXiv API endpoint specifically
     (`export.arxiv.org`, via a central `ROBOTS_EXEMPT_API_HOSTS`
     allowlist in `watcher/config.py`, not a per-call boolean), requires
     published ToU compliance as the governing contract, requires any
     future source invoking the exception to be explicitly added to
     CLAUDE.md and logged here, and leaves `robots.txt` in full,
     never-circumvented force for all HTML/website fetching -- Anthropic's
     news scrape, DeepSeek's sitemap-diff, outlet pages, and any HTML page
     on arxiv.org itself.
  4. **Governance is proper.** Fetch discipline sits in CLAUDE.md's
     Sources section, not among the seven numbered HARD RULES; the
     backlog explicitly reserved this as an owner/PM-checkpoint decision
     with amendment named as a live option (see the two entries directly
     above), so deciding it at this checkpoint is exactly the escalation
     path the project defined, not a unilateral mid-build
     reinterpretation.

  **Implementation**: `CLAUDE.md`'s fetch-discipline paragraph
  ("Sources & selection algorithm") gained the narrow exception, worded to
  name arXiv specifically and state it never applies to HTML/website
  fetching. `watcher/config.py` adds
  `ROBOTS_EXEMPT_API_HOSTS = frozenset({"export.arxiv.org"})`.
  `watcher/http.py::check_robots_allowed` short-circuits to `True` (with
  an info-level log citing the CLAUDE.md exception) when a URL's host is
  in that set, *before* ever fetching that host's `robots.txt` -- every
  other host's gate is unchanged. `watcher/sources/arxiv.py`'s module and
  `fetch_arxiv_items` docstrings are updated to state the operative
  constraint is now arXiv's own ToU rate limit rather than `robots.txt`;
  the `check_robots_allowed` call itself is left in place unchanged (the
  exemption short-circuits centrally, not via a special case in this
  fetcher). `tests/test_arxiv_fetch.py`'s disallow-skip test is
  repurposed to assert the fetcher now proceeds and returns parsed items
  against the exact real disallow body previously captured live from
  `export.arxiv.org/robots.txt`, confirming `robots.txt` itself is never
  even fetched. `tests/test_fetch_discipline.py` gains three new tests:
  the exemption applies without a `robots.txt` fetch for the allowlisted
  host, the identical disallow body still returns `False` for a
  non-allowlisted host (the exemption is host-scoped, not a blanket
  special case), and the allowlist is pinned to exactly
  `{"export.arxiv.org"}` today. All lab-fetcher disallow-skip tests
  (`tests/test_lab_fetch_rss.py`, `tests/test_lab_fetch_html_diff.py`) are
  untouched -- they verify the rule that remains in force for every
  non-exempt host.
  **HN Algolia is deliberately left unchanged**: `hn.algolia.com/robots.txt`
  currently 404s (allow-all, already logged above), so there is no
  conflict to resolve for that source, and `hn.algolia.com` is *not*
  preemptively added to `ROBOTS_EXEMPT_API_HOSTS` -- CLAUDE.md's "today
  exactly one" wording plus this file's logging requirement means any
  future extension is a separate, explicit, logged decision, not an
  automatic consequence of this one.
  **Re-verified live**: `python scripts/run_watcher_live.py --runs 2`
  now reports `arxiv=50` on both runs (previously `arxiv=0`), with the
  idempotency bar still holding (run 2 adds zero new ledger keys); full
  output and schema-revalidation recorded in `PROGRESS.md`'s round 2
  checkpoint entry.
- **2026-07-09 — `.github/workflows/watch.yml` cron corrected from the
  Phase 1 placeholder `17 6 * * *` (06:17 UTC) to `0 23 * * *` (23:00
  UTC), to actually match CLAUDE.md's documented "daily 07:00 HKT"
  target.** Hong Kong is UTC+8 with no DST, so 07:00 HKT = 23:00 UTC the
  previous day; a daily-at-23:00-UTC cron therefore fires at 07:00 HKT
  the following day, every day -- the intended cadence, exactly. The
  original placeholder (logged earlier in this file, in the Decisions
  section) was chosen only to dodge GitHub Actions' top-of-hour
  scheduling congestion and was never meant to be the final hour; this
  entry is the promised tightening, done at this checkpoint rather than
  left open indefinitely.

## Phase 2, commit 14: path-allowlist + schema-validation CI gate scripts (2026-07-09)

- **Both `scripts/check_path_allowlist.py` and `scripts/validate_changed_schemas.py` invoke `git diff --name-only --no-renames HEAD`, not plain `--name-only`.** Plain `--name-only` can collapse a rename to a single "new path only" line (governed by the user's/repo's rename-detection settings), which would let a file moved *out* of `content/`/`data/` silently escape the allowlist gate; `--no-renames` reports a rename as a plain delete-of-old-path + add-of-new-path pair instead, so both halves are independently checked. Simplest fix that makes rename handling deterministic and not dependent on ambient git config.
- **`scripts/validate_changed_schemas.py`'s path→schema mapping adds `data/whats_moving.json -> whats_moving.schema.json`**, one entry beyond this turn's given mapping table. `data/whats_moving.json` is a real, already-committed Phase 1 data artifact with its own pre-existing schema; omitting it would leave a real gap in this CI gate (`watch.yml` commits this file daily). `content/primer.json` and `data/audit/latest.json` are deliberately *not* added — no schema exists for either yet (Phase 3/5 build them), so mapping to a nonexistent schema file would break the gate instead of protecting it.
- **A changed `.json` file that maps to a known schema but no longer exists on disk (deleted, or the vacated half of a `--no-renames`-reported rename) is silently skipped, not reported as a failure** — there is nothing to validate once a file is gone; the path-allowlist gate (not this one) is what still checks a deleted path's location.

## Phase 2, commit 15: run-planner / degradation ladder (`scripts/plan_run.py`) (2026-07-09)

- **Level-2 "every-other-day" parity direction: odd day-of-year is the "run" (capped top-5) parity, even is "skip."** Neither the spec nor CLAUDE.md fixes which parity runs; odd was chosen so day 1 of any year is always a run day. Logged rather than left as an unexplained arbitrary branch.
- **Level-2 parity is deliberately `today.timetuple().tm_yday % 2` (day-of-year), not `today.toordinal() % 2` (a continuous ordinal-date parity), per this turn's explicit instruction.** This has a known, accepted consequence at a year boundary: in a 365-day (odd-length) year, December 31 (day-of-year 365, odd) and the following January 1 (day-of-year 1, odd) share the same parity, so the ladder runs two days in a row there instead of strictly alternating. Covered explicitly by `tests/test_degradation_ladder.py::test_level_2_day_of_year_parity_across_a_year_boundary` rather than silently left unverified.
- **Level-3 digest weekday is Monday (`DIGEST_WEEKDAY = 0`, `date.weekday()`'s own convention)**, taken directly from the spec's own "(e.g. Monday)" example rather than inventing a different day.
- **A cluster's representative title (fed into `kebab_slug` for `proposed_card_id`) is `sources[0]["title"]`** — the first member in the cluster's own deterministic order (`watcher/queue_writer.py`'s existing ordering), since `queue.schema.json` has no single cluster-level title field of its own.
- **`kebab_slug` caps slugs at 60 characters** (stripping any trailing hyphen left by truncation) so a long real headline can't produce an unreasonably long `content/cards/<id>.json` filename; simplest reasonable bound, not spec-mandated.
- **`read_degradation_level()` defaults an unset/blank/unparseable `QUOTA_DEGRADATION_LEVEL` to 0 and clamps an out-of-range integer into `[0, 3]`** (both paths logged via `logging.warning`), rather than raising — a malformed owner-set repo variable should degrade to the safe, normal-mode default, not crash the run planner outright.
- **`scripts/plan_run.py` follows `watcher/velocity.py`'s existing `compute_*(..., *, now: datetime)` shape** (never calling `datetime.now()`/`date.today()` internally) rather than accepting a bare `today: date` directly — `today` is derived once, inside `compute_run_plan`, as `now.date()`. This keeps `generated_at` and the level-2/level-3 date-dependent decisions both driven off one single caller-supplied instant, fully unit-testable with a fixed `now`, matching this project's established "no live clock calls inside pure-code logic" convention.

## Phase 2: CLAUDE.md corroboration procedure, reputable-outlet table, verifier procedure, corrections workflow, degradation-ladder mechanism (2026-07-09)

- **Reputable-outlet table (14 entries): Reuters, Bloomberg, WSJ, FT, NYT, The Information, TechCrunch, The Verge, Ars Technica, Wired, MIT Technology Review, Axios, Nikkei Asia, SCMP.** The spec and the approved build plan both name this as a to-be-decided list ("a reasonable set... TechCrunch, The Verge, Ars Technica, Reuters, Bloomberg, The Information, Wired, MIT Technology Review, etc."); no exhaustive list exists upstream. Chosen as a short, well-known, English-language tech/business desk roster, deliberately including two Asia-focused outlets (Nikkei Asia, SCMP) not in the task's own example list — added specifically because the labs roster (`watcher/config.py`) includes five Chinese entities (DeepSeek, Alibaba Qwen, Moonshot, Zhipu, ByteDance Seed) and Hard Rule 4 (neutrality) requires the same evidentiary bar for their coverage as for US labs; an outlet table skewed entirely US/UK would make CONFIRMED structurally harder to reach for China-region stories purely as an artifact of which outlets happen to cover that beat. Candidates considered and left off for now: Business Insider (inconsistent editorial standards), Semafor and CNBC (solid but redundant with outlets already listed), The Economist (excellent but paywall makes "fetch and read the text" unreliable for an unattended pipeline). This list is explicitly owner-amendable — see CLAUDE.md's own note that any addition/removal is a logged, deliberate decision, not an analyst-discretion call.
- **Follow-up-story linking convention: a fixed prose pattern, `(follow-up to "<prior headline>", card <prior_id>)`, appended as one sentence inside `what_happened`/`why_it_matters`, rather than a new schema field.** The approved build plan names "follow-up linking" as one step of the analyst's corroboration procedure but doesn't specify a mechanism, and `card.schema.json` is closed (`additionalProperties: false`) with no relation field (no `follow_up_of`/`related_cards`). Adding one would be a code/schema change, out of scope for a CLAUDE.md-only update. The prose-pattern convention needs no schema change today, stays inside the existing free-text `what_happened`/`why_it_matters` fields, and is fixed/greppable enough that a future site enhancement could promote it to a real hyperlink using `content/cards/index.json`'s existing `id → date/headline` lookup. Flagged here as a real gap: a future Phase 2/4 commit should consider adding a proper `related_cards: string[]` (or similar) field to `card.schema.json` so this becomes a structural link rather than a documentation convention the analyst has to remember to follow.
- **`data/pending_corrections.json` pending-entry rejection (evidence does not confirm an error) has no distinct schema state — the entry is simply removed from `pending[]`.** `pending_corrections.schema.json` already has no "rejected" status field (only `source: audit|manual` on intake). Documenting the drain-and-remove behavior in CLAUDE.md's corrections-workflow section surfaced that a rejected candidate currently leaves no trace at all once removed. Logged as a possible future enhancement (e.g. an `audit.yml`-visible rejected-corrections log) rather than changed now, since it's outside this update's code-only scope.

## Phase 2: card-index / verifier-stats / reconcile helpers (2026-07-09)

- **A "dropped" card is handled with zero special-case code in `scripts/update_card_index.py`.** The index is always rebuilt from scratch by globbing whichever `content/cards/*.json` files currently exist on disk (`iter_card_paths`) — a card whose file was deleted is simply absent from that glob result on the next regeneration. There is no separate "mark as dropped" API and no diff-against-previous-index logic; the filesystem's current state is the single source of truth every time.
- **`build_card_index` raises (via `watcher.schema_validate.validate`) on a malformed on-disk card rather than silently skipping it.** By the time a file exists at `content/cards/<id>.json`, it should already have been schema-validated once (by whatever wrote it); a card that fails `card.schema.json` at index-build time is a real, loud bug worth surfacing immediately, not a reason to quietly omit it from the index.
- **Card index sort order: `date` descending, then `id` descending as a same-day tie-break** — `card_index.schema.json`'s own description says only "most-recent-first by convention (not schema-enforced)"; a deterministic tie-break was needed for same-day cards and `id` (which embeds the date + a slug + a hash fragment, see `scripts/plan_run.py::compute_proposed_card_id`) was the simplest already-available deterministic key.
- **`scripts/reconcile_run.py`'s finalization logic keys off whether `content/cards/<proposed_card_id>.json` exists on disk, not off any separate "verifier decision" signal.** The approved plan gives no persisted channel for "why the verifier dropped this cluster" beyond the card either existing or not; a cluster in `data/run_plan.json`'s `clusters[]` with no corresponding card file is treated as dropped, full stop. `DROPPED_REASON_NO_CARD` is therefore one fixed, generic string rather than a real per-cluster reason — logged as a known limitation; a future phase could thread a genuine reason through `data/run_plan.json` or a sibling file if the auditor ever needs finer granularity.
- **A `run_plan.json` cluster_hash with no existing `data/ledger.json` entry at all is defensively skipped (logged via `logging.warning`), not raised, by `reconcile_ledger`.** Shouldn't happen in the real pipeline (every queued cluster is upserted into the ledger by `watch.yml` before `analyze.yml` ever runs), and there's no `first_seen`/`member_urls` on hand to synthesize a schema-valid entry from scratch if it did.
- **Added `rolling_pass_rate(runs, *, window_days, as_of)` to `scripts/reconcile_run.py`**, beyond this turn's literally-scoped "append one row" requirement — a small, pure, pooled-across-the-window helper over `verifier_stats.json`'s own `runs[]` shape. Added because this turn's own test-file description explicitly calls for "rolling pass-rate computation on synthetic history" in `tests/test_verifier_stats.py`, and this pre-stages exactly the data shape Phase 5's weekly `audit.yml` "verifier pass-rate trend (rolling 7d/30d)" check will need (CLAUDE.md's own daily-loop diagram already names that future check) — the auditor itself remains out of this turn's scope; only this one reusable pure function was added, kept in the same module since it operates solely on this module's own artifact shape rather than duplicating that shape's field names in a not-yet-built module. Returns `None` (not `0.0`) for a window with zero `cards_drafted` across every run in range, so an all-quiet period reads as "no data" rather than misleadingly "everything failed."
- **`scripts/reconcile_run.py::reconcile_run` derives the card-index write path as `<cards_dir>/index.json`, never the module-level `CARD_INDEX_PATH` default**, specifically so that passing a non-default `cards_dir` (as every test does, against a `tmp_path`) can never silently fall through to writing the real repo's `content/cards/index.json`. Caught during this turn's own verification pass: an earlier draft called `write_card_index(cards_dir)` with the index path left at its default, which wrote a real (test-only, bogus) `content/cards/index.json` into the actual repo tree during a test run. Fixed before finishing, and the stray file was deleted; logged here as a caught-and-fixed defect rather than left silent, since it's exactly the kind of path-default footgun this project's own path-allowlist gate (`scripts/check_path_allowlist.py`) exists to catch downstream — better to not produce it in the first place.

## Integration pass: four parallel Phase 2 units merged in one working tree (2026-07-09)

- **`scripts/` needed no `scripts/__init__.py`.** `scripts.plan_run`/`scripts.update_card_index`/`scripts.reconcile_run` are imported both as `scripts.<name>` (by sibling scripts and their tests) and run standalone (`python scripts/<name>.py`, each inserting the repo root onto `sys.path` itself); Python 3's implicit namespace-package support resolves `scripts` as a package either way once the repo root is on `sys.path`, so no marker file was needed — confirmed by running the full suite and every script's CLI entrypoint directly, both green with no `__init__.py` present.
- **This integration pass ran each new script's `main()` directly (not just pytest) to smoke-test them against the real repo tree, which had a real side effect worth flagging: `update_card_index.py`/`plan_run.py` wrote a real (bogus, empty-cards-directory) `content/cards/index.json` and `data/run_plan.json` into the actual working tree.** Both were deleted immediately after the smoke test, before staging anything, and are not part of any commit — this integration pass's own version of the exact footgun `scripts/reconcile_run.py`'s own commit-14 entry above already logged and fixed at the function-default level. Confirms that footgun is real and worth the fix already made; no code change needed here, just cleanup.
- **`git diff --unified=0 IMPROVEMENT_BACKLOG.md` showed all four parallel units' new backlog entries landed as one single contiguous append hunk** (this file only ever grows at its tail), not four independently-stageable hunks — there was no clean per-unit split available via `git add -p` without hand-splicing the file's tail across separate temporary edits and re-committing. Given the task's four commits are grouped by *code* ownership (path-allowlist+schema-gate / run-planner / card-index+reconcile / CLAUDE.md) and this file's own diff is doc/log content throughout, the simplest reasonable resolution — rather than fragile manual hunk surgery across already-made commits — is to land the entire `IMPROVEMENT_BACKLOG.md` diff (all four units' log entries, plus this entry) together with the `CLAUDE.md` doc commit, the fourth and last of the four.

## Phase 2, commit: `analyze.yml` (ANALYST + VERIFIER pipeline) + `watch.yml` dispatch hookup (2026-07-09)

- **`anthropics/claude-code-action@v1`'s real interface was confirmed live against its own `action.yml`/`docs/usage.md`/`docs/custom-automations.md`/`examples/manual-code-analysis.yml` at write time (not assumed from memory), but its prompt-mode git/commit/PR-auto-creation behavior could not be confirmed either way.** Confirmed present: `prompt`, `claude_args` (a newline-separated raw-CLI-flags string; `--max-turns`, `--model`, `--allowedTools` all appear verbatim in the action's own docs examples, camelCase `allowedTools` confirmed in two independent doc pages), and `claude_code_oauth_token` as a real top-level input alternative to `anthropic_api_key`. The `examples/manual-code-analysis.yml` example (a `workflow_dispatch` + bare `prompt`, no PR/issue context, closest analog to this repo's use) requests only `permissions: contents: read` and performs no commit — it never demonstrates auto-commit/auto-PR behavior one way or the other, and `docs/custom-automations.md` separately (and inconsistently, given that very example exists) describes `workflow_dispatch` support as "coming soon." No doc page anywhere states whether a bare `prompt`+`workflow_dispatch` invocation (no assignee/label/mention trigger, no PR context) auto-commits on its own. **`analyze.yml` therefore assumes it does NOT** — an explicit `git -c user.name=... commit` step at the end of the job handles committing `content/`/`data/` changes, exactly mirroring `watch.yml`'s own already-established, already-tested pattern. If a future version of the action turns out to auto-commit in this mode, the explicit commit step becomes a no-op (`git diff --cached --quiet` would find nothing staged) rather than a conflict, so this assumption fails safe either way.
- **The VERIFIER's `allowedTools` includes `Bash(rm:*)` in addition to `git status`/`git diff`, one narrow, deliberate exception to this turn's literal "Bash restricted to git diff/status only" instruction.** CLAUDE.md's own verifier procedure (already-committed, prior phase) requires the verifier to be able to "drop" a hollowed-out card entirely by removing its `content/cards/<id>.json` file, but the verifier is granted no `Write` tool and there is no dedicated file-delete tool in Claude Code's tool set — without some `rm` capability, "drop hollowed-out cards" would be unimplementable by the verifier itself. `Bash(rm:*)` is the simplest reasonable resolution (a genuinely path-scoped delete permission, e.g. restricted to `content/cards/*.json` specifically, isn't expressible in Claude Code's prefix-based Bash permission syntax); the prompt text explicitly instructs the verifier to use it for nothing else, and any misuse is still caught by `scripts/check_path_allowlist.py` before the commit step regardless (a deleted path outside `content/`/`data/` still fails the gate, since `git diff --name-only` reports deletions too). A dedicated delete tool (or a real path-scoped Bash permission) would be a cleaner fix for a future turn.
- **`vars.CLAUDE_MODEL` (referenced via `--model ${{ vars.CLAUDE_MODEL }}` in both LLM steps' `claude_args`) is a new owner-set repo variable this turn introduces, with no fallback/default value in the workflow YAML.** GitHub Actions expressions do support a `||` fallback operator, but any fallback value would itself be a hardcoded model identifier — exactly what this turn's own instruction says never to do ("never hardcode a specific dated model snapshot"). The simplest reasonable choice is therefore no fallback at all: `vars.CLAUDE_MODEL` must be set once (e.g. `gh variable set CLAUDE_MODEL --body claude-sonnet-4-5`) before `analyze.yml` can run at all, exactly the same one-time prerequisite already documented for `vars.QUOTA_DEGRADATION_LEVEL` and the `CLAUDE_CODE_OAUTH_TOKEN` secret. Logged in `PROGRESS.md`'s "what remains for the human" list.
- **`watch.yml`'s new final step counts `data/queue.json`'s clusters with a one-line `python3 -c` script rather than adding a new `scripts/*.py` helper or reaching for `jq`.** The count is a single `len(json.load(...))` over an already-schema-validated top-level array (`queue.schema.json`) — simple enough that a dedicated script would be pure ceremony, and `jq` (while present on `ubuntu-latest` runners) would be the only non-Python tool touching this repo's own data shape, breaking the project's stated all-Python discipline for no real benefit over a one-line inline script.
- **`analyze.yml`'s commit step stages `content/` and `data/` as two whole directories (`git add content/ data/`), not an enumerated file list** (unlike `watch.yml`'s three explicitly-named files) — because Phase 2's analyst/verifier can touch a variable, run-dependent set of paths under those two roots (new cards, corrections, lexicon, board, ledger, run plan, verifier stats, card index), unlike `watch.yml`'s fixed three-file watcher output. `.gitignore`d paths (`data/.cache/`) are unaffected since `git add <dir>` already respects `.gitignore`, and `scripts/check_path_allowlist.py` has already run and passed by this point in the job, so staging the entirety of both directories is exactly as safe as staging the pre-vetted diff.

## Phase 2: pending-corrections intake helper + corrections-workflow consistency test (2026-07-09)

- **`scripts/pending_corrections.py` ships no `main()`/CLI entrypoint**, unlike every other `scripts/*.py` module in this repo — it is a pure library the ANALYST LLM step never calls (it drains `data/pending_corrections.json` directly via its own `Edit`/`Write` tools per CLAUDE.md's prose procedure, not by shelling out to this script), and nothing in any workflow invokes it as a step today. Adding a CLI would be speculative surface area for a caller that doesn't exist yet; a future programmatic intake source (e.g. an `audit.yml` finding-writer) can wire one on when it actually needs `append_pending_correction`.
- **`append_pending_correction` raises `ValueError` on a duplicate `id`** rather than silently overwriting or de-duplicating — an id collision most likely means a caller bug (reusing an id, or double-appending the same finding), and CLAUDE.md's own analogous `proposed_card_id` discipline (`scripts/plan_run.py`) treats id unicity the same way, as something the caller must get right rather than something this layer papers over.
- **`drain_pending_correction` is an idempotent no-op when the target `id` is already absent**, rather than raising `KeyError` — matches this pipeline's general preference (`watcher/ledger.py`'s own upsert) for idempotent re-application over erroring on an already-settled state, since a retry of the same drain (e.g. after a partial failure elsewhere) should not itself become a new failure.
- **`append_pending_correction`/`drain_pending_correction` do not validate their own return value against `schemas/pending_corrections.schema.json`** — validation happens at `load_pending_corrections`/`save_pending_corrections` time only, mirroring `scripts/reconcile_run.py`'s existing `append_verifier_stats_row`/`save_verifier_stats` division of responsibility, so a caller composing several in-memory edits before one final save isn't paying for redundant validation passes.
- **`tests/test_corrections_workflow.py`'s synthetic fixtures are hand-built dicts, not loaded from `fixtures/schema_examples/`** — the existing `pending_corrections` fixture pair there covers that schema alone; this test's job is specifically the *pairing* of a `card.schema.json` "corrected" card with its `corrections.schema.json` entry, which no existing fixture pair represents together, so a small dedicated pair of builders (`_corrected_card`/`_corrections_entry`, matching `tests/test_card_index.py`'s own `_card()` builder convention) was the simplest reasonable choice over adding new shared fixture files for a one-file need.

## Phase 2 checkpoint: full-suite pytest confirmation entry (2026-07-09)

- **`PROGRESS.md`'s new checkpoint entry draws a line between "pure-code, unit-tested today" and "LLM judgment, only provable by a live `analyze.yml` run," rather than counting schema/reconciliation tests as if they verified the corroboration or verifier judgment itself.** Spec-silent framing choice, not a code change: the simplest honest option was to name, for each of the three P2 acceptance criteria, exactly which already-committed test(s) exercise the pure-code bookkeeping around an outcome vs. what remains untestable without a real analyst/verifier run — rather than either overclaiming coverage or discarding the useful distinction. Also noted plainly that `improve.yml` does not exist yet in this repo (it is Phase 5 scope per the approved plan, not Phase 2), so this checkpoint doesn't claim any verification status for a file that hasn't been written.

## Phase 2 PM checkpoint, round 1 (2026-07-09)

- **Citation-field naming mismatch, spec sec-4 vs. sec-5 — resolved and now logged, per plan §8's commitment.** Spec sec-4's daily-loop diagram gives the analyst step's citation shape as `citations[] = {url, supporting_quote ≤15w}`; spec sec-5 (the card's own authoritative field list) gives a fuller, differently-named shape: `citations:[{url, outlet, quote}]`. The two sections disagree on both the quote field's name (`supporting_quote` vs. `quote`) and on cardinality (sec-4's sketch omits `outlet` entirely). This is a distinct mismatch from the earlier-logged "Card schema `citations[]` cardinality" entry above (that one reconciles sec-0's "≥2 sources" elevator-pitch prose against sec-5's own no-minimum wording, resolved as `minItems: 1`) — this entry is the still-outstanding sec-4-vs-sec-5 field-shape item plan §8 separately commits to logging. **Resolved by adopting sec-5's fuller shape verbatim, everywhere, favoring the schema-authoritative section over the diagram's abbreviated sketch:** `schemas/card.schema.json` already states in its own description that its field list is "spec sec-5 verbatim," and its `citations[]` items require exactly `{url, outlet, quote}` (`quote` capped at ≤15 words per that same description — the word cap itself is identical between the two spec sections; only the field's name and the presence of `outlet` differ). `CLAUDE.md`'s own rendering of the sec-4 loop diagram (the "ANALYST" line, and the identical wording in the VERIFIER-procedure section below it) was written using this same sec-5 shape (`citations[] = {url, outlet, quote ≤15w}`) from this repo's very first commit — confirmed via `git log -p -- CLAUDE.md`: no revision of this file, at any point, ever wrote `supporting_quote`. `outlet` is the substantive reason sec-5's shape governs: CLAUDE.md's numbered corroboration decision procedure and its 14-outlet reputable-outlet table are structurally load-bearing on knowing *which outlet* a citation came from (PRIMARY vs. OUTLET classification, the same-outlet/wire-copy counting rule) — a field sec-4's leaner `{url, supporting_quote}` shape has nowhere to carry at all. Read this way, sec-4's diagram is an abbreviated sketch of the full field list sec-5 actually specifies, not a competing design the project had to choose between. No code or schema change accompanies this entry: `card.schema.json` already matches sec-5 exactly, and a repo-wide grep confirms `supporting_quote` appears nowhere in the repo, so the normalization described here was already complete in practice — only the decision-log entry documenting it (this one) was missing, which is what this checkpoint round adds.
- **Backlog candidate (not yet implemented, non-blocking): a small pure-code test parsing `.github/workflows/analyze.yml` directly**, converting today's manual-YAML-inspection facts about the VERIFIER step into a real regression test that survives future workflow edits. Proposed assertions: (a) the VERIFIER step's `--allowedTools` string contains no bare `Write` grant; (b) its `Bash(...)` grants are limited to exactly the documented `git diff`/`git status` pair plus the one logged `Bash(rm:*)` exception (no other `Bash(...)` prefix ever appears); (c) the path-allowlist gate step (running `scripts/check_path_allowlist.py`) and the schema-validation gate step (running `scripts/validate_changed_schemas.py`) both appear strictly before the final commit step in the job's `steps:` list. Today all three facts are true only by direct human inspection of the committed YAML (see `PROGRESS.md`'s P2-acceptance-criteria section, criterion 2) — there is no automated check today that would catch a future edit silently reintroducing a `Write` grant, widening the `Bash(rm:*)` exception into something broader, or reordering a gate step past the commit step. Logged here as a candidate for a future commit rather than implemented in this checkpoint round, per this round's own instruction that it is non-blocking.
