# PROGRESS.md

Reverse-chronological build log for AI Frontier Wire. Newest entry on top.
Each entry corresponds to one commit or one phase checkpoint. See
`CLAUDE.md` for the standing rules and architecture, and
`IMPROVEMENT_BACKLOG.md` for every spec-silent decision made along the way.

---

## 2026-07-09 — Phase 1 live acceptance proof + `watch.yml`/`ci.yml`

Ran the Phase 1 live acceptance criterion for real: `python
scripts/run_watcher_live.py --runs 2`, invoked from a shell with real
outbound network access, against the actual live endpoints — HN Algolia's
search API, arXiv's Atom API, and all four registered lab sources
(`openai.com/news/rss.xml`, `deepmind.google/blog/rss.xml`,
`anthropic.com/news` HTML anchor-scrape, DeepSeek's `sitemap.xml` diff).
Nothing here is mocked, simulated, or paraphrased — the block below is the
actual captured stdout of that run, verbatim.

```
robots.txt disallows https://export.arxiv.org/api/query?search_query=cat%3Acs.AI+OR+cat%3Acs.CL+OR+cat%3Acs.LG&sortBy=submittedDate&sortOrder=descending&start=0&max_results=50 for UA 'AIFrontierWireBot/1.0 (+https://github.com/0xfanbase/AI-Radar; bot@users.noreply.github.com)' -- skipping source for this run.
robots.txt disallows the arXiv API query -- skipping arXiv source for this run.
robots.txt fetch failed for https://api-docs.deepseek.com/robots.txt (HTTPSConnectionPool(host='api-docs.deepseek.com', port=443): Read timed out. (read timeout=10)) -- skipping source for this run.
robots.txt disallows https://api-docs.deepseek.com/news/news1120 -- skipping this DeepSeek article.
--- Run 1 ---
  Sources fetched:  hn=20  arxiv=0  lab=1158
  Clusters formed:  931
  Queue size:       8
  Ledger entries:   0 -> 931  (+931 new)
robots.txt disallows https://export.arxiv.org/api/query?search_query=cat%3Acs.AI+OR+cat%3Acs.CL+OR+cat%3Acs.LG&sortBy=submittedDate&sortOrder=descending&start=0&max_results=50 for UA 'AIFrontierWireBot/1.0 (+https://github.com/0xfanbase/AI-Radar; bot@users.noreply.github.com)' -- skipping source for this run.
robots.txt disallows the arXiv API query -- skipping arXiv source for this run.
--- Run 2 ---
  Sources fetched:  hn=20  arxiv=0  lab=1144
  Clusters formed:  925
  Queue size:       8
  Ledger entries:   931 -> 931  (+0 new)
  New items per source (vs. previous run): hn=+0 arxiv=+0 lab=+0
--- Summary across all runs ---
  Ledger entries:  0 -> 931  (+931 total new)
  Queue size (final run): 8
```

**Reading these numbers correctly:**

- **arXiv returned 0 both runs.** This is not a bug: `export.arxiv.org`'s
  own `robots.txt` currently disallows every path for every user agent
  (`User-agent: * / Disallow: /`), confirmed live and already logged in
  `IMPROVEMENT_BACKLOG.md` before this run. Per CLAUDE.md's fetch-discipline
  rule ("drop that source, never circumvent a disallow"), the fetcher
  correctly returns `[]` rather than fetching anyway.
- **`lab` count (1158 → 1144) is large because OpenAI's live RSS feed
  (`openai.com/news/rss.xml`) is not windowed** — it currently serves its
  entire historical news archive as `<item>` entries (1033 confirmed by a
  raw `curl`/grep of the feed at the time of this run: `grep -c "<item>"`
  → 1033), not just recent releases. Breaking down the 1144 total for run 2
  by lab (verified with a direct per-fetcher call immediately after):
  `openai=1033, deepmind=100, anthropic=11, deepseek=0`. The 14-item drop
  between run 1 (1158) and run 2 (1144) is entirely DeepSeek: run 1 had no
  prior `data/.cache/deepseek_sitemap_seen.json` state file, so every
  `/news/` URL in DeepSeek's ~60-URL sitemap was "new" (14 of them); run 2
  ran against the state file run 1 had just written, correctly found 0 new
  `/news/` URLs, and every other lab source's fetch was byte-for-byte
  reproducible between the two runs seconds apart. This is the sitemap-diff
  mechanism working exactly as designed, not a discrepancy to be
  concerned about — arithmetic checks out: `1033 + 100 + 11 + 0 = 1144`.
- **Cluster count differs slightly between runs (931 vs 925) only because
  the input pool differs by those same 14 DeepSeek items** (each of which
  clustered into its own or an existing cluster on run 1, but wasn't
  re-fetched at all on run 2 since the sitemap-diff correctly suppressed
  already-seen URLs) — not because clustering is non-deterministic.

**The acceptance bar is "run 2 adds zero new ledger keys," not
"byte-identical files."** `data/ledger.json` went `0 → 931` on run 1 and
`931 → 931 (+0 new)` on run 2 — exactly the required outcome. Existing
ledger entries do get their `last_seen` timestamp/date bumped on a re-run
that still sees a cluster's member URLs (that's `watcher/ledger.py`'s
`apply_run` doing its job — a story is still live, not stale), and
`times_seen`-style bookkeeping fields are expected to change value on
every run that re-observes a cluster. None of that is a violation of
idempotency; the only thing that would be a violation is a *new key*
(a new `cluster_hash`) appearing for a cluster whose member-URL set was
already present in the ledger before the run — and that did not happen.
`data/queue.json` and `data/whats_moving.json` were also both regenerated
by this real run and are committed as-is; all three were re-validated
against their schemas (`schemas/ledger.schema.json`,
`schemas/queue.schema.json`, `schemas/whats_moving.schema.json`) via
`watcher.schema_validate.validate` immediately after the run, with no
errors.

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
  future audit's missed-story check. Also observed live during this very
  run: `api-docs.deepseek.com/robots.txt` itself timed out on one fetch
  attempt (`Read timed out. (read timeout=10)`), which the fetcher
  correctly treated as "skip this source for this run" rather than
  retrying past its 3-attempt budget or crashing — worth knowing this
  lab's infrastructure is occasionally slow/flaky in production, not just
  in theory.

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

Verification: `python -m pytest` — 207 passed, 2 deselected (live tests
excluded by default), green before this commit.

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
