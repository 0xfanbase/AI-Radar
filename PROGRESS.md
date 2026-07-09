# PROGRESS.md

Reverse-chronological build log for AI Frontier Wire. Newest entry on top.
Each entry corresponds to one commit or one phase checkpoint. See
`CLAUDE.md` for the standing rules and architecture, and
`IMPROVEMENT_BACKLOG.md` for every spec-silent decision made along the way.

---

## 2026-07-09 — Phase 2 checkpoint: full-suite pytest confirmation + honest analyze.yml/improve.yml verification status

This is a wrap-up checkpoint on top of the Phase 2 work already recorded
below (this entry adds no new production code) — it re-runs the complete
test suite one more time end-to-end and states plainly what is and isn't
verified about the two LLM-driven workflows.

**What Phase 2 built, in one place** (each already has its own detailed
entry further down this file and in `IMPROVEMENT_BACKLOG.md`):
schema extensions (`schemas/ledger.schema.json`'s `verifier_outcome` shape
+ dropped/`card_id`-null invariant; four new schemas —
`card_index`/`run_plan`/`verifier_stats`/`pending_corrections`); the
one-time `content/`↔`data/` relocation (`frontier_board.json`/
`lexicon.json` moved into `content/`, `content/corrections.json` seeded);
the CI gate scripts (`scripts/check_path_allowlist.py`,
`scripts/validate_changed_schemas.py`); the degradation ladder
(`scripts/plan_run.py`, the `QUOTA_DEGRADATION_LEVEL` mechanism); the
card-index/verifier-stats/reconcile helpers
(`scripts/update_card_index.py`, `scripts/reconcile_run.py`,
`scripts/pending_corrections.py`); `CLAUDE.md`'s full corroboration/
verifier/corrections-workflow procedure and reputable-outlet table; and
`.github/workflows/analyze.yml` itself (ANALYST + VERIFIER two-step
pipeline, gated by the two CI scripts above, wired to `watch.yml`'s new
dispatch step).

**Full-suite re-run performed this checkpoint**: `python -m pytest` —
**452 passed, 2 deselected** (the 2 deselected are the `@pytest.mark.live`
acceptance-proof tests, excluded by default per `pytest.ini`'s
`addopts = -m "not live"`, exactly as designed). This is every test file
under `tests/` — everything carried over from Phase 1 (fetchers,
clustering, ranking, ledger, queue writer, velocity, fetch discipline)
plus every Phase 2 addition (`test_p2_schemas.py`,
`test_ledger_status_extension.py`, `test_check_path_allowlist.py`,
`test_validate_changed_schemas.py`, `test_degradation_ladder.py`,
`test_card_index.py`, `test_verifier_stats.py`,
`test_corrections_workflow.py`, `test_pending_corrections.py`), collected
and run together in one invocation. No failures; nothing needed fixing.
No source files changed in this checkpoint — only this file.

**Stated plainly, as the build plan itself commits to doing (§ Verification,
item 3): `analyze.yml` cannot be end-to-end verified in this session, and
that is not something this session can fake or work around.** It has
never actually executed on GitHub Actions. Running it for real requires,
at minimum, the `CLAUDE_CODE_OAUTH_TOKEN` repo secret — and **no tool
available in this session can create a GitHub repo secret** (that action
isn't exposed by any tool this agent has access to, by design — secrets
are deliberately not something an automated session should be able to
provision for itself). It also requires `vars.CLAUDE_MODEL` to be set
(no fallback is baked into the workflow, deliberately — see the entry
below and `IMPROVEMENT_BACKLOG.md`). This remains a manual follow-up step
for the repo owner, exactly as flagged in every earlier checkpoint entry
in this file; it is restated here, explicitly, so it isn't lost among the
pure-code verification work above. **`improve.yml` doesn't exist in this
repo yet at all** — it's Phase 5 scope per the approved build plan
(§6), not part of Phase 2 — so there is nothing to even attempt to
verify for it today; flagged here only because when it is built, it will
depend on the identical `CLAUDE_CODE_OAUTH_TOKEN` secret for the identical
reason, so it will carry the identical unverified-until-the-owner-acts
status the day it lands.

**P2 acceptance criteria, and exactly how much of each is provably true
today vs. deferred to a live run:**

1. **"A single-source rumor publishes as REPORTED, or doesn't publish at
   all."** The judgment call itself — deciding a given cluster's claim
   is rumor-strength, discarding it at the claim-quality gate, or landing
   it at `reported` rather than `confirmed` — is prose executed by the
   ANALYST LLM step in `analyze.yml`, reasoning over live-fetched pages;
   no unit test can simulate that judgment, because doing so would just
   be re-implementing the judgment in Python, which is exactly what the
   design deliberately does *not* do (see CLAUDE.md's numbered
   corroboration procedure). **What is pure-code and fully unit-tested
   today** is the bookkeeping that has to hold correctly no matter what
   the LLM decides: `schemas/ledger.schema.json`'s `if`/`then` constraint
   that a `status: "dropped"` entry's `card_id` must be `null`
   (`tests/test_p2_schemas.py::test_ledger_dropped_valid_fixture_passes`
   / `test_ledger_dropped_with_nonnull_card_id_fails`);
   `scripts/reconcile_run.py::reconcile_ledger`'s actual finalization
   logic — a cluster with no resulting `content/cards/<id>.json` on disk
   is marked dropped with a permanently-null `card_id`, one that does
   have a file is marked published
   (`tests/test_ledger_status_extension.py::test_reconcile_ledger_drops_a_cluster_with_no_resulting_card`,
   `test_reconcile_ledger_publishes_a_cluster_with_a_resulting_card`,
   `test_dropped_cluster_hash_card_id_stays_null_across_repeated_reconciliation`);
   and `card.schema.json`'s `status` enum
   (`confirmed|reported|corrected`, deliberately no `dropped` value at
   the card level, since a dropped cluster never gets a card written at
   all). In short: today's tests prove the plumbing correctly records
   whatever the analyst decides; whether the analyst's live judgment on
   a real rumor is *correct* is untested and untestable outside a live
   run.
2. **"A fabricated benchmark number in a draft gets stripped by the
   verifier."** Same structure: the sentence-by-sentence support check
   and the strip-vs-drop decision (CLAUDE.md's adversarial re-check
   procedure) is prose executed by the fresh-context VERIFIER LLM step,
   re-fetching citations live and reading them — nothing in this repo
   simulates "is this number actually fabricated," nor should it.
   **What is pure-code and verifiable today** is the structural machinery
   that makes stripping possible and safe: `analyze.yml`'s VERIFIER step
   is declared with no `Write` tool in its `allowedTools` (only
   `Read,Glob,Grep,WebFetch,Edit,Bash(git diff:*),Bash(git
   status:*),Bash(rm:*)` — confirmed by direct inspection of the
   committed YAML; there is no dedicated automated test parsing the
   workflow YAML today, so this is a manual-inspection fact, stated as
   such rather than folded into the "452 passed" count); `card.schema.json`
   has no minimum body length beyond `minLength: 1`, so a body the
   verifier has cut down remains schema-valid
   (exercised incidentally by every card fixture test in
   `tests/test_schemas.py`/`tests/test_p2_schemas.py`); and the
   demotion/drop outcome bookkeeping
   (`verifier_outcome.demoted_from_confirmed`, `dropped_reason`) is
   schema-shaped and tested exactly as in point 1 above
   (`tests/test_p2_schemas.py::test_ledger_verifier_outcome_demoted_from_confirmed_only`).
   Today's tests prove the data model can faithfully represent "demoted,"
   "stripped," and "dropped" outcomes; they cannot and do not prove a real
   fabricated number gets caught.
3. **"A workflow diff makes CI fail."** This is the one criterion that
   needs no LLM judgment at all — it's pure code, and it is fully,
   directly unit-tested today, with no live-run caveat.
   `scripts/check_path_allowlist.py`'s `is_allowed`/`find_violations` are
   asserted directly against fixture diffs including
   `.github/workflows/analyze.yml` itself, `watcher/*.py`, `schemas/*`,
   and `CLAUDE.md`, all correctly flagged as violations
   (`tests/test_check_path_allowlist.py`), plus a `main()`-level exit-code/
   stderr test and one smoke test against this repo's own real `git diff`
   history. `scripts/validate_changed_schemas.py`'s
   `schema_name_for_path`/`validate_changed_files` are asserted against
   every mapped `content/`/`data/` path (valid and invalid fixtures both),
   confirming unmapped/non-JSON paths are skipped and deleted paths never
   false-fail (`tests/test_validate_changed_schemas.py`). Both scripts run
   before the commit step in `analyze.yml` with no
   `continue-on-error` anywhere in that job, so a real workflow/code diff
   genuinely blocks the auto-commit in production exactly as these tests
   already prove in isolation — this is the one of the three criteria
   where the committed pytest suite *is* the full acceptance proof, start
   to finish, independent of any live LLM run.

---

## 2026-07-09 — Phase 2: `analyze.yml` (ANALYST + VERIFIER pipeline) + `watch.yml` dispatch hookup

Per the approved build plan's §3, this checkpoint wires the two
already-built pure-code halves of Phase 2 (`scripts/plan_run.py`,
`scripts/reconcile_run.py`, `scripts/check_path_allowlist.py`,
`scripts/validate_changed_schemas.py`) together with the two LLM steps
into a real `.github/workflows/analyze.yml`, and hooks `watch.yml` up to
dispatch it.

**`anthropics/claude-code-action@v1`'s interface was looked up live**
against the action's own `action.yml`, `docs/usage.md`,
`docs/custom-automations.md`, and `examples/manual-code-analysis.yml` —
not assumed from training memory. Confirmed real: `prompt`, `claude_args`
(newline-separated raw CLI flags — `--max-turns`, `--model`,
`--allowedTools` all appear verbatim in the action's own docs, camelCase
`allowedTools` confirmed independently in two doc pages), and
`claude_code_oauth_token` as a real top-level input.

**Could not confirm from the action's own docs whether prompt-mode
(`workflow_dispatch` + a bare `prompt`, no PR/issue/mention context)
auto-commits or auto-opens a PR on its own** — the closest real example
(`examples/manual-code-analysis.yml`) requests only `contents: read` and
never commits, and `docs/custom-automations.md` inconsistently describes
`workflow_dispatch` support as "coming soon" despite that very example
existing. Stated plainly rather than guessed: **`analyze.yml` assumes the
action does NOT auto-commit** — an explicit `git -c user.name=...
commit`/`push` step at the end of the job handles it, mirroring
`watch.yml`'s own already-tested pattern exactly. Logged in
`IMPROVEMENT_BACKLOG.md`.

**`analyze.yml`** (`workflow_dispatch` only — not a second cron):
(a) `scripts/plan_run.py` writes `data/run_plan.json`; (b) an ANALYST
`claude-code-action@v1` step (own 35-turn budget, model from
`vars.CLAUDE_MODEL`, `allowedTools` restricted to
`Read,Glob,Grep,WebFetch,Edit,Write,Bash(git diff:*),Bash(git status:*)`)
drains `data/pending_corrections.json` first, then drafts
`content/cards/<proposed_card_id>.json` per `data/run_plan.json` cluster,
growing `content/lexicon.json`/`content/frontier_board.json`; (c) a
VERIFIER step (fresh context, its own separate 35-turn budget, no `Write`
tool — `allowedTools` = `Read,Glob,Grep,WebFetch,Edit,Bash(git
diff:*),Bash(git status:*),Bash(rm:*)`, the last one a narrow logged
exception so it can physically delete a dropped card's file) re-fetches
every citation independently and strips/drops, never upgrades; (d)
`scripts/reconcile_run.py` (pure code) regenerates the ledger/card-index/
verifier-stats; (e) `scripts/check_path_allowlist.py` +
`scripts/validate_changed_schemas.py` gate the diff — either failing
blocks the commit step entirely (default GitHub Actions step-failure
behavior, no `continue-on-error` anywhere in this workflow); (f) commit +
push `content/`/`data/` under the bot identity, only if there's something
to commit.

**`watch.yml` amended**: `permissions.actions: write` added, plus a final
step that counts `data/queue.json`'s clusters (`python3 -c
"import json; print(len(json.load(open('data/queue.json'))))"`) and, only
if non-empty, runs `gh workflow run analyze.yml --ref main` using
`secrets.GITHUB_TOKEN`.

**Verification performed this checkpoint:**
- Both workflow files parse as valid YAML (`yaml.safe_load`), and every
  embedded multi-line `run:` shell block passes `bash -n`.
- `python -m pytest` — 430 passed, 2 deselected (live tests excluded by
  default) — unchanged pass count from before this checkpoint, since this
  turn touches only the two workflow YAML files, nothing under `scripts/`,
  `watcher/`, or `tests/`.
- **Not verified, and cannot be from this session**: `analyze.yml` has
  never actually run on GitHub Actions. It needs the
  `CLAUDE_CODE_OAUTH_TOKEN` repo secret and the `vars.CLAUDE_MODEL` /
  `vars.QUOTA_DEGRADATION_LEVEL` repo variables, none of which any session
  can create — this is stated plainly as a real gap, not claimed as tested.
  Once those exist and this branch is merged, the first real
  `watch.yml` → `analyze.yml` cycle is the actual end-to-end proof.

**What remains for the human (added to since Phase 1):** add the
`CLAUDE_CODE_OAUTH_TOKEN` repo secret; set `vars.CLAUDE_MODEL` (e.g.
`gh variable set CLAUDE_MODEL --body claude-sonnet-4-5`) — no fallback is
baked into the workflow YAML, deliberately, since any fallback value would
itself be a hardcoded model snapshot; optionally set
`vars.QUOTA_DEGRADATION_LEVEL` (defaults to 0/normal if left unset); enable
GitHub Pages; review and merge this branch to `main`; then observe
hands-off operation to confirm the loop actually runs end to end.

---

## 2026-07-09 — Phase 2, commit 12: ledger schema extension + card_index/run_plan/verifier_stats/pending_corrections schemas

Per the approved build plan's §3 (Phase 2 — Analyst + Verifier), this
commit lays the schema groundwork the rest of Phase 2 builds on, before any
analyst/verifier/planner code lands.

**`schemas/ledger.schema.json` extended, additively:**
`verifier_outcome` (already an optional, free-form field since Phase 1) now
has a defined shape — `{last_attempted_at, dropped_reason?,
demoted_from_confirmed?}` — and a new `if`/`then` enforces that a
`status: "dropped"` entry's `card_id` stays `null`. The pre-existing
`status` enum (`queued|published|dropped`) is unchanged (see
`IMPROVEMENT_BACKLOG.md` for why a differently-worded `active|dropped`
enum was considered and rejected — it would have invalidated every real
entry). Both changes are additive/tightening only: the real, committed
`data/ledger.json` (104 entries, all `status: "queued"`, none using
`verifier_outcome`) still validates unchanged against the extended schema
— checked directly in `tests/test_p2_schemas.py`.

**Four new schemas added** (`schemas/card_index.schema.json`,
`schemas/run_plan.schema.json`, `schemas/verifier_stats.schema.json`,
`schemas/pending_corrections.schema.json`), shapes per the plan's §3 field
lists, spec-silent field-naming choices logged in
`IMPROVEMENT_BACKLOG.md`. `watcher/schema_validate.py` needed no change —
it has no schema-name mapping table, only a direct
`schemas/<name>.schema.json` path convention the four new names already
fit.

**Seeded** `data/verifier_stats.json = {"version":1,"runs":[]}` and
`data/pending_corrections.json = {"version":1,"pending":[]}`. No
`data/run_plan.json` seed was created, per this turn's explicit scope —
`scripts/plan_run.py` (a later Phase 2 commit) produces real ones.

**Tests**: new `tests/test_p2_schemas.py` covers, for each of the four new
schemas, self-validation (Draft 2020-12), a valid fixture passing, and an
invalid fixture failing; plus ledger-extension-specific cases (a dropped
entry with structured `verifier_outcome` validates; a dropped entry with a
non-null `card_id` fails the new conditional; `verifier_outcome` missing
its required `last_attempted_at` fails; `verifier_outcome` absent entirely
still validates; and the real committed `data/ledger.json` validates
unchanged). New fixtures under `fixtures/schema_examples/{valid,invalid}/`
for `card_index`, `run_plan`, `verifier_stats`, `pending_corrections`, and
a dedicated `ledger_dropped.json` valid/invalid pair (kept separate from
the pre-existing `ledger.json` fixture pair, which `tests/test_schemas.py`
already covers and which this commit leaves untouched).

Verification: `python -m pytest` — 246 passed, 2 deselected (live tests
excluded by default; 23 net new tests added this commit in
`tests/test_p2_schemas.py`).

---

## 2026-07-09 — Phase 1 PM checkpoint round 2: arXiv fetch-discipline exception resolved, cron corrected to true 07:00 HKT

Three carried-forward PM checkpoint items resolved this round:

**1. arXiv `robots.txt` question — decided: `amend_fetch_discipline_for_documented_apis`.**
The owner/PM decision (full four-reason rationale in
`IMPROVEMENT_BACKLOG.md`'s "Phase 1 PM checkpoint, round 2" section):
accepting arXiv as a permanently-dead source would silently delete an
entire tier of the approved architecture (CLAUDE.md's own source table
lists arXiv as a tier-1 Primary/research source, and the ranking formula
gives it a plan-specified weight of 2.0) and the two rules were never in
genuine conflict — `robots.txt` is a crawl directive for page-indexing
crawlers, while arXiv separately publishes API Terms of Use
(arxiv.org/help/api/tou) that affirmatively invite exactly this
programmatic access. `CLAUDE.md`'s "Sources & selection algorithm"
fetch-discipline paragraph now carries a narrow, explicitly-named
exception: a provider's own documented public REST API with published
ToU governing programmatic access is accessed under those API terms,
not gated by `robots.txt` — today exactly one such host,
`export.arxiv.org`. `robots.txt` remains in full, never-circumvented
force for every other fetcher and for any HTML/website fetching,
including any HTML page on arxiv.org itself; any future extension of
this exception must be named in CLAUDE.md and logged here.

Mechanism (a central, auditable allowlist, not a per-call boolean):
`watcher/config.py` adds `ROBOTS_EXEMPT_API_HOSTS =
frozenset({"export.arxiv.org"})`; `watcher/http.py::check_robots_allowed`
short-circuits to `True` (with an info-level log noting the documented-
API exemption) when a URL's host is in that set, before ever fetching
that host's `robots.txt` — every other host's fetch is unaffected.
`watcher/sources/arxiv.py`'s module and `fetch_arxiv_items` docstrings
now state the operative constraint is arXiv's own published rate limit
(no more than 1 request per 3 seconds from a single connection) — easily
satisfied, since this fetcher makes exactly one combined OR-query per
once/twice-daily run. HN Algolia is deliberately left untouched
(`hn.algolia.com/robots.txt` already 404s/allow-all today, so there is
no conflict to resolve, and the exception's "today exactly one" wording
means any future addition is a separate, explicit, logged decision).

`tests/test_arxiv_fetch.py`'s disallow-skip test is repurposed to assert
the fetcher now proceeds and returns parsed items against the exact real
disallow body captured live from `export.arxiv.org/robots.txt`, and
confirms `robots.txt` is never even fetched (the exemption short-circuits
before that call). `tests/test_fetch_discipline.py` gains three new
`http.py`-level tests: the exemption applies without a `robots.txt` fetch
for the allowlisted host, the exact same disallow body still returns
`False` for a non-allowlisted host (proving the exemption is host-scoped,
not a blanket rule), and the allowlist itself is pinned to exactly
`{"export.arxiv.org"}` today. All existing lab-fetcher disallow-skip
tests (`tests/test_lab_fetch_rss.py`, `tests/test_lab_fetch_html_diff.py`)
are untouched — they verify the general rule, which still fully applies.

**Re-ran the Phase 1 live acceptance criterion after this fix**: `python
scripts/run_watcher_live.py --runs 2`, against the real live endpoints.
Verbatim captured stdout:

```
--- Run 1 ---
  Sources fetched:  hn=20  arxiv=50  lab=21
  Clusters formed:  90
  Queue size:       8
  Ledger entries:   54 -> 104  (+50 new)
--- Run 2 ---
  Sources fetched:  hn=20  arxiv=50  lab=21
  Clusters formed:  90
  Queue size:       8
  Ledger entries:   104 -> 104  (+0 new)
  New items per source (vs. previous run): hn=+0 arxiv=+0 lab=+0
--- Summary across all runs ---
  Ledger entries:  54 -> 104  (+50 total new)
  Queue size (final run): 8
```

**arXiv now returns 50 items on both runs** (previously 0) — the fetcher
proceeds past the robots.txt gate under the new exemption and the real
Atom API responds normally. The idempotency bar still holds: run 2 adds
**zero new ledger keys** (104 → 104), the same acceptance criterion as
round 1, now proven with arXiv actually contributing to the candidate
pool instead of silently returning `[]`. Cluster membership changed as
expected (54 → 90 clusters, since arXiv items now enter the pool for the
first time) and ledger entries grew from 54 → 104 (+50, all newly-seen
clusters that include at least one arXiv paper, or arXiv papers/HN
stories that cluster on their own) — a one-time jump from arXiv joining
the pool, not a idempotency violation. `data/ledger.json`,
`data/queue.json`, and `data/whats_moving.json` were regenerated by this
real run and re-validated against their schemas
(`schemas/ledger.schema.json`, `schemas/queue.schema.json`,
`schemas/whats_moving.schema.json`) via `watcher.schema_validate.validate`
immediately after, with no errors — same verification discipline as
round 1's checkpoint. The full (uncapped) cluster pool's largest cluster
size is still 2 (the same OpenAI/HN pair from round 1's checkpoint);
arXiv's 50 papers did not chain into any multi-member cluster this run.

**2. `.github/workflows/watch.yml` cron corrected to the actual documented
target.** CLAUDE.md's diagram names "daily 07:00 HKT" as the target run
time. Hong Kong is UTC+8 with no DST, so 07:00 HKT = 23:00 UTC the
*previous* day. The Phase 1 placeholder (`17 6 * * *`, 06:17 UTC — an
off-hour minute chosen only to dodge GitHub Actions' top-of-hour
congestion, logged in `IMPROVEMENT_BACKLOG.md`) never actually matched
that target hour. Changed to `0 23 * * *`: fires once daily at 23:00 UTC,
which is 07:00 HKT the following day — the documented target, exactly.

**3. Corrected an inaccurate claim in round 1's own checkpoint entry above**
("every other cluster is a single item") — a direct post-run inspection
missed a second real 2-item cluster (a same-day DeepSeek lab-lab pair
merged under the 0.65 lab-lab Jaccard bar) alongside the OpenAI/HN pair
already described; the wording above is corrected in place rather than
left standing as written.

Verification: `python -m pytest` — 223 passed, 2 deselected (live tests
excluded by default; 3 net new tests added this round in
`tests/test_fetch_discipline.py`, plus the repurposed arXiv test).

---

## 2026-07-09 — Phase 1 PM checkpoint round 1: queue-sanity fix + re-verified live acceptance proof

The Phase 1 PM checkpoint review found a real acceptance-criterion defect
in the live proof captured below (superseded by this entry): the
committed queue's rank-1 entry was a 17-member mega-cluster merging 16
unrelated OpenAI announcements spanning ~2.5 years ("Introducing the GPT
Store" (Jan 2024) through every "Introducing GPT-5.x" up to "Introducing
GPT-Live"), chained purely by Jaccard similarity on shared "Introducing
GPT-..." title boilerplate, with `cross_source_count=17` inflating it to
the top score slot on every future run. Root causes and fixes, in full
detail in `IMPROVEMENT_BACKLOG.md`'s "Phase 1 PM checkpoint, round 1"
section:

1. **`LAB_RECENCY_WINDOW_DAYS = 14`** (`watcher/config.py`, applied in
   `watcher/sources/labs/registry.py::fetch_all_lab_items`) — drops any
   lab Item with a parseable `published_at` older than 14 days before
   clustering ever sees it, so OpenAI's archive-serving RSS feed (1033
   `<item>`s, back to 2023) can no longer flood the candidate pool.
   DeepSeek's undated Items (`published_at=""`) are never dropped by this
   filter — its sitemap-diff already gates newness structurally.
2. **`LAB_LAB_JACCARD_SIMILARITY_THRESHOLD = 0.65`** (`watcher/config.py`,
   applied in `watcher/clustering.py`) — a stricter merge bar than the
   general 0.35 specifically when *both* compared items are
   `source_type == "lab"`, since short/templated lab-announcement titles
   can still boilerplate-chain even within one 14-day window.
3. **`watcher/models.py::tokenize_title` bug fix** — the tokenizer split
   a dotted point-release number like "5.5" into two "5" tokens that a
   `frozenset` collapsed into the same single token "GPT-5" already
   produces, making "Introducing GPT-5" and "Introducing GPT-5.5"
   tokenize identically (Jaccard 1.0, un-fixable by any merge-bar
   threshold). Fixed to keep dotted version numbers as one token, so
   distinct point releases stay distinct. This one is a scope addition
   beyond the two options the PM checkpoint named (raise the lab-lab bar
   / seed-only vs. max-over-members) — logged as such, with the reasoning
   for why "max-over-members" was analyzed and *not* chosen (it can only
   make merging more permissive, never less, so it cannot fix an
   over-merging defect on its own).

Because windowing changes which items ever reach clustering, cluster
membership (and therefore every `cluster_hash`) changes too — the
previously-committed 931-entry `data/ledger.json` no longer corresponds to
this pipeline's actual output, so it was regenerated from scratch (reset
to `{"version": 1, "entries": {}}`, then rebuilt by the live re-run below)
rather than left to accumulate stale entries alongside new ones.

**Re-ran the Phase 1 live acceptance criterion after the fix**: `python
scripts/run_watcher_live.py --runs 2`, invoked from a shell with real
outbound network access, against the actual live endpoints — HN Algolia's
search API, arXiv's Atom API, and all four registered lab sources
(`openai.com/news/rss.xml`, `deepmind.google/blog/rss.xml`,
`anthropic.com/news` HTML anchor-scrape, DeepSeek's `sitemap.xml` diff).
Nothing here is mocked, simulated, or paraphrased — the block below is the
actual captured stdout of that run, verbatim (this replaces the stale,
pre-fix proof this section previously showed).

```
robots.txt disallows https://export.arxiv.org/api/query?search_query=cat%3Acs.AI+OR+cat%3Acs.CL+OR+cat%3Acs.LG&sortBy=submittedDate&sortOrder=descending&start=0&max_results=50 for UA 'AIFrontierWireBot/1.0 (+https://github.com/0xfanbase/AI-Radar; bot@users.noreply.github.com)' -- skipping source for this run.
robots.txt disallows the arXiv API query -- skipping arXiv source for this run.
--- Run 1 ---
  Sources fetched:  hn=20  arxiv=0  lab=36
  Clusters formed:  54
  Queue size:       8
  Ledger entries:   0 -> 54  (+54 new)
robots.txt disallows https://export.arxiv.org/api/query?search_query=cat%3Acs.AI+OR+cat%3Acs.CL+OR+cat%3Acs.LG&sortBy=submittedDate&sortOrder=descending&start=0&max_results=50 for UA 'AIFrontierWireBot/1.0 (+https://github.com/0xfanbase/AI-Radar; bot@users.noreply.github.com)' -- skipping source for this run.
robots.txt disallows the arXiv API query -- skipping arXiv source for this run.
--- Run 2 ---
  Sources fetched:  hn=20  arxiv=0  lab=21
  Clusters formed:  40
  Queue size:       8
  Ledger entries:   54 -> 54  (+0 new)
  New items per source (vs. previous run): hn=+0 arxiv=+0 lab=+0
--- Summary across all runs ---
  Ledger entries:  0 -> 54  (+54 total new)
  Queue size (final run): 8
```

**Reading these numbers correctly:**

- **arXiv returned 0 both runs**, for the same already-logged reason as
  before: `export.arxiv.org`'s own `robots.txt` currently disallows every
  path for every user agent, and the fetcher correctly honors it. This
  question is carried forward to the Phase 2 checkpoint as an explicit
  owner decision per the PM checkpoint's own instruction (accept arXiv as
  a dead source vs. amend CLAUDE.md's fetch-discipline rule to distinguish
  a documented, ToU-governed API from website crawling) — not resolved
  here.
- **`lab` count (36 → 21) is now two orders of magnitude smaller than the
  pre-fix run's 1158 → 1144**, because the 14-day recency window is doing
  its job: a direct per-fetcher call immediately after this run, windowed
  the same way, breaks down to `openai=13, deepmind=2, anthropic=6,
  deepseek=0` (21 total, matching run 2 exactly). The 15-item drop between
  run 1 (36) and run 2 (21) is entirely DeepSeek, for the same
  sitemap-diff reason logged in the pre-fix run above: run 1 had no prior
  `data/.cache/deepseek_sitemap_seen.json` state file (this session's
  cache was cleared before re-running, to capture a clean fresh-checkout
  proof), so every currently-listed DeepSeek `/news/` URL was "new" once;
  run 2 ran against the state file run 1 had just written and correctly
  found 0 new URLs. Every non-DeepSeek lab source's fetch was reproducible
  between the two runs seconds apart.
- **The mega-cluster no longer forms.** A direct post-run check of the
  full (uncapped) cluster pool from this same live data found a largest
  cluster size of **2, occurring twice** — not the single 2-item exception
  an earlier draft of this entry claimed: OpenAI's own "Introducing
  GPT-Live" RSS item joined by a same-story HN submission
  (`cross_source_count=2`), **and** a second, same-day pair of DeepSeek
  lab items that merged under the stricter 0.65 lab-lab Jaccard bar
  (`cross_source_count=2`, both members `source_type="lab"`) — every
  other cluster in the pool is a single item. `data/queue.json`'s rank-1 entry is that
  2-member cluster (score 318.59, both sources a real, current, 2026-07-08
  OpenAI release corroborated by a live 651-point HN thread); ranks 2-8
  are all distinct, current, plausible HN stories (an LLM-burnout essay, a
  Mistral robotics-navigation release, a Fable/Anthropic classifier
  critique, a GitHub-agent security writeup, a Microsoft agent-viz tool, a
  model-comparison build-off, and a coding-agent benchmark post) — every
  top-8 entry is a real, followable, current story, not a chained
  boilerplate artifact.
- **Cluster count differs between runs (54 vs 40) only because the input
  pool differs by the same 15 DeepSeek items described above** (each
  clustered on run 1 but not re-fetched at all on run 2, since the
  sitemap-diff correctly suppressed already-seen URLs) — not because
  clustering is non-deterministic.

**The acceptance bar is "run 2 adds zero new ledger keys," not
"byte-identical files."** `data/ledger.json` went `0 → 54` on run 1 and
`54 → 54 (+0 new)` on run 2 — exactly the required outcome, on the freshly
regenerated ledger. Existing ledger entries do get their `last_seen`
timestamp/date bumped on a re-run that still sees a cluster's member URLs
(that's `watcher/ledger.py`'s `apply_run` doing its job — a story is
still live, not stale), and that is not a violation of idempotency; the
only thing that would be a violation is a *new key* (a new `cluster_hash`)
appearing for a cluster whose member-URL set was already present in the
ledger before the run — and that did not happen. `data/queue.json` and
`data/whats_moving.json` were also both regenerated by this real run and
are committed as-is; all three were re-validated against their schemas
(`schemas/ledger.schema.json`, `schemas/queue.schema.json`,
`schemas/whats_moving.schema.json`) via `watcher.schema_validate.validate`
immediately after the run, with no errors.

**Known residual risk (not fully eliminated, flagged for the future
`audit.yml`):** the 0.65 lab-lab Jaccard bar is a threshold, not a
semantic fix — two genuinely different lab releases that happen to share
more boilerplate than that (e.g. two same-day variant announcements) could
still merge, and two genuine companion pieces that share less than that
(e.g. the real fixture pair "Inside GeneBench-Pro" / "Introducing
GeneBench-Pro", Jaccard 0.5) will no longer merge. This tradeoff is
logged, not silently accepted, in `IMPROVEMENT_BACKLOG.md`.

**Known risks (structural, not fixed by more code — flagged for the
future `audit.yml`'s link-rot/missed-story checks and for whoever next
touches these two fetchers):**

- **Anthropic anchor-scrape (`watcher/sources/labs/anthropic.py`) depends
  on `anthropic.com/news`'s current HTML structure** — specifically, that
  each story anchor contains a descendant element whose `class` attribute
  substring-matches `title` (see `html_common.py`'s
  `_extract_anchor_title`). Anthropic can restructure this page at any
  time with no notice (no versioned API, no RSS fallback for this lab);
  if they do, the fetcher's fallback (anchor's own text minus any
  `<time>` descendant) degrades to messier titles rather than failing
  loudly, which means a silent quality regression is more likely than a
  crash. There is no alerting on this today beyond the future auditor's
  general checks.
- **DeepSeek sitemap-diff (`watcher/sources/labs/deepseek.py`) depends on
  `api-docs.deepseek.com/sitemap.xml` continuing to exist, continuing to
  list `/news/<slug>` article URLs, and each article page continuing to
  have exactly one meaningful `<h1>`.** If DeepSeek migrates off Docusaurus,
  changes their docs URL, or drops the sitemap, this source silently
  degrades to `[]` (robots.txt-disallow-shaped failure mode, same as
  arXiv above) with only a log line — no test in this repo can catch a
  live upstream restructure ahead of time, only after-the-fact via the
  future audit's missed-story check. Also previously observed live (the
  pre-fix run captured in this same section's earlier revision):
  `api-docs.deepseek.com/robots.txt` itself timed out on one fetch attempt
  (`Read timed out. (read timeout=10)`), which the fetcher correctly
  treated as "skip this source for this run" rather than retrying past
  its 3-attempt budget or crashing — worth knowing this lab's
  infrastructure is occasionally slow/flaky in production, not just in
  theory, even though this round's re-run didn't happen to reproduce it.

**`.github/workflows/watch.yml` and `.github/workflows/ci.yml` added this
commit** — `watch.yml` runs the cron above (`17 6 * * *`, an off-hour
minute, plus `workflow_dispatch`), sets up Python 3.11, caches
`data/.cache`, installs `requirements.txt`, runs `python -m watcher.cli
run`, and commits+pushes `data/ledger.json`/`data/queue.json`/
`data/whats_moving.json` under the `frontier-wire-bot` identity only if
there's something to commit; `ci.yml` runs `python -m pytest` (via
`requirements-dev.txt`) on every push and pull request. Neither workflow
has been exercised on GitHub Actions itself yet (that requires this branch
to be merged/pushed and a run to actually fire) — logged here as the next
thing to observe once this lands, not claimed as already verified end to
end.

**Round 1 status:** the four PM checkpoint directives requiring code/doc
changes (queue-sanity windowing fix, mega-cluster verification + Jaccard
decision, re-run + regenerated `data/*.json`, missing constant-provenance
backlog entries) are addressed above and in `IMPROVEMENT_BACKLOG.md`. The
fifth directive (arXiv `robots.txt`) is deliberately left unresolved and
carried forward to the Phase 2 checkpoint, per the PM's own instruction.
Resubmitted for Phase 1 sign-off.

Verification: `python -m pytest` — 220 passed, 2 deselected (live tests
excluded by default), green after this round's fix (13 new tests added:
`tests/test_models.py`'s tokenizer coverage, plus new lab-lab Jaccard and
recency-window cases in `tests/test_clustering.py` /
`tests/test_lab_fetch_rss.py`).

---

## 2026-07-09 — Phase 1, commit 1: project scaffolding

Seeded the repo chassis: `CLAUDE.md` (purpose, daily-loop diagram, hard
editorial rules in full, source table + selection algorithm, prompt-injection
stance, quota degradation ladder, schema/test conventions), this
`PROGRESS.md`, `IMPROVEMENT_BACKLOG.md`, `README.md`, `.gitignore`,
`requirements.txt` / `requirements-dev.txt`, `pytest.ini`, the empty
`watcher/` package, and a `tests/` suite whose `conftest.py` structurally
blocks live network calls in the default `pytest` run (autouse fixture
monkeypatching `requests.sessions.Session.request` to raise unless a test
is marked `live`).

Verification: `pip install -r requirements-dev.txt` succeeded;
`python -m pytest` passed (2 tests, 0 failures, live tests excluded by
default per `pytest.ini`).

**Next up:** JSON schemas (`schemas/*.json`), the shared HTTP layer
(`watcher/http.py`), and the HN/arXiv/lab fetchers — per the Phase 1 commit
sequence in the approved build plan.

**What remains for the human, eventually (tracked here as the project
progresses, not yet applicable at this stage):** add the
`CLAUDE_CODE_OAUTH_TOKEN` repo secret before `analyze.yml`/`improve.yml`
can run end-to-end; enable GitHub Pages (Settings → Pages → Source: GitHub
Actions) before `deploy.yml` can publish; review and merge this branch to
`main`; then observe two weeks of hands-off operation to satisfy Phase 5's
own acceptance criterion.
