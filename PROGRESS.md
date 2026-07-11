# PROGRESS.md

Reverse-chronological build log for AI Frontier Wire. Newest entry on top.
Each entry corresponds to one commit or one phase checkpoint. See
`CLAUDE.md` for the standing rules and architecture, and
`IMPROVEMENT_BACKLOG.md` for every spec-silent decision made along the way.

---

## 2026-07-11 — Architecture change: daily analyst run moves from `analyze.yml`/GitHub secret to a Claude Code Remote Routine

The owner does not have, and does not want to manage, a
`CLAUDE_CODE_OAUTH_TOKEN` GitHub secret. `analyze.yml` (and the not-yet-built
`improve.yml`) needs exactly that secret to run its `anthropics/claude-code-action@v1`
steps. Rather than leave `watch.yml` dispatching a workflow that would
fail on every single invocation once merged — precisely the kind of
failed-run notification spam that prompted this conversation — the real
execution mechanism for both the daily analyst+verifier run and the
(upcoming) fortnightly improve loop is now a **Claude Code Remote
Routine**: a scheduled session, external to GitHub Actions entirely, that
reads the exact ANALYST/VERIFIER prompt text directly out of
`analyze.yml`, runs each as its own fresh subagent via the Agent tool
(preserving the verifier's fresh-context/no-shared-memory property just
as faithfully as the two-separate-GitHub-Actions-steps design did), runs
the same pure-code `scripts/plan_run.py` → `scripts/reconcile_run.py` →
`scripts/check_path_allowlist.py` → `scripts/validate_changed_schemas.py`
sequence, and commits+pushes under the bot identity — no GitHub secret
anywhere in this path. A Routine ("AI Frontier Wire — daily
analyst+verifier refresh," daily at 23:30 UTC, 30 minutes after
`watch.yml`'s own cron) is already created and enabled.

Concrete changes this entry covers:
- `watch.yml`: removed the "Dispatch analyze.yml if the queue is non-empty"
  step and the `actions: write` permission it alone required (the job's
  `contents: write` permission is unaffected and still needed for its own
  commit-and-push step). Replaced with a comment pointing at the Routine.
- `analyze.yml`: not deleted — annotated at the top of the file as
  reference/inactive documentation of the exact procedure the Routine
  runs, since duplicating that prompt text in two places would let them
  drift out of sync. No workflow-logic lines inside it were changed.
- `CLAUDE.md`: the "Daily self-learning loop" section gained a paragraph
  stating this mechanism plainly, immediately after the architecture
  diagram, so a reader doesn't take the diagram's `claude_code_oauth_token`
  mention at face value.
- `IMPROVEMENT_BACKLOG.md`: the decision itself logged in full.

**Verification:** `python -m pytest` — 675 passed, 2 deselected, unchanged
(no test referenced the removed dispatch step or permission). Both
modified workflow YAML files re-validated with `yaml.safe_load`.
`improve.yml` (Phase 5, not yet built) will follow the same pattern —
a second Routine, not a second secret — when it's written.

---

## 2026-07-11 — Correction: the Phase 4 PM checkpoint round-1 "no browser binary" claim was false; a real Lighthouse run and score already existed

The round-1 entry directly below states plainly, twice, that "no Chrome,
Chromium, or Firefox binary exists anywhere in this environment" and that
the Lighthouse accessibility score therefore could not be obtained. That
claim is **wrong**, and this entry corrects it and the record, rather than
leaving a false statement standing.

This environment has Playwright's own bundled Chromium pre-installed at
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`
(`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`), independently confirmed
working by directly invoking it: `chrome --version` returns "Chromium
141.0.7390.37." Earlier in this same Phase 4 build (the "Real A11y Check"
step, before the round-1 checkpoint below), an agent had already found
this binary, found `npx`/`node` at `/opt/node22/bin`, installed
`lighthouse@13.4.0` via `npx --yes`, served the real built `public/`
directory locally, and ran a genuine, non-simulated Lighthouse
accessibility audit against three real pages. The resulting JSON reports
were saved to this session's scratchpad and still exist; each was
independently re-parsed and re-verified as part of this correction (real
`fetchTime`s, real `localhost:8080` `finalUrl`s, `runtimeError: null`,
`runWarnings: []`):

| Page audited | Accessibility score | Failing audits |
|---|---|---|
| `/` (home) | **100 / 100** | 0 |
| `/board/` | **100 / 100** | 0 |
| `/lexicon/rag/` | **100 / 100** | 0 |

This is a real, tool-measured Lighthouse accessibility score, clearing the
build plan's `>=90` bar with room to spare on every page sampled — not an
estimate, not a structural-check substitute, and not fabricated after the
fact; it was obtained *before* the round-1 checkpoint incorrectly asserted
it was impossible. The round-1 checkpoint's own fix commit
(`1ddf3dc`) then spent its "honest documentation" effort recording a false
environment limitation instead of simply re-stating this real result — an
avoidable regression in the record's own accuracy, from a PM re-review
that should have looked for the prior agent's tool output before
concluding a capability didn't exist.

**What this changes, concretely:** the round-1 entry's "no browser
binary" claim (its point 1, and its restatement in the "what remains"
list) and the Phase 4 sign-off entry's matching claim (its "Stated
plainly" paragraph and matching "what remains" item) are corrected in
place, directly below, rather than left standing alongside this
correction. The contrast-ratio regression test added by round-1
(`site/tests/test_contrast_ratios.py`) is unaffected and remains valid
and useful on its own merits — this correction touches only the
Lighthouse-related claims. **The Lighthouse item is removed from "what
remains for the human"** — it's done, with a passing score, and doesn't
need to wait for GitHub Pages to be enabled, since the local `public/`
build that was actually audited is exactly what `deploy.yml` publishes
unchanged. Re-running Lighthouse once more against the real deployed URL
after Pages is enabled is a reasonable, cheap sanity check but is no
longer a blocking requirement.

---

## 2026-07-11 — Phase 4 PM checkpoint round 1: Lighthouse gap logged honestly, contrast-ratio regression test added

The Phase 4 sign-off PM review independently re-ran both test suites
(675 passed / 2 deselected root; 17 passed `site/tests`), ran the real
`python site/generate.py -v` against this repo's actual `content/`/`data/`
end to end, and independently recomputed every WCAG contrast ratio the
entry directly below claims — confirming the site itself, its templates,
tokens, and generator needed no changes at all (see that review's own
findings; nothing under `site/builders/`, `site/templates/`,
`site/static/css/`, or `site/generate.py` is touched by this checkpoint).
Two real gaps were found in the Phase 4 sign-off entry's own record-keeping
and test coverage, both closed by this entry:

**1. The Lighthouse gap — CORRECTED, see the entry directly above this
one.** This round's own review incorrectly concluded no Chrome/Chromium/
Firefox binary exists in this environment, and on that mistaken basis
asserted the plan's Lighthouse requirement could not be met and had been
silently omitted. Both the "no binary exists" claim and the "silently
omitted" framing were wrong: a real Lighthouse run, using this
environment's pre-installed Playwright Chromium, had already been
performed earlier in this same Phase 4 build, scoring **100/100 on all
three pages sampled (home, `/board/`, `/lexicon/rag/`), zero failing
audits**. See the correction entry above this one for the full
re-verified detail. The structural accessibility pass this checkpoint
refers to below (one `<h1>`/page, landmarks, skip-link ordering,
status-chip text, sparkline `aria-label`s, reduced-motion gating) remains
real and correct — it was simply never the *only* accessibility
verification performed for Phase 4, contrary to what this round claimed.

**2. The missing contrast-ratio regression test.** The build plan promised
an automated test reading tokens.css's hex values directly rather than
re-hardcoding them (tokens.css's own header comment already said as much:
"A future automated contrast-ratio test ... should read the hex values
below directly rather than re-hardcoding them"). Commit 27's own backlog
entry deferred writing it to a later Phase 4 commit, and no later commit
ever picked it up — `site/tests/test_build.py` only asserted the literal
string `--color-signal-cyan: #43E5C4` is present in the generated CSS,
which proves the token exists, not that it's contrast-safe. Fixed this
round: new `site/tests/test_contrast_ratios.py` parses every `--color-*`
custom property directly out of the real, on-disk
`site/static/css/tokens.css` at test-collection time (no hex value is
typed a second time anywhere in the new test file), computes WCAG 2.x
relative luminance and contrast ratios in Python, and asserts every
text-role token (`ink`, `signal-cyan`, `star-white`, `reported-amber`,
`corrected-red`) clears 4.5:1 against both background tokens (`bg`,
`panel`). A dedicated pair of tests locks in that `hairline` is never
treated as a text-role color by this test — both structurally (asserting
it's excluded from the text-role token set) and empirically (asserting
its own measured ratio against both backgrounds is genuinely below 4.5:1,
matching tokens.css's own "1.26:1" header-comment claim), so the exclusion
is enforced, not merely assumed. The computed ratios were independently
verified this round to match tokens.css's header-comment claims exactly
(signal-cyan 12.16/11.13, reported-amber 8.29/7.59, corrected-red
5.30/4.85, ink and star-white both >14:1 against either background) — no
palette value needed to change.

**Verification performed this round:**
- `python -m pytest` (root) — **675 passed, 2 deselected**, unchanged (no
  file under `tests/` was touched this round).
- `python -m pytest site/tests` — **22 passed** (up from 17: the 5 new
  tests in `site/tests/test_contrast_ratios.py`).
- `python site/generate.py -v` re-run against this repo's real
  `content/`/`data/` — same clean build as the entry below, unaffected by
  this round's test-only and documentation-only changes.

Per this checkpoint's own PM directive: with both items above landed and
pytest green, **Phase 5 (`audit.yml` + `improve.yml`) may now proceed.**
Enabling GitHub Pages, merging this branch to `main`, and the
base-path/custom-domain decision remain on the "what remains for the
human" list in the entry below — none of them are resolved by this round,
and none of them can be from this session. The Lighthouse item is
**removed** from that list per the correction entry above: it was already
done, with a real, passing (100/100) score, before this round incorrectly
re-added it as outstanding.

---

## 2026-07-09 — Phase 4: builders wired together, end-to-end site build verified, accessibility pass, deploy.yml

This is the Phase 4 **integration** checkpoint: four builder units that
had been written in parallel in this same working tree since the
scaffold commit below (`wire.py` from an earlier commit; `board.py`;
`lexicon.py` + `primer.py`; `moving.py` + `method.py` +
`corrections.py` + `about.py`, each already independently tested and
each deliberately *not* wired into `site/generate.py` yet, per every
one of their own docstrings) are now called from one place, producing
the real `public/` output the approved build plan's route table names.

**`site/generate.py::render_pages()` now calls every builder**, in this
order: Wire (home page + monthly archive, none exist yet since
`content/cards/` is still empty), Board, Lexicon (index + one page per
of the 30 real terms), Primer, What's Moving, Method, Corrections,
About, then a new `404.html`. `sitemap.xml` and `robots.txt` are written
directly by `generate.py` itself. Two small integration fixes were
needed, both logged in full in `IMPROVEMENT_BACKLOG.md`: (1) `board.py`
was the one builder missing a `write_board_page(env, ..., public_dir)`
function matching every sibling's convention — a two-line addition; (2)
the masthead "what's moving" sparkline strip, which the build plan calls
for site-wide, only rendered on `/moving/` itself until this pass — now
set once as a Jinja **environment global** on the one shared
`Environment` every builder is called with, so `base.html`'s existing
conditional `{% include %}` picks it up on every page without touching
any of the seven other builders' own context functions.

**The real generator was actually run** (not just tested) against this
repo's real `content/`/`data/` directories, twice: once into a scratch
directory for inspection, once into the repo's own real (gitignored)
`public/`. Both runs completed with no errors and produced all 39
expected files: the home page, `board/index.html` (all 13 real Board
rows, correctly grouped US=6/China=5/open-weights=2, all 13 pulse-dot
eligible since every row's real `last_verified` is today's date),
`lexicon/index.html` plus all 30 real term pages, `primer/index.html`
(all 10 real steps), `moving/index.html`, `method/index.html`,
`corrections/index.html` (the real, honest empty state — `[]` today),
`about/index.html`, `404.html`, `sitemap.xml`, `robots.txt`, plus the
copied `static/` assets. No `/wire/<YYYY-MM>/` archive page exists yet,
correctly, since `content/cards/` is still empty.

**Accessibility pass performed across the whole generated output**, not
just spot-checked: every one of the 39 generated HTML pages has exactly
one `<h1>`, exactly one `<main id="main-content">` landmark, and the
skip-link (`<a class="skip-link" href="#main-content">`) as the first
focusable element in `<body>` — verified both by direct inspection this
turn and by 9 new automated tests added to `site/tests/test_build.py`
(`test_every_generated_html_page_has_exactly_one_h1`,
`test_every_generated_html_page_has_one_main_content_landmark`,
`test_skip_link_is_first_focusable_element_on_every_page`,
`test_masthead_sparkline_strip_renders_site_wide`,
`test_every_named_route_in_the_build_plan_is_written`,
`test_every_real_lexicon_term_gets_its_own_page`,
`test_404_page_uses_the_shared_shell_and_is_a_real_not_found_page`,
`test_sitemap_xml_is_well_formed_and_lists_expected_routes`,
`test_robots_txt_allows_everything_and_references_sitemap`). The Board's
`@keyframes` pulse animation was independently re-confirmed to live only
inside `@media (prefers-reduced-motion: no-preference)` (already correct
from `board.py`'s own commit, re-checked here as part of this pass, not
changed).

**Stated plainly (corrected 2026-07-11 — see the correction entry near the
top of this file): the approved build plan's own section 5 additionally
calls for a manual Lighthouse accessibility run, recorded as a numeric
score in this file. That measurement WAS performed as part of this same
Phase 4 build's "Real A11y Check" step, using this environment's
pre-installed Playwright Chromium (`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`)
via `npx lighthouse` — real, not simulated, and independently re-verified
afterward: **100/100 on `/`, `/board/`, and `/lexicon/rag/`, zero failing
audits on any of the three.** (An intervening PM re-review round
incorrectly claimed no browser binary existed in this environment and
that this measurement was therefore impossible; that claim was itself
wrong and has been corrected — see the top-of-file entry for the full
re-verification.) Everything verified above (one `<h1>`/page, one
`<main id="main-content">`/page, skip-link ordering, status-chip text,
sparkline `aria-label`s, reduced-motion gating) is a separate, structural,
automated-test-backed check that complements the Lighthouse score rather
than substituting for it.

**`public/404.html`** is a real, fully-shelled not-found page (extends
`base.html`, one `<h1>Page not found</h1>`, links back to every major
section) — GitHub Pages automatically serves a repo-root `404.html` for
any unmatched path on a project site once Pages is enabled, no extra
configuration needed. **`public/sitemap.xml`** lists every real route as
an absolute URL under `https://0xfanbase.github.io/AI-Radar` (GitHub
Pages' own default project-site URL for this repo with no custom domain
— see `IMPROVEMENT_BACKLOG.md` for why that specific value was chosen and
its limits). **`public/robots.txt`** allows every crawler and points at
`sitemap.xml`.

**A pre-existing, already-logged gap is re-flagged, not fixed, by this
checkpoint**: every internal link/asset href this site renders
(`/board/`, `/static/css/tokens.css`, `/lexicon/<slug>/`, etc.) is
root-relative, on the assumption the built site is served from its
domain root. If GitHub Pages ends up serving this repo from its default
project subpath (`https://0xfanbase.github.io/AI-Radar/`) rather than a
custom domain mapped to the repo root, every one of those links needs a
base-path prefix that doesn't exist today. Fixing it would mean editing
href-generation code inside all four builder units this checkpoint only
wires together (each independently written and tested this session) —
out of this integration turn's own scope, and logged again, plainly, in
`IMPROVEMENT_BACKLOG.md` rather than silently patched around.

**`.github/workflows/deploy.yml`** (new): triggers on push to `main`
touching `content/**`, `data/**`, or `site/**`, plus
`workflow_dispatch`; `permissions: pages: write, id-token: write`; a
`build` job (checkout with `fetch-depth: 0`, matching this repo's
established convention; `setup-python`; installs both
`requirements-dev.txt` and `site/requirements.txt` — the former is what
actually provides `pytest` itself and the root test suite's runtime
deps, not named explicitly in the task's own wording but required for
"run pytest" to do anything at all; runs `python -m pytest`, then
`python -m pytest site/tests` [the site generator's own suite, not
collected by the root `pytest.ini`'s `testpaths = tests`, but exactly
the suite that verifies the thing this job is about to build and
publish]; runs `python site/generate.py -v`; uploads `public/` via
`actions/upload-pages-artifact`); a `deploy` job (`needs: build`,
`environment: github-pages`, `actions/deploy-pages`). **Stated plainly,
in the workflow file's own top comment and here: this cannot actually
deploy anything until the repo owner enables GitHub Pages in repo
settings (Settings → Pages → Source: "GitHub Actions") — no tool
available to any session in this project can toggle that setting
itself.** The `build` job's own tests + real site build can be validated
by any session regardless of whether Pages is enabled; the `deploy` job
will fail with an environment-not-configured error until that one
manual step happens.

**Verification performed this checkpoint:**
- `python -m pytest` (root) — **670 passed, 2 deselected**, unchanged
  from immediately before this checkpoint (no file under `tests/` was
  touched).
- `python -m pytest site/tests` — **15 passed** (up from 6: 9 new tests
  added this checkpoint, see above).
- The real `site/generate.py` was run directly, twice, against this
  repo's actual `content/*.json`/`data/*.json` (not fixtures) — see
  above for the full file-by-file confirmation.
- `.github/workflows/deploy.yml` parses as valid YAML (`yaml.safe_load`)
  and matches this repo's own already-established `on:`-parses-as-
  boolean-`True` quirk shared by every other workflow file here (a
  PyYAML 1.1 artifact of an unquoted `on:` key, not a new issue
  introduced by this file).

**What remains for the human (corrected 2026-07-11 — see the correction
entry near the top of this file; back down to four items, not five: the
Lighthouse run was already done, with a real 100/100 score, not an
outstanding item):** add the `CLAUDE_CODE_OAUTH_TOKEN` repo secret and set
`vars.CLAUDE_MODEL` before `analyze.yml` can ever run end-to-end; enable
GitHub Pages (Settings → Pages → Source: "GitHub Actions") before
`deploy.yml`'s own `deploy` job can succeed; review and merge this branch
to `main` (both `watch.yml`'s `analyze.yml` dispatch and `deploy.yml`'s
own push trigger target `main`, so nothing here fires automatically until
then); and decide whether this project will use a custom domain (a
`CNAME` file mapped to serve from the domain root) or accept the default
`github.io/AI-Radar/` project subpath, since every internal link this
site renders assumes root serving and does not yet handle the latter
case. None of these four are things any session can do from here.

---

## 2026-07-09 — Phase 4, commit 27: site generator scaffold (base template, design tokens)

First Phase 4 commit. Builds the static-site generator's skeleton per the
approved plan's stack decision (Python + Jinja2, no JS framework, no npm/
node anywhere) — the plumbing that later commits' page builders
(`wire`/`board`/`lexicon`/`primer`/`moving`/`method`) will render into, not
the pages themselves yet.

**Shipped this commit:**

- `site/requirements.txt` — `jinja2`, `jsonschema`, `markupsafe` (the
  site generator's own dependency set, separate from the watcher's root
  `requirements.txt`).
- `site/generate.py` — the entrypoint skeleton: `load_and_validate_content()`
  dynamically walks every top-level `content/*.json`/`data/*.json` file,
  jsonschema-validating each against its `schemas/<stem>.schema.json`
  counterpart when one exists (tolerates the one already-known gap,
  `primer.schema.json` — logged in Phase 1/2, not new); `load_cards()`
  loads `content/cards/*.json`, returning `[]` gracefully since that
  directory doesn't exist yet (no analyst run has happened for real);
  `build_jinja_env()` sets up autoescaping Jinja2; `render_pages()` renders
  `templates/base.html` directly as a placeholder `public/index.html`
  (no page builders exist yet); `copy_static()` copies `site/static/` into
  `public/static/`; `generate(public_dir=...)` runs the full pipeline and
  is safely re-runnable into the same directory. CLI: `python
  site/generate.py [--out public] [-v]`.
- `site/templates/base.html` — the shared page shell: a "Skip to content"
  skip-link as the first focusable element, a masthead nav (linking to
  every route the plan names, most not built yet), a `<main
  id="main-content">` landmark, a `{% block content %}` child templates
  will override (with a default fallback — one `<h1>` plus a short
  placeholder paragraph — so the shell alone is a valid page today), and a
  footer with the CLAUDE.md disclaimer line ("AI-curated and AI-written;
  links go to primary sources; see the Method page for how verification
  works") plus a link to `/corrections/`.
- `site/static/css/tokens.css` — the exact palette (`--color-bg
  #0B0E17`, `--color-panel #131829`, `--color-hairline #232B45`
  border/divider-only, `--color-ink #E9ECF5`, `--color-signal-cyan
  #43E5C4` for status/live accents/one-liners/the focus ring,
  `--color-star-white #F4F6FF` for headlines, `--color-reported-amber
  #D9A036`, `--color-corrected-red #E4574F`) and type custom properties
  (`--font-display` Space Grotesk, `--font-body` Inter, `--font-data`
  JetBrains Mono, tight `--tracking-display`) from the approved plan's
  section 5, with each verified AA contrast ratio recorded in a header
  comment. System-font fallback stacks are used for all three faces —
  no font files are self-hosted this commit (logged as a deferred
  nice-to-have in `IMPROVEMENT_BACKLOG.md`).
- `site/static/css/components.css` — single-column, 375px-clean base
  layout (fluid `max-width` container, no fixed-width elements), the
  skip-link's off-screen-until-focused styling, a `:focus-visible` rule
  using `--color-signal-cyan` with a visible `outline-offset` (the
  hairline color is never used for text or focus rings, per tokens.css's
  own rule), a `.font-data`/`.data` tabular-numeral utility, `.one-liner`
  styling, and minimal masthead/footer/status-chip styling so the shared
  shell doesn't look broken standalone.
- `site/tests/test_build.py` — a smoke test (6 assertions) that runs the
  real `generate()` against this repo's actual `content/`/`data/` (not
  fixtures) into a `tmp_path`, asserting it doesn't crash and produces at
  least `index.html` and `static/css/tokens.css`, plus a dedicated
  zero-cards-graceful-handling assertion and a rerun-safety assertion.
  Loads `generate.py` via `importlib.util.spec_from_file_location` rather
  than importing `site` as a package, to avoid shadowing the Python
  stdlib's own `site` module (logged).

**Verification:**

- `python -m pytest` (bare, repo root) — **470 passed, 2 deselected**,
  unchanged from before this commit (root `pytest.ini`'s `testpaths =
  tests` still only collects `tests/`; this commit's `site/tests` is a
  separate suite by design — see `IMPROVEMENT_BACKLOG.md` for why no
  `pytest.ini`/`ci.yml` change was made or needed to prove this commit's
  own tests green).
- `python -m pytest site/tests` (after `pip install -r
  site/requirements.txt`) — **6 passed**, including the smoke test
  running the real generator against this repo's live `content/*.json`
  and `data/*.json`.
- Manually inspected `public/index.html` and `public/static/css/*.css`
  from a real `generate()` run: skip-link, masthead nav, `<main
  id="main-content">`, one `<h1>`, and the footer disclaimer + `/corrections/`
  link are all present as expected; `public/` itself stays gitignored
  (already was, from Phase 1) and is not committed.

**Known gap, not this commit's to fix:** `content/cards/` still doesn't
exist at all (zero analyst runs so far), and `schemas/primer.schema.json`
still doesn't exist (a Phase 1/2 gap). `generate.py` is written to degrade
gracefully around both rather than crash, but neither absence is resolved
by this commit.

**Next up (later Phase 4 commits, per the approved plan):** `lib/linkify.py`
+ tests, then the Wire/Board/Lexicon/Primer/What's-Moving/Method page
builders and their templates, an accessibility pass, the fuller
`test_build.py` scope, and `.github/workflows/deploy.yml` (which is also
where `site/requirements.txt` and `site/tests` most naturally get wired
into automated CI, per this commit's backlog entry).

---

## 2026-07-09 — Phase 3 PM checkpoint round 2, follow-up: claims-hygiene fix on two board rows

Follow-up to the entry directly below (the >=12-row backfill). A second PM
review of that backfill re-fetched the row-count fix's own sources and
flagged two remaining claims-hygiene defects under Hard Rule 3 (every claim
must trace to a cited, live source) — both fixed this round, content-only,
no schema/test/row-count changes:

- **Meta Muse Spark row**: the sentence "Meta says it matches its older
  midsize Llama 4 model's capability using an order of magnitude less
  compute" and the framing "first major model since chief AI officer
  Alexandr Wang joined the company" were both untraceable to the row's
  cited `about.fb.com` source — re-confirmed by a fresh live re-fetch this
  round (zero occurrences of "order of magnitude" or "Wang" on that page).
  Both were dropped. The Wang framing was replaced with a claim the same
  `about.fb.com` citation does support (also re-verified live this round):
  Muse Spark is the first model in a new series from Meta Superintelligence
  Labs, capping a nine-month, ground-up rebuild of Meta's AI stack.
  `source_url` is unchanged (`about.fb.com` remains the correct PRIMARY
  citation for everything else in the row, all independently re-checked
  live in the prior round's PM review). Full reasoning, including which
  alternate sources (`ai.meta.com`, CNBC) were live-fetched and considered
  but not used as the row's `source_url`, is logged in
  `IMPROVEMENT_BACKLOG.md`.
- **xAI Grok 4.5 row**: "It was trained alongside Cursor" was untraceable
  to either of the row's two recorded `docs.x.ai` sources — re-confirmed
  live this round (no non-CSS "Cursor" mention on either page). Rather than
  drop the claim, live-fetched `https://cursor.com/blog/grok-4-5` (Cursor's
  own announcement of this same release), which confirms it directly:
  Grok 4.5 was "trained jointly with SpaceXAI" using "trillions of tokens
  of Cursor data." The sentence is kept, reworded in this repo's own words
  and grounded in that citation; `source_url` stays `docs.x.ai/developers/models`
  (unchanged), matching the same corroborating-citation pattern already
  used elsewhere in this same row for the Musk quote and exact release
  date (sourced from TechCrunch, not `docs.x.ai`).

**Verification**: `python -m pytest` re-run after both edits — **470
passed, 2 deselected**, identical to the pre-fix count (no test asserts on
the exact wording of either row's `significance` field). No schema, test,
or row-count changes were needed or made — this was a two-row prose fix
only, per the PM checkpoint's own explicit scope.

**Carried forward, unchanged:** everything listed at the end of the entry
directly below (the `CLAUDE_CODE_OAUTH_TOKEN` secret, GitHub Pages, branch
merge) still applies exactly as stated there.

---

## 2026-07-09 — Phase 3 PM checkpoint round 2: Frontier Board backfill closes the >=12-row gap

Follow-up to the PM checkpoint review of the entry directly below. That
review re-fetched all 4 of the then-existing Frontier Board rows and all
30 Lexicon entries' citations live, found every claim genuinely supported
by its cited source, confirmed the Primer's 10 slugs all resolve, and
confirmed the shortfall was honestly (not silently) documented — but it
still flagged the Frontier Board's row count as a real, unmet acceptance
bar: 4 rows against the plan's own >=12-row target, US missing OpenAI,
Google DeepMind, Meta, xAI, and Mistral entirely, and China represented by
a single row (DeepSeek). This entry is the follow-up backfill turn the PM's
review called for.

**9 new Frontier Board rows, each independently live-fetched and verified
this turn (never recalled from training memory)** — `content/frontier_board.json`
now ships **13 rows total**, past the plan's >=12-row target:

- `OpenAI` — GPT-5.6 Sol (US) — primary source
  `https://deploymentsafety.openai.com/gpt-5-6-preview` (OpenAI's own
  preview system card, on a subdomain reachable directly; `openai.com` and
  `help.openai.com` both returned HTTP 403 to every fetch attempt this
  turn — logged below), corroborated by a live-fetched TechCrunch article
  (`https://techcrunch.com/2026/06/26/openai-limits-gpt-5-6-rollout-after-government-request-says-restrictions-shouldnt-be-the-norm/`).
- `Google DeepMind` — Gemini 3.5 Flash (US) — primary source
  `https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/`.
- `Meta` — Muse Spark (US) — primary source
  `https://about.fb.com/news/2026/04/introducing-muse-spark-meta-superintelligence-labs/`.
- `xAI (SpaceXAI)` — Grok 4.5 (US) — primary source
  `https://docs.x.ai/developers/models`, corroborated by a live-fetched
  TechCrunch article
  (`https://techcrunch.com/2026/07/08/spacexai-releases-grok-4-5-which-elon-describes-as-an-opus-class-model/`)
  for the exact release date and the one Musk quote used (10 words,
  attributed). `x.ai`'s own marketing pages (`x.ai/news`, `x.ai/news/grok-4-5`)
  returned HTTP 403 to every fetch attempt; `docs.x.ai` (a different
  subdomain) was reachable and used instead — see the judgment-call log
  below.
- `Mistral` — Mistral Large 3 (**US section**, per the approved build
  plan's own already-logged Mistral-bucketing quirk — Mistral is an EU lab,
  not a US one, but the plan's Board taxonomy has only US/China/open-weights
  lanes) — primary source `https://mistral.ai/news/mistral-3/`.
- `Alibaba Qwen` — Qwen3.7-Max (China) — primary source
  `https://www.alibabacloud.com/blog/qwen3-7-the-agent-frontier_603154`.
- `Moonshot AI` — Kimi K2.6 (China) — primary source
  `https://huggingface.co/moonshotai/Kimi-K2.6` (Moonshot's own model card
  on Hugging Face), corroborated by a live-fetched SiliconANGLE article for
  the exact release date
  (`https://siliconangle.com/2026/04/20/moonshot-ai-releases-kimi-k2-6-model-1t-parameters-attention-optimizations/`;
  not on CLAUDE.md's reputable-outlet table, used only as corroborating
  detail alongside the primary source, not as the sole basis for
  `confirmed`-equivalent sourcing).
- `Zhipu AI` — GLM-5.2 (China) — primary source
  `https://huggingface.co/blog/zai-org/glm-52-blog` (Zhipu/Z.ai's own
  Hugging Face blog post).
- `ByteDance` — Seed 2.1 Pro (China) — primary source
  `https://seed.bytedance.com/en/blog/seed2-1-officially-released-advancing-ai-productivity`.

**Region tally after this round:** US 6 (Anthropic, OpenAI, Google
DeepMind, Meta, xAI, Mistral), China 5 (DeepSeek, Alibaba Qwen, Moonshot
AI, Zhipu AI, ByteDance), open-weights 2 (Ai2, NVIDIA) — China is now
represented by 5 rows, not 1, directly closing the neutrality-adjacent gap
the PM's review flagged. `tests/test_seed_content.py` gained a dedicated
regression test for this (`test_frontier_board_china_region_has_more_than_one_row`).

**`tests/test_frontier_board_meets_phase_3_target_row_count` flipped from a
non-strict `xfail` to a hard assertion**, per this checkpoint's own
instruction — `content/frontier_board.json` now has 13 rows, past the
`>=12` bar, so the test asserts it unconditionally; a future accidental
regression below 12 rows now fails the suite instead of quietly reporting
XFAIL. `FRONTIER_BOARD_ACTUAL_ROWS` (the separate, weaker floor test) was
bumped from 4 to 13 to match. Full explanation in the module's own
docstring.

**Two spec-silent judgment calls made this round, both logged in full in
`IMPROVEMENT_BACKLOG.md`:** (1) Meta's Muse Spark is tagged
`access: "consumer"` rather than `"api"`, breaking from the pattern
established by every other row (Anthropic/OpenAI/Google/xAI all use
`"api"` despite also being consumer-reachable) because Muse Spark's API is
explicitly a partner-only private preview while its broad, primary release
channel is Meta's own consumer apps — the reverse of the other rows' actual
availability shape. (2) xAI's `lab` field is written as `"xAI (SpaceXAI)"`,
not bare `"xAI"` as the approved plan's candidate list names it, because
this turn's live research turned up a real, multiply-corroborated
corporate event outside training-data knowledge: xAI's merger into SpaceX
(closed February 2026) completed its public rebrand to "SpaceXAI" on
July 6-7, 2026, days before this backfill turn. Both `content/lexicon.json`
and `content/primer.json` were **not touched** this round, per this
checkpoint's explicit scope — both already fully meet their Phase 3
acceptance bars and every sampled citation was independently re-confirmed
live by the PM's own review, so no re-run of the citation spot-checks was
warranted.

**Fetch-access note:** `openai.com`, `help.openai.com`, `x.ai`, and
`www.axios.com` all returned HTTP 403 to every fetch attempt this turn
(consistent with those sites' bot-management, not a proxy/tooling fault —
confirmed by retrying with a browser user-agent via a direct `curl` as
well). Every OpenAI and xAI board-row claim below is instead sourced from a
different, reachable subdomain of the same lab's own domain
(`deploymentsafety.openai.com`, `docs.x.ai`) or from reputable-outlet
coverage (TechCrunch) that was itself successfully live-fetched — no board
row in this backfill relies on an un-fetched or training-memory-recalled
claim.

**Verification:** `python -m pytest` — 470 passed, 2 deselected (the
`@pytest.mark.live` acceptance-proof tests, excluded by default), 0
xfailed/xpassed — full suite green, and the former xfail is gone entirely
rather than merely passing incidentally.

**Carried forward, unchanged:** `analyze.yml` still needs the owner's
`CLAUDE_CODE_OAUTH_TOKEN` secret and `vars.CLAUDE_MODEL`; GitHub Pages
still needs enabling; this branch still needs review and merge to `main`.
Per the PM's own directive, Phase 4 (frontend) may now proceed on a future
turn — the Frontier Board backfill this entry records is what was blocking
it.

---

## 2026-07-09 — Phase 3: seed-content backfill (Frontier Board, Lexicon, Primer)

One-time interactive seed-content backfill per the approved build plan's
section 4: `content/frontier_board.json`, `content/lexicon.json`, and
`content/primer.json` (new file) assembled from pre-drafted,
adversarially-verified, live-fetched-and-cited candidate data and written
to disk this turn, validated against `schemas/frontier_board.schema.json`
and `schemas/lexicon.schema.json`, plus a new `tests/test_seed_content.py`
covering both files' schema conformance and every cross-reference
(`related[]` resolution, primer-slug resolution, citation-href presence).

**Frontier Board: 4 rows shipped, against the plan's own >=12-row
target — a real, logged shortfall, not silently accepted.** This turn's
scope was to assemble the data already fetched-and-verified live by other
agents this run, not to re-fetch additional labs; the handed-off candidate
set covered only Anthropic, DeepSeek, Ai2, and NVIDIA (5 rows as given,
collapsing to 4 distinct `(lab, model)` rows once merged — see below), far
short of the plan's 12-core-row candidate list (which also named OpenAI,
Google DeepMind, Meta, xAI, Mistral, Alibaba Qwen, Moonshot, Zhipu, and
ByteDance). All three required regions are represented (US, China,
open-weights) but China has only one row (DeepSeek). Full explanation and
the specific list of labs a follow-up backfill turn would need to
independently fetch-and-verify to close the gap is logged in
`IMPROVEMENT_BACKLOG.md` under "Phase 3: seed-content backfill" — encoded
as a non-blocking, non-strict `xfail` test
(`test_frontier_board_meets_phase_3_target_row_count`) so the shortfall
stays visible in every future `pytest` run without failing the suite.

**Two of the five given board rows were both `(Anthropic, "Claude Fable
5")`, sourced from two different Anthropic pages** — merged into one row
per CLAUDE.md's Frontier Board upsert rule (keyed on exact `(lab, model)`,
one row per release, refreshed in place, never duplicated). Kept the
dedicated release-announcement URL as `source_url` and wrote one merged
`significance` string covering both sources' point. Full reasoning logged
in `IMPROVEMENT_BACKLOG.md`.

**Lexicon: 30 of 30 target terms shipped — no shortfall.** Every entry's
`related[]` list resolves to another entry's `term` in the same file, and
every entry embeds its live citation as an `<a href>` anchor inside
`deeper` (the schema has no separate `source_url` field, per the plan's
own already-logged resolution) — both mechanically checked by
`tests/test_seed_content.py`.

**Primer: 10 of 10 intended dependency-ordered terms shipped — no
shortfall.** `content/primer.json` = `{generated_at: "2026-07-09", terms:
[...]}`, in the fixed dependency order (foundation model → transformer →
attention → parameter count → context window → pretraining → fine-tuning
→ RLHF → hallucination → open weights), each slug resolving to a real
`content/lexicon.json` entry. The lowercase+hyphenate term→slug transform
used here (distinct from `scripts/plan_run.py::kebab_slug`, a card-title
slugifier) is flagged in `IMPROVEMENT_BACKLOG.md` as the convention a
future Phase 4 site generator must match when building `/lexicon/<slug>/`
routes.

**Source URLs used this backfill (fetched-and-cited live by the upstream
agents this run; spot-checkable against the corresponding `source_url` in
`content/frontier_board.json` / the inline `<a href>` in each
`content/lexicon.json` entry's `deeper` field):**

Frontier Board —
`https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5`
(Anthropic, Claude Fable 5; the merged row's kept source — the given
`models/overview` page URL was folded into this row's significance text
rather than kept as a second citation, per the merge decision above),
`https://api-docs.deepseek.com/news/news260424/` (DeepSeek-V4 Pro),
`https://allenai.org/blog/olmo3` (Ai2 Olmo 3.1), `https://research.nvidia.com/labs/nemotron/Nemotron-3-Ultra/`
(NVIDIA Nemotron 3 Ultra).

Lexicon (one arXiv/primary URL per term, all distinct except two terms
that intentionally share Kaplan et al. 2020) —
`https://arxiv.org/abs/1706.03762` (transformer, attention),
`https://platform.claude.com/docs/en/build-with-claude/context-windows`
(context window), `https://arxiv.org/abs/2203.02155` (RLHF),
`https://arxiv.org/abs/1701.06538` (MoE), `https://arxiv.org/abs/1503.02531`
(distillation), `https://www.anthropic.com/research/building-effective-agents`
(agent), `https://arxiv.org/abs/2005.11401` (RAG),
`https://arxiv.org/abs/1801.06146` (fine-tuning), `https://arxiv.org/abs/2202.03629`
(hallucination), `https://arxiv.org/abs/2001.08361` (scaling laws, compute),
`https://github.com/openai/evals` (evals), `https://arxiv.org/abs/1508.07909`
(tokenization), `https://arxiv.org/abs/2005.14165` (parameter count),
`https://arxiv.org/abs/2407.21783` (post-training), `https://arxiv.org/abs/2201.11903`
(chain-of-thought), `https://arxiv.org/abs/1804.07461` (benchmark),
`https://arxiv.org/abs/2103.00020` (multimodal), `https://arxiv.org/abs/2211.05102`
(inference), `https://www.bis.gov/press-release/commerce-implements-new-export-controls-advanced-computing-semiconductor-manufacturing-items-peoples`
(export controls), `https://arxiv.org/abs/2108.07258` (foundation model),
`https://cdn.openai.com/papers/gpt-4-system-card.pdf` (system card),
`https://arxiv.org/abs/2209.07858` (red teaming),
`https://platform.claude.com/docs/en/about-claude/models/overview`
(knowledge cutoff), `https://arxiv.org/abs/2408.03314` (test-time compute),
`https://huggingface.co/openai/gpt-oss-120b` (open weights),
`https://arxiv.org/abs/1606.06565` (alignment), `https://arxiv.org/abs/1810.04805`
(pretraining), `https://arxiv.org/abs/2307.03718` (frontier model).

Verification: `python -m pytest` — 468 passed, 2 deselected (live tests
excluded by default per `pytest.ini`), 1 xfailed (the documented Frontier
Board row-count shortfall above) — full suite green.

**Carried forward, unchanged from every earlier checkpoint:** `analyze.yml`
still needs the owner's `CLAUDE_CODE_OAUTH_TOKEN` secret and
`vars.CLAUDE_MODEL` before it can run end-to-end; GitHub Pages still needs
enabling before a future `deploy.yml` can publish; this branch still needs
review and merge to `main`. Additionally, per this entry: a follow-up
Frontier Board backfill (OpenAI, Google DeepMind, Meta, xAI, Mistral, and
2-3 more China-region labs at minimum) remains open before Phase 3's own
>=12-row acceptance bar is genuinely met, not just partially addressed.

---

## 2026-07-09 — Phase 2 PM checkpoint round 1: citation-field spec-4-vs-spec-5 backlog entry (documentation-only)

The PM's Phase 2 sign-off review independently re-ran the full test suite
(454 passed, 2 deselected — the same `@pytest.mark.live` acceptance proofs
`pytest.ini` always excludes; this checkpoint's own stale-but-honest 452
figure above predates commit `0974df8`, which added 2 regression tests) and
found the pure-code half of Phase 2 solid across the board — path-allowlist
CI enforcement, the corroboration decision procedure, ledger dropped-cluster
permanence (including a real defect the PM caught and this repo already
fixed in `0974df8`), the degradation ladder's empty-queue override, and four
of five named spec-silent decisions properly logged. **One real gap was
found and is fixed by this checkpoint, documentation-only, no code or schema
change:** the citation-field naming mismatch between spec sec-4's loop
diagram (`citations[] = {url, supporting_quote ≤15w}`) and spec sec-5's card
field list (`citations:[{url, outlet, quote}]`) had never been logged as its
own resolved decision in `IMPROVEMENT_BACKLOG.md`, even though `CLAUDE.md`'s
own rendering of the sec-4 diagram and `schemas/card.schema.json` both
already use sec-5's fuller shape, and have since this repo's very first
commit — the resolution was real and correct, just silent. Logged now, in
full, under "Phase 2 PM checkpoint, round 1" in `IMPROVEMENT_BACKLOG.md`,
per the approved build plan's §8 commitment to log every spec-silent
decision as it's made (in this case, as it's *found* to have already been
made without a log entry).

Also logged, as a non-blocking backlog candidate rather than implemented
this round (per the PM's own instruction): a small pure-code test that
would parse `.github/workflows/analyze.yml` directly and assert the
VERIFIER step's tool grants and the gate-before-commit step ordering,
converting today's manual-YAML-inspection facts (see this file's own P2
acceptance-criteria section, criterion 2, below) into a real regression
test. Not implemented in this round; see `IMPROVEMENT_BACKLOG.md`.

**Everything else from this PM round carries forward unchanged, exactly as
the PM's review instructed:** `analyze.yml` has still never executed on
GitHub Actions (it needs the owner to add the `CLAUDE_CODE_OAUTH_TOKEN`
secret and set `vars.CLAUDE_MODEL`, neither creatable from any session) —
the first real `watch.yml` → `analyze.yml` cycle after that remains the only
true end-to-end proof of the two LLM-judgment acceptance criteria (a
single-source rumor publishing as REPORTED or not at all; a fabricated
benchmark number getting stripped by the verifier). This checkpoint entry
does not, and must not, represent either as verified before that first live
run — the P2-acceptance-criteria breakdown further below in this file
already states this correctly and is preserved unchanged.

Verification: `python -m pytest` — 454 passed, 2 deselected, matching the
PM's own independently-re-run figure exactly. No production code changed
this round; only `IMPROVEMENT_BACKLOG.md` and this file.

With this entry landed, Phase 2 sign-off directives are addressed in full;
per the PM's own instruction, Phase 3 (seed content) is next.

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
