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
