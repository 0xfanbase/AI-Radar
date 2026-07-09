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
