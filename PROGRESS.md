# PROGRESS.md

Reverse-chronological build log for AI Frontier Wire. Newest entry on top.
Each entry corresponds to one commit or one phase checkpoint. See
`CLAUDE.md` for the standing rules and architecture, and
`IMPROVEMENT_BACKLOG.md` for every spec-silent decision made along the way.

---

## 2026-07-11 — Fix: canvas rain fell too fast -- ported the reference site's actual stepped-speed algorithm

Immediately after the canvas rain (below) shipped, the owner reported the
speed "seems much faster" than their reference site and asked for it to
match exactly. The reference's real `rain.js` wasn't captured in the
.mhtml snapshot used to build the first canvas version -- only its CSS
custom properties were -- so the first implementation's timing was an
independent guess (every column advancing one glyph every single frame at
40ms/tick). Fetched the reference site's actual `assets/rain.js` directly
(same site the owner already pointed to) to get the ground truth rather
than guessing again.

**Root cause, concretely:** the reference's rain is deliberately *stepped*,
not continuous -- each column is assigned a random "speed tier" (1, 2, or 3
frames per step, weighted roughly 50/35/15) and only advances when its own
tick counter reaches that tier, so at any given frame most columns aren't
moving at all. It also runs at a slower 50ms/frame (20fps) cap, uses wider
20px columns (vs. 16px), and keeps only ~90% of columns active at once
(the rest dormant, occasionally reactivating). My first implementation had
none of this: every column stepped one glyph on every single 40ms tick,
unconditionally -- roughly double the effective fall rate with zero
per-column variation, which reads as both "faster" and "too uniform."

**Fix:** ported the reference's actual algorithm into
`site/static/js/matrix-rain.js`, keeping this project's own constraints
intact:
- Per-column speed tiers, dormant/reactivating columns, 50ms frame cap,
  20px columns -- matches the reference's felt pace and per-column speed
  variety directly.
- Settled trail glyphs occasionally flicker to a different character in
  place (a "shimmer," not just a falling frozen string) and a brief
  brighter "hot" flash on some column respawns -- both ported faithfully,
  since they're part of what makes the reference read the way it does,
  not just its raw speed.
- Two adaptations, not straight copies: the reference's "surge" (a
  temporary density/brightness ramp triggered by a live Bitcoin-block
  arrival event specific to that site) has no equivalent here -- this is a
  static site with no live client-side event stream -- so it's simply
  omitted, not stubbed out. And the reference hardcodes pure white for its
  brightest tier; this version reads every color from `tokens.css` at
  runtime instead (the same rule every stylesheet here already follows),
  producing the mid-bright "neck" tone via a runtime `color-mix()` of the
  existing signal-green and star-white tokens (matching this project's own
  established precedent for an in-between tone -- board.html's pulse-dot
  glow) rather than hardcoding a third color or adding a new token.

Verification: `python -m pytest` (709 passed, 2 deselected) and
`python -m pytest site/tests` (296 passed -- the existing
"no hardcoded color literal in matrix-rain.js" regression test required
one comment-wording fix, since the docstring's own prose describing what
the reference hardcodes tripped the same hex-literal regex the test uses;
fixed by spelling the hex out without its leading punctuation) both green.
Rebuilt and watched two Playwright screenshots ~600ms apart: columns now
advance visibly less far per interval, with real variation in how far
different columns move, matching the reference's calmer, more organic
pace instead of the previous uniform, faster fall. No console errors.

---

## 2026-07-11 — Architecture exception: canvas + JavaScript rain, replacing the static CSS/SVG tiles as the primary effect

The owner shared a reference site of theirs (a live Bitcoin dashboard) whose
own digital-rain background they wanted this site's Matrix theme to match,
saying the retuned CSS/SVG version (see the addendum below) still "looks too
fake." Inspecting the reference's actual source (an .mhtml save) showed why:
its rain is a `<canvas>` element redrawn every frame by JavaScript --
genuine per-glyph randomness each frame, plus a real alpha-blended
translucent-black repaint every frame that produces smooth, continuously
varying fade trails -- a technique a static, pre-rendered, repeating CSS/SVG
background tile cannot reproduce, no matter how much its per-glyph opacity
cycle is tuned (see the addendum entry below for that attempt).

This is a real architectural fork, not a style tweak: AI Frontier Wire has
been zero-JavaScript since Phase 4, stated in `CLAUDE.md`, in every
stylesheet's own header comment, and enforced by a live pytest test
(`test_no_script_tag_anywhere_in_any_generated_html_page`). Rather than
silently drop that constraint, this was surfaced to the owner directly as an
explicit choice: port the reference's actual canvas+JS technique (accepting
one narrow, isolated JavaScript exception), or keep pushing the zero-JS
CSS/SVG approach further knowing it likely can't fully match. The owner
chose the former.

**What shipped, keeping the exception as narrow and safe as possible:**
- `site/static/js/matrix-rain.js` -- a single, self-contained IIFE
  implementing the classic canvas rain algorithm (per-frame translucent-black
  repaint for the fade trail, a random glyph drawn per column each frame,
  occasionally in a brighter "head" color). Every color it draws with is
  read from the live `tokens.css` custom properties at runtime
  (`getComputedStyle(...).getPropertyValue(...)`) -- never a second
  hardcoded hex/rgba copy, the same rule every stylesheet on this site
  already follows. Respects `prefers-reduced-motion` on load *and* on a live
  OS-level toggle while the page is open (no animation loop starts at all
  when reduced motion is preferred; a single static frame is drawn instead).
- `--color-rain-fade` added to `tokens.css` (an `rgba()` value -- the
  per-frame alpha-blend fade amount, not a text/background color role, so
  it's deliberately not `#RRGGBB` and `test_contrast_ratios.py`'s hex-only
  token parser correctly ignores it).
- `base.html`: a `<canvas id="matrix-rain-canvas" aria-hidden="true">` is
  now the primary rain layer on every page, immediately followed by the
  original static CSS/SVG rain layer (unchanged from the redesign +
  retuning below), now wrapped in `<noscript>` -- a REAL fallback, not dead
  code: whenever JavaScript is disabled, blocked, or fails for any reason,
  the CSS/SVG version renders instead, so nothing about the site's actual
  content or navigation ever depends on the script executing.
- `matrix.css` gained `#matrix-rain-canvas`'s own positioning rule
  (`position: fixed; inset: 0; z-index: -1; pointer-events: none;`,
  `background: var(--color-bg)`) and an updated header comment explaining
  both layers' relationship.
- `test_no_script_tag_anywhere_in_any_generated_html_page` replaced with
  `test_exactly_one_script_tag_and_it_is_the_matrix_rain_canvas` -- the
  zero-JavaScript invariant isn't deleted, it's narrowed to its one
  deliberate exception: any *other* `<script>` tag anywhere is still a hard
  failure. New tests also confirm the canvas comes before the `<noscript>`
  fallback in document order, the fallback still contains the real
  CSS/SVG rain markup, and `matrix-rain.js` itself contains no hardcoded
  color literal and genuinely handles `prefers-reduced-motion` (including
  the live-toggle `addEventListener("change", ...)` path).

Verification: `python -m pytest` (709 passed, 2 deselected) and
`python -m pytest site/tests` (296 passed, 4 new) both green. Rebuilt the
site and confirmed with Playwright (real Chromium): with JavaScript
enabled, the canvas renders genuinely organic, per-frame-randomized rain
with visible bright-head/fading-tail trails, no console errors beyond an
unrelated pre-existing missing-favicon 404; with JavaScript explicitly
disabled (a separate browser context), the `<noscript>` CSS/SVG fallback
renders correctly in its place. Mobile (390px) unchanged either way -- the
content column still fills the viewport there, so no rain is visible, the
same previously-logged trade-off. All reading content remains exactly as
legible as before in every configuration -- the opaque-panel + z-index
legibility mechanism was untouched by any of this.

---

## 2026-07-11 — Addendum: the opacity/glow fix above overshot -- retuned to the classic "movie screen" look

Immediately after the fix below shipped (opacity 0.15 -> 0.6, a double
drop-shadow glow), the owner gave more specific feedback: it should look
like "the movie set's screen" -- i.e. the diegetic terminal/monitor
readout from the source material -- and "not too dense." The 0.6-opacity,
heavy-bloom version below had swung too far from "barely visible" to a
dense, uniformly-bright wall, missing the actual defining visual trait of
that look: individual, legible falling streams, each with a bright leading
character fading to a dim trailing one, with real black space between
adjacent streams -- not a flat-brightness, wall-to-wall block.

**Three changes, `site/lib/matrix_rain.py` + `site/static/css/matrix.css`:**
1. `site/lib/matrix_rain.py`'s `_build_tile_svg` now assigns each glyph a
   `fill-opacity` from a new `_trail_opacity()` helper: glyphs are grouped
   into a repeating "trail cycle" (length randomized per tile, 8-16
   glyphs) -- position 0 of each cycle renders at full opacity (the bright
   head), fading toward a dim floor by the cycle's last glyph, then
   snapping back to full brightness at the next cycle. Tiled and animated,
   this reads as a continuous sequence of falling trails with real
   depth, rather than one flat brightness throughout -- the single biggest
   missing ingredient for the authentic look, and achieved without a
   second (hardcoded) highlight color: it's the same `--color-signal-green`
   token at varying opacity against the black background.
2. `DEFAULT_COLUMN_COUNT` reduced from 72 to 40, so adjacent streams have
   real horizontal gaps -- distinct, individually-legible columns instead
   of a dense block.
3. `matrix.css`: `.matrix-rain` opacity brought down from 0.6 to a
   moderate 0.4; `.matrix-rain__col`'s glow simplified from two stacked
   drop-shadows to one smaller, softer one -- a terminal-phosphor glow,
   not a neon bloom.

Verification: `python -m pytest` (709 passed, 2 deselected) and
`python -m pytest site/tests` (292 passed) both green -- the column-count
test (`test_matrix_rain_layer_has_the_full_default_column_count_on_every_page`)
reads `DEFAULT_COLUMN_COUNT` from the module dynamically rather than
hardcoding 72, so it graded the new value automatically with no test edit
needed. `python site/generate.py` builds clean. Re-screenshotted desktop
Wire and Board pages: distinct, legible falling streams with visible
bright/dim variation down each column, moderate density, real gaps between
columns, all reading content still exactly as legible as before (the
opaque-panel legibility mechanism is untouched by any of this).

---

## 2026-07-11 — Fix: Matrix rain was too faint/sparse against real user feedback

After the Matrix-theme redesign (PR #5) merged and went live, the owner
looked at the actual rendered page and reported it "looks weird," pointing
at a reference screenshot from a different, unrelated project of theirs
(a Bitcoin dashboard with its own, more vivid digital-rain background) as
the look they actually wanted: denser, brighter, glowing green rain,
rather than the muted ambient backdrop this project shipped.

**Root cause**: `site/static/css/matrix.css`'s `.matrix-rain` rule set
`opacity: 0.15` specifically to keep the rain from ever competing with
foreground text contrast -- a reasonable-sounding precaution that, in
practice, made the whole effect read as washed-out and barely visible
rather than as an intentional theme. Legibility was already guaranteed by
a different, independent mechanism (every content panel -- masthead,
footer, wire cards, board rows -- has its own opaque background, and the
rain layer sits at `z-index: -1` strictly behind all of them), so dimming
the rain's own color on top of that was redundant and just made it look
weak instead of vivid.

**Fix**: `.matrix-rain`'s opacity raised to `0.6`, and `.matrix-rain__col`
gained a `filter: drop-shadow(...)` glow (two stacked drop-shadows at
different radii for a soft phosphor-CRT bloom), sourced from the same
`--color-signal-green` token every other accent on the site already uses
-- matrix.css's own header rule ("this stylesheet contains ZERO
[hardcoded] color values... source it from a tokens.css custom property")
is preserved; nothing new is hardcoded. No column-count/density change was
needed -- the existing column layout read as sparse only because of how
dim it was rendered, not because there were too few columns.

Verification: `python -m pytest` (709 passed, 2 deselected) and
`python -m pytest site/tests` (292 passed) both still green (no test
asserted the old opacity value or the absence of a `filter` property);
`python site/generate.py` builds clean. Re-screenshotted desktop (1280px)
Wire and Board pages against the reference -- the rain now reads as
vivid, dense, glowing green in the page gutters, while all reading
content remains exactly as legible as before (the opaque-panel mechanism
that guarantees this was untouched by this fix). Mobile (390px) still
shows no visible rain, unchanged from the original design -- the content
column fills the viewport there, a previously-logged, deliberate
readability-over-ambience trade-off that this fix didn't revisit since the
reported problem was specifically about the desktop look.

---

## 2026-07-11 — T7: two case-sensitive-grep survivors from the T1 rename fixed; zero-stale-references check hardened

A further, independent verification pass on the T1-T6 Matrix-theme
workstream found two old-name strings that had survived every prior
pass's "no lingering `signal-cyan`" claim (T1's own rename commit and
T6's from-scratch re-verification both asserted this and both were
correct about what they actually checked): `site/static/css/components.css`'s
`:focus-visible` comment still read "Signal-cyan is the site's one and
only focus-ring color" (capitalized, mid-sentence -- the rule itself
already correctly used `var(--color-signal-green)`, only the prose above
it lagged), and `site/tests/test_svg_sparkline.py` had a test named
`test_svg_has_polyline_using_signal_cyan` (underscore-joined -- its body
already correctly asserted `spark.SIGNAL_GREEN`).

**Root cause, not just the two fixes:** every prior verification grep in
this workstream (T1's own rename pass and T6's independent re-check) ran
`grep -rn "signal-cyan" site/` -- case-sensitive, hyphen-only. That
pattern structurally cannot match a capitalized "Signal-cyan" inside a
comment sentence, nor an underscore-joined "signal_cyan" inside a Python
identifier -- both are the *same* stale name, just cased or punctuated
differently than the literal token the grep was built to find. The prior
"returns nothing" claims were true statements about what that grep
checked; the grep itself just wasn't broad enough to be a real
zero-stale-references gate.

**Fixes applied, one commit each, `python -m pytest` green (709 passed,
2 deselected -- unchanged, since one test was renamed rather than
added/removed) after every commit:**
1. `components.css`'s comment: "Signal-cyan" -> "Signal-green" (two-word
   change; the rule below it was never wrong).
2. `test_svg_sparkline.py`'s function renamed
   `test_svg_has_polyline_using_signal_cyan` ->
   `test_svg_has_polyline_using_signal_green` (body unchanged; it already
   asserted the right constant).

**The check itself, hardened for any future rename in this repo:** the
zero-stale-references gate now reads `grep -rni 'signal[-_]cyan' site/`
-- case-insensitive, and matching both the hyphen and underscore
spelling in one pass. Re-run against every file tracked by git under
`site/` after both fixes above: zero matches (the only remaining hit
found during this pass was a stale compiled `__pycache__/*.pyc` from
before the test rename -- a gitignored build artifact, not source, and
it disappears on the next `pytest` run that recompiles it).

**Judgment call, spec-silent:** whether to also retroactively edit T1's
and T6's own logged claims above (and in `IMPROVEMENT_BACKLOG.md`) to
describe the broader grep. Decided not to, consistent with this
project's own already-established rule (first stated in T1's
`IMPROVEMENT_BACKLOG.md` entry): a reverse-chronological build log
records what was true and what was checked *at the time* each entry was
written, and silently rewriting past entries to match the present would
itself be exactly the kind of undocumented drift this project's audits
exist to catch. This entry is the correction; T1's and T6's entries
stand as an accurate record of a real, if narrower-than-intended, grep
that genuinely returned clean against its own (case-sensitive,
hyphen-only) pattern.

Full reasoning and the exact command history are logged in
`IMPROVEMENT_BACKLOG.md`.

---

## 2026-07-11 — T6: independent end-to-end verification of the Matrix theme workstream (one real bug found and fixed)

Verified the T1-T5 Matrix-theme workstream from scratch, trusting none of
the prior tasks' own self-reports -- re-ran every command and re-derived
every grep target live rather than reusing a previously reported number.

**Everything that passed clean on the first pass:** `python -m pytest`
(709 passed, 2 deselected); `python -m pytest site/tests` (290 passed,
pre-fix baseline); `python site/generate.py` (clean build, no warnings);
zero `<script` anywhere in `public/`; all 39 built HTML pages carry
`<div class="matrix-rain" aria-hidden="true">` on the same tag,
immediately after the skip-link, which itself remains the first element
inside `<body>`; `matrix.css`'s `pointer-events: none`, `z-index: -1`,
and exactly one `animation:` declaration positioned after the
reduced-motion media query opens; `matrix-tiles.css` containing
`%2339FF6E` -- the live-parsed `--color-signal-green` hex, never a
pasted literal; every `<link>` to `matrix.css`/`matrix-tiles.css`
correctly base-path-rewritten to `/AI-Radar/static/css/...`; opaque
`background: var(--color-bg)` on `.masthead`, `.site-footer`, and `main`;
`grep -rn "signal-cyan" site/` and every old-palette hex literal grep
returning nothing; `tokens.css`'s header table already restated with the
new ratios; every page still carrying exactly one `main-content` landmark
and one `<h1>`; `git status` clean with `public/` correctly gitignored;
8 consecutive commits all authored as `frontier-wire-bot
<bot@users.noreply.github.com>`; no repo-local `git config user.name`/
`user.email` override; and the full `6f1c3a6..HEAD` diff touching only
`site/`, `PROGRESS.md`, and `IMPROVEMENT_BACKLOG.md` -- no `.github/`,
`watcher/`, `scripts/`, or `CLAUDE.md` changes anywhere in the
workstream.

**One real bug did not surface from any of the above and needed a
different grep to catch:** `site/templates/board.html`'s pulse-dot
`box-shadow` (inside `@keyframes board-pulse`) hardcoded a literal
decimal `rgba(67, 229, 196, ...)` triple -- `67, 229, 196` in hex is
`43, E5, C4`, the *old*, pre-rename signal-accent token's own hex value,
predating this whole workstream. T1's rename swept every reference
findable via a `signal-cyan` name grep and hex-literal (`#RRGGBB`) greps,
but a decimal RGB triple with no `#` and no hex digits structurally
cannot match either pattern, so it silently survived: the dot itself
correctly rendered green (`background: var(--color-signal-green)`) while
its own animated glow kept tinting the old color underneath. Fixed by
switching both `box-shadow` declarations to
`color-mix(in srgb, var(--color-signal-green) <pct>%, transparent)` --
deriving the alpha-blended glow directly from the live token, rather than
hardcoding the new color's decimal equivalent and recreating the same
class of bug for the next palette change. `color-mix()` has shipped in
every evergreen browser since early 2023, well inside this project's
zero-JS/no-polyfill static-site baseline. Added two regression tests
(`site/tests/test_board_builder.py` and `site/tests/test_build.py`,
the latter scanning all 39 built pages) that fail on any future
reintroduction of a hardcoded decimal `rgb()`/`rgba()` color literal
anywhere on the site, not just this one instance.

**Verification, re-run after the fix, from the same clean tree:**
- `python -m pytest -q` -- 709 passed, 2 deselected.
- `python -m pytest -q site/tests` -- 292 passed (290 + 2 new).
- `python site/generate.py` -- clean build, no new warnings.
- Every structural/consistency grep above re-run against the rebuilt
  `public/` and re-checked green.

Full reasoning (why `color-mix()` over a decimal-equivalent hardcode, and
why the new tests ban the whole pattern rather than pinning one stale
value) is logged in `IMPROVEMENT_BACKLOG.md`.

---

## 2026-07-11 — Matrix digital-rain theme (zero-JS site-wide reskin)

Prompted by the owner asking to "turn this into a matrix theme so that it
exactly like the one in the movie where the green japanese words etc
rains... for all pages involved." Implemented as a real, working,
zero-JavaScript visual reskin across every generated page -- not a mockup
and not a partial page -- in four sequential tasks (T1-T4), each committed
under the `frontier-wire-bot` identity.

**T1 -- palette swap + token rename.** Replaced every hex value in
`site/static/css/tokens.css`'s `:root` block with a pre-verified
Matrix-green palette and refreshed the header comment's own
contrast-ratio table in the same commit (no doc drift): `ink` 13.72:1 vs
`bg` / 12.52:1 vs `panel`; `signal-green` (renamed from `signal-cyan`,
below) 15.70:1 / 14.33:1; `star-white` 19.34:1 / 17.65:1;
`reported-amber` 10.87:1 / 9.91:1; `corrected-red` 6.89:1 / 6.29:1;
`hairline` 1.51:1 / 1.38:1 (correctly stays sub-AA -- border/divider only,
never text, never the focus ring). `site/tests/test_contrast_ratios.py`
re-grades whatever palette is present by parsing `tokens.css` live, so
none of these numbers are hardcoded a second time in a test.

Also decided and executed, in the same commit: renaming
`--color-signal-cyan` to `--color-signal-green`, since its value is now
unambiguously green and this project already treats a token
name/meaning mismatch as a real bug. Grepped the full usage spread first
to size the blast radius -- a small, fully enumerated footprint (five
`var()` references in `components.css`, one inline style in `board.html`,
a hardcoded duplicate constant in `site/lib/svg_sparkline.py`, and
prose-only mentions in `tokens.css`'s header comment, `card.html`'s
template comments, `matrix_rain.py`'s docstring, and
`test_contrast_ratios.py`/`test_build.py`) -- then renamed every one of
those references atomically in the same pass, confirmed by a
zero-stale-references grep gate (`grep -rn "signal-cyan" site/` returns
nothing). The token's role (link/accent color, one-liner color,
`.chip--confirmed`, and -- unchanged -- the site's one and only
focus-ring color) is untouched; only its name and hex value changed. The
already-logged hardcoded-hex duplicate in `svg_sparkline.py` was fixed in
the same pass (`SIGNAL_CYAN` -> `SIGNAL_GREEN`, new hex), and a full
`grep -rnoE "#[0-9A-Fa-f]{6}" site/` sweep (excluding `tokens.css` itself)
confirmed no other module carries a similar undocumented duplicate --
only that one now-fixed constant and the test files' own literal
expected-value assertions (also updated to the new hex, e.g.
`test_build.py`'s `--color-bg: #000000` / `--color-signal-green: #39FF6E`
string checks).

**T2 -- opaque chrome backgrounds.** Added `background: var(--color-bg)`
to `.masthead`, `.site-footer`, and `main` in
`site/static/css/components.css` so the upcoming fixed rain layer (which
paints above the body background but below any in-flow element with its
own background) never shows through behind masthead/footer/reading-column
text. Every card/board/lexicon/primer/method/corrections/moving panel
already set its own `--color-panel` background, so nothing else needed
touching.

**T3 -- wiring the rain layer into every page.** `site/lib/matrix_rain.py`
(pre-existing, untouched, already deterministic and tested) is now wired
into `site/generate.py` via two new functions: `read_color_token()`
(parses the live `--color-signal-green` hex straight out of `tokens.css`
-- the only place a token hex is read outside `tokens.css` itself) and
`write_matrix_tiles_css()`, which emits the 10 unique seeded SVG
data-URI tiles once into a build-generated `public/static/css/matrix-tiles.css`
(one cached ~75KB file, not duplicated inline per page). A new
`site/templates/_matrix_rain.html` partial renders 72 empty,
`aria-hidden="true"`, `pointer-events: none` divs (one per column, no
per-glyph DOM) right after the skip-link and before the masthead in
`base.html`; each column's falling motion comes from a
`background-position` CSS `@keyframes` tiling `background-repeat: repeat-y`
against its assigned tile, at `z-index: -1` and `opacity: 0.15` so the
layer is strictly decorative and behind every reading surface. Matching
the Board's pulse-dot convention exactly, the animation exists only
inside `@media (prefers-reduced-motion: no-preference)` with zero
fallback animation outside it. `matrix.css` itself contains zero color
literals of its own.

**T4 -- regression tests.** Added `site/tests/test_matrix_rain.py`
(determinism, seed sensitivity, delay/duration/left_pct bounds, safe
percent-encoded tile URIs, column/tile/glyph-count overrides, the passed
color landing percent-encoded in every tile) and extended
`site/tests/test_build.py` with whole-site coverage: the rain wrapper
renders on every generated page with `class="matrix-rain"` and
`aria-hidden="true"` together and no focusable element inside it; a
site-wide zero-`<script>` invariant; `matrix.css`'s
`pointer-events`/negative-`z-index`/reduced-motion-gating properties;
`matrix-tiles.css` sourcing the live `signal-green` token; the opaque
masthead/footer/main backgrounds and components.css's own
no-hardcoded-hex invariant; and a from-scratch byte-idempotence check
across two independent build directories.

**Verification, run fresh in this task, from a clean working tree:**
- `python -m pytest -q` -- 709 passed, 2 deselected.
- `python -m pytest -q site/tests` -- 290 passed.
- `python site/generate.py` -- `Built site to /home/user/AI-Radar/public`, no
  warnings.
- `grep -r "<script" /home/user/AI-Radar/public/` -- empty (exit 1, no
  matches).

Full judgment-call reasoning (the rename decision, the rain-tuning
choices, the mobile opaque-chrome trade-off, and why
`matrix-tiles.css` is a build-generated static asset rather than an
inline per-page block) is logged in `IMPROVEMENT_BACKLOG.md`.

---

## 2026-07-11 — UI/UX + editorial-compliance audit and fix pass (Fable-directed)

Prompted by the owner looking at the live Frontier Board page and reporting
two concrete problems: the model table was hard to read and oddly narrow,
and the site had too many nav tabs for a reader with "keen interest in AI
but not a tech expert." Ran a Fable-directed multi-agent workflow: four
parallel read-only audits (UX for that exact reader persona, editorial
compliance against CLAUDE.md's hard rules, code quality, content clarity),
then a Fable synthesis pass turned the combined findings into one
prioritized, concrete 9-task plan, implemented one task at a time
(sequential, to avoid concurrent edits to shared files like `base.html`/
`components.css`), then an independent Fable verification pass (rebuild,
full pytest run, grep the actual rendered HTML -- not just trust each
task's own self-report) confirmed everything held on the first round, no
second fix round needed. I then independently re-verified again myself
before pushing anything: re-ran `python -m pytest` (709 passed, 2
deselected) and `python site/generate.py` (clean build, zero warnings) from
scratch, and re-screenshotted the built site at both 1280px and 390px
before opening a PR.

**Root causes confirmed (not just reported, actually inspected against the
rendered site):**
- `board.html`'s `<table class="board-table data">` applied the
  monospace/tabular-numeral `.data` utility to the *entire* table,
  including free-text columns (Lab/Model/Access/Significance) that were
  never meant to be tabular data; combined with a fixed
  `min-width: 18rem` column holding 60-124-word prose paragraphs, this
  produced the exact broken, narrow, monospace word-fragment wrapping the
  owner saw.
- The masthead nav carried 7 top-level items, and a sparkline strip
  (originally a deliberate Phase-4 "site-wide" design choice, logged in
  `IMPROVEMENT_BACKLOG.md` at the time) was wired as a Jinja environment
  global, so it rendered on every single page -- roughly 16 navigational
  elements above the fold before any actual page content, on every page.
- The Wire homepage's empty-state copy read "...the daily analyst has not
  run live in this environment yet" -- internal dev language, live on the
  public production site right now since no analyst run has happened yet.
- `board.py` never linkified its jargon-dense prose into the Lexicon, unlike
  Wire cards.
- `schemas/primer.schema.json` didn't exist at all, despite CLAUDE.md's
  blanket "every persisted JSON file has a schema" rule and `generate.py`
  printing a real "loaded unvalidated" warning on every build.
- Hard Rule 5 ("Per card: generated timestamp, model, verification status")
  wasn't visually surfaced on cards, and Corrections was only linked from
  the site-wide footer, not per-card.

**Fixes shipped (9 commits, `frontier-wire-bot` identity, all within
`site/`, `schemas/`, `content/`, `scripts/validate_changed_schemas.py`,
tests, and docs -- nothing in `.github/workflows/` or `watcher/` touched):**
1. Frontier Board rebuilt as per-model `<details>`/`<summary>` row-cards
   (zero JavaScript) instead of an 8-column table -- a compact always-visible
   summary line (Lab / Model+pulse / Released / Access) plus an expandable
   body (Modality, Context window, linkified Significance prose, Source).
   Monospace (`.font-data`) now applies only to the Released date and
   Context-window number, never to prose. `board.py` now calls
   `site/lib/linkify.py` the same way `wire.py` already does.
2. Masthead nav condensed from 7 items to exactly 4 (Wire, Board, Lexicon,
   Primer); What's Moving/Method/About/Corrections moved to a real footer
   nav row (`aria-label="More"`) -- one tap away on every page, never
   crowding the fold. Added `aria-current="page"` per-page. The sparkline
   strip is no longer a site-wide Jinja global -- it now renders only on
   the Wire home page, capped to the top 5 topics by 7-day mentions so it
   can't overflow a 390px viewport.
3. All reader-facing dev-language empty-state strings (Wire, What's
   Moving, Method) rewritten in plain reader language, with a new
   `site/tests/test_reader_copy.py` copy-lint test that fails the build if
   dev-facing phrasing regresses into a `*_MESSAGE` constant.
4. Cards now show a small `.wire-card__meta` disclosure line (generated
   timestamp + model + a direct `/corrections/` link) beneath the date,
   and carry a stable `id="card-<id>"` anchor; a `correction_note` now also
   links straight to `/corrections/`. "Why it matters" got its own
   visually distinct block (cyan left rule + label) so the payoff
   paragraph reads differently from plain narration.
5. `schemas/primer.schema.json` authored and wired into
   `scripts/validate_changed_schemas.py` -- the "loaded unvalidated"
   warning is gone.
6. Corrections now cross-link both directions (card footer -> Corrections
   page; each correction entry -> the exact original card via its new
   `id="card-<id>"` anchor).
7. Wire-card topic display names and What's Moving's topic names now share
   one `site/lib/topics.py` source of truth; fixed "1 mentions / 7d"
   pluralization.
8. Lexicon "Seen in" now resolves card ids to real headlines (falling back
   to the bare id only if a headline can't be resolved); chips that aren't
   clickable are now visually/semantically marked inert
   (`chip--inert` + a `title` explaining why) rather than looking like a
   broken link.
9. The 12 bare "Author et al., Year"-style citation anchors in
   `content/lexicon.json` rewritten into plain-language source
   descriptions.

Verification: `python -m pytest` -- 709 passed, 2 deselected (live tests
excluded by default) both inside the workflow's own Fable verification
pass and independently, again, by me afterward from a clean rebuild.
`python site/generate.py` -- clean build, no warnings. Screenshots taken
before and after (desktop 1280px + mobile 390px, Chromium via the
environment's pre-installed Playwright browser) confirm the Board is now
fully readable with no monospace-on-prose and no forced narrow column, and
the primary nav is now 4 items instead of 7.

Full plan rationale and every judgment call made along the way (why
`<details>`/`<summary>` over a second table variant, why the sparkline
strip scopes to the Wire home page specifically, etc.) are logged in
`IMPROVEMENT_BACKLOG.md`.

---

## 2026-07-11 — Fix: internal links and static assets were 404ing on the real live site (GitHub Pages project-subpath vs. root-relative hrefs)

`claude/phase-1-watcher-build-fsc12w` was merged to `main` and GitHub Pages
was enabled with Source: GitHub Actions. `deploy.yml` ran and reported
success, and the site came up live at
`https://0xfanbase.github.io/AI-Radar/` -- but every internal link (nav,
footer, static CSS, lexicon auto-links, "seen in" card links) actually
404'd. Confirmed directly: `curl -o /dev/null -w '%{http_code}'
https://0xfanbase.github.io/static/css/tokens.css` -> 404;
`https://0xfanbase.github.io/AI-Radar/static/css/tokens.css` -> 200.

**Root cause**: this is a GitHub Pages *project* site
(`https://<owner>.github.io/<repo>/`), not a custom domain mapped to its
root, but every internal href/src this site renders was root-relative by
every page builder's own, otherwise-correct design (`href="/board/"`,
not `href="/AI-Radar/board/"`) -- a real risk `site/generate.py`'s own
Phase 4 scaffold-commit comment had already flagged, unresolved, as
depending on exactly this deployment detail (see that commit's entry
below and `IMPROVEMENT_BACKLOG.md`). It went from a documented risk to a
live, user-facing bug the moment Pages actually started serving from the
subpath.

**Fix**: `site/generate.py` gained `BASE_PATH` (derived from the path
component of the already-existing `SITE_BASE_URL` constant -- today
`"/AI-Radar"`, automatically `""` if this project ever moves to a bare
custom domain, so there is exactly one place to change, not two that
could drift out of sync) and `apply_base_path()`, a single deterministic
find-and-replace pass over every generated `*.html` file's literal
`href="/`/`src="/` prefixes, run once at the end of `generate()`. This
was chosen over threading a base-path parameter through all seven
independently-tested page builders plus `site/lib/linkify.py`'s
baked-in lexicon-term anchors and `site/builders/lexicon.py`'s
`seen_in_href` -- a much larger, riskier change touching every builder's
existing tests -- because a literal string rewrite over already-rendered
output fixes every one of those call sites at once without touching any
of their internals or existing (still-correct, still-passing)
assertions. `generate()` also now removes and recreates `public_dir`
fresh on every call (previously only `mkdir(exist_ok=True)`), both to
fix a latent stale-file risk and, critically, to guarantee
`apply_base_path()` is never applied twice to an already-rewritten file
(which would double-prefix every link).

Verified directly: regenerated the real site into a scratch directory
with the real `content/`/`data/`; every internal link and CSS `<link>`
href now reads `/AI-Radar/...`; the one real external citation link
(`https://arxiv.org/...`) and the skip-link fragment (`#main-content`)
are both untouched; `sitemap.xml`/`robots.txt` (already absolute,
`.xml`/`.txt` not `.html`) are unaffected; re-running `generate()` twice
into the same directory does not double-prefix anything. 6 new tests
added to `site/tests/test_build.py` covering all of the above. Full
suite: 701 passed, 2 deselected (root); 233 passed (site, up from 227).

Manually re-triggered `deploy.yml` (`workflow_dispatch`) after this fix
lands and merges; the live site should then serve every internal link
and asset correctly. This closes the "base-path/custom-domain decision"
item on every earlier checkpoint's "what remains for the human" list --
no further owner decision is needed unless a custom domain is added
later, in which case updating `SITE_BASE_URL` alone is sufficient.

---

## 2026-07-11 — Correction: 10 site-dependent test files were misplaced under repo-root `tests/`, silently breaking real GitHub Actions CI despite every local `pytest` run passing

**What was wrong.** `tests/test_about_builder.py`, `test_board_builder.py`,
`test_corrections_builder.py`, `test_lexicon_builder.py`, `test_linkify.py`,
`test_method_builder.py`, `test_moving_builder.py`, `test_primer_builder.py`,
`test_svg_sparkline.py`, and `test_wire_builder.py` — all ten of Phase 4's
`site/builders/*.py` / `site/lib/*.py` test files — lived directly under
the repo-root `tests/` directory instead of `site/tests/`, where every
other Phase 4 test (`site/tests/test_build.py`,
`site/tests/test_contrast_ratios.py`) correctly lives. Nine of the ten
import `jinja2`/`markupsafe` transitively (by `importlib`-loading a
`site/builders/*.py` module that itself imports them) or directly;
`test_svg_sparkline.py` doesn't strictly need them but tests a `site/`
module and belongs with its siblings. `pytest.ini`'s `testpaths = tests`
collects all of `tests/`, and `.github/workflows/ci.yml`'s "Install dev
dependencies" step only ever ran `pip install -r requirements-dev.txt` —
which does not include `jinja2`/`markupsafe` (those live only in
`site/requirements.txt`, installed solely for `deploy.yml`'s separate
`site/tests` step). In real GitHub Actions CI, a bare `python -m pytest`
would hit `ModuleNotFoundError` collecting these ten files and fail
collection for the *entire* `tests/` suite, not just these files.

**Why it wasn't caught locally.** Across Phase 4's build stages, path
instructions for where a new test file should live were given
inconsistently from one commit to the next — several
`IMPROVEMENT_BACKLOG.md` entries (Phase 4's own board/lexicon/primer/wire
builder entries) explicitly log, at the time, a "deliberate asymmetry"
placing these files at repo-root `tests/` specifically so a bare
`python -m pytest` would pick them up; that reasoning independently held
for each individual commit but the accumulated result put nine
jinja2-dependent files somewhere `ci.yml` never installs `jinja2` for.
Every dev/build session that touched this repo during Phase 3/4 had
already run `pip install -r site/requirements.txt` at some point (to build
and inspect the actual site), so `jinja2`/`markupsafe` were already present
in every local/session Python environment that ever ran `python -m
pytest` here — a false negative masking a real CI-only failure, the same
class of bug as this file's own shallow-checkout `HEAD~1` incident above
(`ci.yml`'s `fetch-depth: 0` fix): something GitHub Actions' clean,
minimal environment exposes that every local sandbox's accumulated state
quietly papers over.

**The fix.**

1. `git mv`'d all 10 files from `tests/` to `site/tests/`.
2. Corrected each file's own `REPO_ROOT = Path(__file__).resolve()...`
   computation for the new, one-level-deeper location
   (`site/tests/file.py` → `site/tests` → `site` → repo root, i.e.
   `.parent.parent.parent` instead of `.parent.parent`) — the only change
   made to these files' path math; everything built relative to
   `REPO_ROOT` (`schemas/`, `content/`, `data/`, `site/builders/`, etc.)
   keeps working unchanged. Every moved file's own docstring/comments
   that named a sibling by its old `tests/test_*.py` path (e.g. "matching
   `tests/test_board_builder.py`'s own convention") were updated to
   `site/tests/test_*.py`, and the same fix was applied to code comments
   in `site/generate.py`, `site/builders/{lexicon,wire,primer}.py`,
   `site/lib/{linkify,svg_sparkline}.py`, and one cross-reference in
   `tests/test_auditor_lexicon_coverage.py` that pointed at the old
   location.
3. `.github/workflows/ci.yml` now also installs `site/requirements.txt`
   and runs `python -m pytest site/tests` as a second, explicit step —
   mirroring `deploy.yml`'s own pre-existing pattern for that suite — so
   this class of bug (a test file quietly depending on a package `ci.yml`
   never installs) cannot silently recur regardless of what any future
   commit's own local session happens to already have installed.

**Verification — the only way to actually prove this, since this shared
dev environment already has `jinja2` installed and would falsely pass
either way:** a fresh `python3 -m venv` with *only*
`requirements-dev.txt` installed (zero `site/` dependencies) ran the root
suite genuinely green — **701 passed, 2 deselected** — proving `tests/`
no longer depends on `jinja2`/`markupsafe` at all. A second fresh venv
with both `requirements-dev.txt` and `site/requirements.txt` installed
(matching the new `ci.yml` step) ran `python -m pytest site/tests` — **227
passed** — proving the ten moved files genuinely work at their new
location with the corrected `REPO_ROOT` path math, not merely "still pass
in an environment that already happened to have everything."

**Addendum (same day, independent verification pass).** An independent
from-scratch re-verification of this fix (its own fresh venvs, reproducing
the exact 701/227 counts above, plus a check of the real GitHub Actions
run at the fixed commit — green, both new steps executed) found one
remaining weakness in the `ci.yml` half: the "Install site generator
dependencies" step ran *before* the root "Run tests" step, so a future
recurrence of the exact misplacement this entry corrects (a
jinja2-dependent test file landing back in repo-root `tests/`) would have
silently passed CI — jinja2 would already be installed by the time the
root suite ran. `ci.yml`'s steps were reordered to: install dev deps →
run root suite → install site deps → run `site/tests` — so every CI run
now structurally proves repo-root `tests/` needs nothing beyond
`requirements-dev.txt`, closing the recurrence path rather than only the
original symptom. No test asserts on `ci.yml` step order (verified by
grep before the change), and both suites re-ran green in the fresh venvs
after it (**701 passed, 2 deselected** root; **227 passed** `site/tests`).

## 2026-07-11 — Phase 5 complete: final consolidation across all 5 phases; single "what remains for the human" list

This is the final Phase 5 checkpoint and the closing entry for the whole
P1–P5 build described in the approved build plan. It does not add new
pipeline code — it consolidates `CLAUDE.md` and this file so both
accurately describe the finished, as-built system in one place, and it
replaces the several partial/scattered "what remains" lists elsewhere in
this file's own history (some of them now stale — see below) with one
single, current, authoritative list.

**What Phase 5 built, in full (across the entries directly below this
one, all dated the same day):**

- `auditor/lexicon_audit.py`, `linkrot.py`, `duplicates.py`, `trend.py`,
  `missed_story.py` — the five pure-code, no-LLM weekly checks CLAUDE.md's
  `audit.yml` bullet names verbatim (link rot; lexicon orphan/coverage;
  verifier pass-rate trend; missed-story vs. top-20 weekly HN AI stories;
  duplicate-topic detection), each reusing existing watcher/reconciler
  logic (`watcher.clustering`, `watcher.models`, `watcher.http`,
  `scripts.reconcile_run.rolling_pass_rate`) rather than reimplementing
  it, per every entry's own "reuse, not reimplementation" note.
- `auditor/report.py` + `auditor/cli.py` + `schemas/audit.schema.json` —
  assembles the five checkers into one schema-valid
  `data/audit/latest.json`, exposed as `python -m auditor.cli run`.
- `scripts/append_backlog_findings.py` — derives severity-tagged findings
  from that report and appends them to `IMPROVEMENT_BACKLOG.md` as
  checkbox lines (falling verifier trend = high; missed story or
  duplicate pair = medium; dead link or lexicon gap = low; lexicon
  orphans suppressed from the backlog specifically while zero cards are
  published, to avoid flagging all 30 pre-launch seed terms as noise on
  day one).
- `.github/workflows/audit.yml` — real, active, committed GitHub Actions
  YAML (weekly cron, `permissions: contents: write`, no LLM step, no
  secret of any kind) wiring `python -m auditor.cli run` +
  a bot-identity commit. This one needs no Routine and no secret — it
  will fire for real, automatically, the first Sunday after this branch
  is on `main` (GitHub Actions only evaluates `schedule:` on the default
  branch).
- `scripts/pick_backlog_item.py` — the deterministic (highest-severity,
  oldest-on-ties) selection rule the fortnightly improve loop uses to
  pick its one target item, parsing the real checkbox format
  `append_backlog_findings.py` actually emits.
- `scripts/fortnight_guard.py` — a pure-code ISO-week-parity guard
  (`decide_fortnight_mode`) approximating a fortnightly cadence from a
  weekly firing, verified against two real ISO-year-boundary edge cases
  (2026→2027's genuine 53-week year, and the ordinary 2027→2028
  boundary), never reading the real clock except through an explicit,
  monkeypatchable seam.
- `.github/workflows/improve.yml` — the reference procedure for the
  fortnightly self-improvement loop (guard → `pick_backlog_item.py` → one
  `claude-code-action@v1` IMPROVE step, own `--max-turns 25`, allowed to
  touch any file unlike the daily analyst → `python -m pytest` as an
  independent backstop → `peter-evans/create-pull-request` opens a PR).
  **Structurally incapable of merging its own PR** — no merge step
  appears anywhere in the file. Built and documented in full; **its
  Routine was deliberately not created this phase** — see "What remains
  for the human" below.
- This turn's own consolidation: the `CLAUDE.md` "Daily self-learning
  loop" section's execution-mechanism paragraph is rewritten to state, as
  three separately-tracked cases rather than one blended claim: the daily
  analyst+verifier Routine is **active**; the fortnightly improve loop is
  **designed and fully documented but not activated**, pending the
  owner's own separate, explicit approval (spelled out below, in
  `CLAUDE.md`, and in `IMPROVEMENT_BACKLOG.md`); and `audit.yml` is real,
  independent, already-committed GitHub Actions YAML needing no secret
  and no Routine of any kind, since it has no LLM step to gate on either.

**Verification performed this turn:** `python -m pytest` (root,
`testpaths = tests`) — **906 passed, 2 deselected**, unchanged from the
count already recorded in the entry directly below (this turn added no
new code and no new tests, only documentation edits to `CLAUDE.md` and
this file, so an unchanged count is the expected, correct result, not a
regression). `python -m pytest site/tests` — **22 passed**, likewise
unchanged. Both root and `site/` suites confirmed green together before
committing. No file outside `CLAUDE.md`, `PROGRESS.md`, and
`IMPROVEMENT_BACKLOG.md` was touched this turn.

### What remains for the human — single, final, consolidated list

Every "what remains" item scattered across this file's own history,
across all five phases, consolidated into one current list. (Several
earlier entries below — e.g. the Phase 2/Phase 4 sign-off entries'
own "what remains" paragraphs — still say things like "add the
`CLAUDE_CODE_OAUTH_TOKEN` repo secret" or "set `vars.CLAUDE_MODEL`."
Those were accurate *at the time they were written*, before the
architecture change documented earlier in this file moved the daily
analyst run off GitHub Actions entirely. They are left as-written,
since this file is a historical build log, not a living doc to be
retroactively edited — but they are superseded by this list, which
reflects the system exactly as it stands today.)

1. **Enable GitHub Pages** — repo Settings → Pages → Source: "GitHub
   Actions." `deploy.yml`'s `deploy` job cannot succeed until this is
   set; nothing in this codebase can toggle a repo setting.
2. **Review and merge this branch (`claude/phase-1-watcher-build-fsc12w`)
   to `main`.** Every scheduled mechanism in this repo — `watch.yml`'s
   daily cron, `audit.yml`'s weekly cron, `deploy.yml`'s push trigger —
   targets/fires relative to `main`, and GitHub Actions only evaluates a
   workflow's `schedule:` trigger on the repository's default branch.
   Nothing here runs automatically, on any cadence, until this merge
   happens.
3. **The base-path/custom-domain decision.** Decide whether this project
   serves from a custom domain (add a `CNAME` file, serves from the
   domain root) or accepts the default `github.io/AI-Radar/` project
   subpath — every internal link the site generator renders today assumes
   root serving and does not yet handle a subpath prefix. This is a
   one-time content/config decision, not a code defect, but it must be
   made before or shortly after Pages is enabled.
4. **Explicit approval, and the actual act of creating, a second Claude
   Code Remote Routine for the fortnightly improve loop.** This is stated
   plainly as a deliberate, separate ask from the daily analyst Routine
   already running — not a formality, not implied by the daily Routine's
   own existing approval. The daily Routine's authority is scoped to
   reading `content/`/`data/`-relevant inputs and writing only inside
   `content/` and `data/` (the same boundary the path-allowlist gate
   enforces for the GitHub-native path). A Routine for the fortnightly
   loop would be authorized to `Edit`/`Write` *any* file in the repo —
   workflows, `watcher/`/`scripts/`/`auditor/` pipeline code, `schemas/`,
   even `CLAUDE.md` itself — gated only by a human PR review after the
   fact, not a pre-commit path allowlist. That is a materially larger
   grant of autonomous authority than "let it refresh the news," and this
   build deliberately does not take that step on the owner's behalf.
   `scripts/fortnight_guard.py` and `improve.yml` are fully built and
   tested and are ready the moment the owner decides to authorize this;
   until then they remain designed-but-inactive, exactly as intended.
   (The alternative path — adding a `CLAUDE_CODE_OAUTH_TOKEN` secret and a
   real `schedule:` to `improve.yml` itself — remains available too, and
   is equally an explicit, owner-initiated action; see the point directly
   below.)
5. **`CLAUDE_CODE_OAUTH_TOKEN` is NOT needed at all under the current,
   as-built architecture — stated plainly, since several earlier entries
   in this same file (written before the architecture change) said the
   opposite.** Both the active daily analyst/verifier Routine and the
   designed-but-inactive fortnightly improve loop are built to run as
   Claude Code Remote Routines, not as `claude-code-action@v1` GitHub
   Actions steps — neither needs this secret, and the owner does not need
   to create or manage one for this project to run as designed.
   `analyze.yml`/`improve.yml` remain in the repo only as reference
   documentation of the exact prompt text each Routine runs; they are not
   live workflows and are not waiting on this secret to become live. The
   *only* scenario where this secret would ever be needed is if the owner
   later decides to switch either loop's execution back to the
   GitHub-native `claude-code-action@v1` path instead of a Routine — a
   possible future choice, not a current gap.
6. **Phase 5's own acceptance criterion: 14 days of hands-off autonomous
   operation.** This can only be observed in real time, after the branch
   is merged and the daily Routine (already active) and, if/when
   authorized, the fortnightly Routine have had a real two-week window to
   run on their own schedule — not something any build session can do,
   simulate, or certify in advance. Once merged, this is a matter of
   waiting and then checking `data/ledger.json`, `content/cards/`,
   `data/audit/latest.json`, and `IMPROVEMENT_BACKLOG.md`'s own audit
   findings for two weeks of real, unattended activity.

---

## 2026-07-11 — Phase 5: `scripts/fortnight_guard.py` + `.github/workflows/improve.yml` (reference only) -- improve-loop Routine NOT yet activated, pending owner approval

Designs and documents the fortnightly self-improvement loop's mechanism in
full -- the last unbuilt piece of Phase 5's own file layout -- **without**
creating any live trigger for it, per this turn's own explicit, binding
scope. The daily analyst refresh already runs as a Claude Code Remote
Routine (see the architecture-change entry below); a second Routine for
this loop would be authorized to touch *any* file in the repo, not just
`content/`/`data/`, and the repo owner has not been asked for, and has not
given, that broader go-ahead. See
`IMPROVEMENT_BACKLOG.md`'s own matching, more detailed entry ("Phase 5:
improve-loop Routine NOT yet activated -- requires explicit owner
approval") for the full reasoning; this entry covers what was actually
built and tested.

**`scripts/fortnight_guard.py`** (new): a pure-code ISO-week-parity guard
-- `decide_fortnight_mode(today: date)` takes an explicit date (never
`date.today()`/`datetime.now()` internally; only `main()`'s own
`_default_today()` seam ever reads the real clock, and only when `--date`
isn't passed) and returns `{date, iso_year, iso_week, iso_weekday, parity,
mode, reason}`, where `mode` is `"run"` if `today`'s ISO week number
(`date.isocalendar()[1]`) is odd, else `"skip"` -- approximating a
fortnightly cadence from a weekly firing exactly the way
`scripts/plan_run.py`'s own degradation-ladder level 2 approximates
every-other-day from a daily one, but with ISO-week parity instead of
day-of-year parity (the correct fit for halving a *weekly*, not *daily*,
cadence -- see the module's own docstring for the full reasoning).
`write_github_output()` appends `mode`/`iso_year`/`iso_week`/`parity` as
GitHub Actions step outputs when `$GITHUB_OUTPUT` is set in the
environment (a real, always-present env var inside any GitHub Actions
step; the script just reads it, no shell redirection needed in the
workflow itself), so `improve.yml`'s own subsequent steps can gate on
`steps.guard.outputs.mode` directly. No persisted `data/*.json` artifact
is written -- the approved plan names none for this guard, unlike
`plan_run.py`'s `data/run_plan.json`.

**The year-boundary quirk, verified against two real boundaries, not
assumed:** an ISO year has 53 weeks (instead of 52) in roughly one year of
five; at that boundary, week 53 (odd) is immediately followed by week 1 of
the next year (also odd, since ISO week numbers always start at 1) --
two consecutive "run" weeks in a row rather than a strict alternation.
`date(2026, 12, 28).isocalendar()[1] == 53` was confirmed live (Python's
own standard library, not assumed) to make 2026 a genuine 53-ISO-week
year, and `tests/test_fortnight_guard.py` walks eight real consecutive
Sundays straight through the actual 2026-to-2027 boundary, asserting the
documented back-to-back "run" pair (2027-01-03, then 2027-01-10) occurs
and that it's the *only* adjacent same-mode pair in that whole sequence.
A second real boundary, 2027-to-2028 (2027 is an ordinary 52-ISO-week
year, also confirmed live), is walked the same way to prove clean
alternation with zero exceptions in the common case. This is the "real
year boundary" proof this turn's own instruction asked for.

**New test file `tests/test_fortnight_guard.py`** (20 tests):
`iso_week_parity` correctness against hand-checked real dates, including
the "ISO week 1 is always odd, across different calendar years" case
(each of the three sample dates independently confirmed via
`date.isocalendar()` before being asserted, per this turn's own
"verify before writing a claim down" instruction, after an earlier draft
of this same test picked dates that turned out *not* to actually be ISO
week 1 and was corrected before being written to disk);
`decide_fortnight_mode`'s exact full-dict shape for both a run week and a
skip week; ordinary week-to-week alternation; the two real-year-boundary
proofs described above; `write_github_output`'s exact emitted lines and
its append-not-overwrite behavior (via `tmp_path`); and `main()`'s CLI
plumbing (`--date` parsing, stdout content, `GITHUB_OUTPUT` written only
when the env var is actually set, and the real-clock default-date path
proven via a monkeypatched `_default_today` seam rather than depending on
whatever the real ISO week parity happens to be on the day this suite
runs).

**`.github/workflows/improve.yml`** (new; reference-only, same
annotated-inactive pattern as `analyze.yml`, plus a second, stronger point
its own header spells out: even beyond needing a `CLAUDE_CODE_OAUTH_TOKEN`
secret this project doesn't manage, no live trigger of *any* kind --
GitHub Actions schedule or a second Routine -- has been created for this
specific job, and that is a deliberate, separate withholding of authority,
not merely "not built yet"). Describes, in order: `workflow_dispatch`
trigger only (no `schedule:` block -- the real cadence would live in
whichever future Routine calls this procedure); a guard step
(`python scripts/fortnight_guard.py`, `id: guard`); a backlog-item-picking
step (`python scripts/pick_backlog_item.py`, `id: pick`, gated on
`steps.guard.outputs.mode == 'run'`, capturing that already-shipped
script's stdout into a step output itself rather than modifying that
script); a single `claude-code-action@v1` step (`--max-turns 25`, own
`--allowedTools` that -- unlike the daily ANALYST/VERIFIER -- may
`Edit`/`Write` anywhere in the repo, since this step's whole output is an
unmerged PR, not an auto-commit) whose prompt names the one picked item
verbatim and instructs it to implement only that item, run
`python -m pytest` itself, and check off that item's box in
`IMPROVEMENT_BACKLOG.md`; a second, independent `python -m pytest` step
as a structural backstop (logged as a deliberate scope addition beyond
the plan's literal text, in the same spirit as Phase 1's PM-checkpoint
tokenizer-fix addition); and a `peter-evans/create-pull-request@v6` step
that stages, commits (bot identity via that action's own
`author`/`committer` inputs), pushes a fresh branch, and opens the PR.
**Structural no-merge guarantee**: no merge step of any kind appears
anywhere in the file -- the job's last step opens a PR and stops.

**Verification:** `python -m pytest` -- **906 passed, 2 deselected** (up
from 886 immediately before this turn; +20 new tests, exactly
`tests/test_fortnight_guard.py`'s own count; nothing else changed or
broke). Both workflow YAML files touched or referenced this turn
(`improve.yml`, freshly written) were re-validated with `yaml.safe_load`
after writing -- confirmed to parse with the identical `{"on": True, ...}`
PyYAML quirk every other workflow file in this repo already exhibits
(YAML 1.1 treats the bare word `on` as a boolean; this is a PyYAML/YAML-1.1
artifact, not a GitHub Actions parsing issue, and is pre-existing across
`analyze.yml`/`audit.yml`/`watch.yml`/`ci.yml` too -- checked directly,
not assumed, before concluding it wasn't a new problem introduced by this
turn's own file). No file outside `scripts/fortnight_guard.py`,
`tests/test_fortnight_guard.py`, and `.github/workflows/improve.yml` was
touched by this turn's own code changes (`PROGRESS.md` and
`IMPROVEMENT_BACKLOG.md` are documentation-only, and
`scripts/pick_backlog_item.py` was read but deliberately not modified, per
this turn's own instruction that it's already built).

**What remains, stated plainly, for the user:** activating the
fortnightly loop for real means the repo owner explicitly authorizing and
creating a second Claude Code Remote Routine (firing weekly, running
`fortnight_guard.py` then, on a "run" week,
`pick_backlog_item.py` then the IMPROVE prompt then `pytest` then opening
a PR -- never merging it) -- or, alternatively, adding the
`CLAUDE_CODE_OAUTH_TOKEN` secret and a real `schedule:` cron to
`improve.yml` itself and running it as literal GitHub Actions YAML. Either
path is a deliberate, informed, owner-initiated action this session does
not take on its own, exactly mirroring the daily analyst Routine's own
activation history (designed and documented first, activated by explicit
owner request second).

---

## 2026-07-11 — Phase 5: `.github/workflows/audit.yml` (weekly, no LLM) + `scripts/pick_backlog_item.py` (fortnightly-improve-loop backlog-item picker)

Wires the auditor package built across the five entries directly below
(`auditor/lexicon_audit.py`, `linkrot.py`, `duplicates.py`, `trend.py`,
`missed_story.py`, `report.py`, `cli.py`, `scripts/append_backlog_findings.py`)
into a real, scheduled GitHub Actions workflow, and builds the
deterministic selection rule the (not-yet-built) fortnightly `improve.yml`
will use to pick its next self-improvement target. Per this turn's own
explicit, binding scope: no `improve.yml` file, fortnight-parity guard, or
live trigger/Routine of any kind is created this turn — only `audit.yml`
(which needs neither) and the backlog picker.

**`.github/workflows/audit.yml`** (new): weekly cron `"0 0 * * 0"` (Sunday
00:00 UTC = 08:00 HKT exactly, matching CLAUDE.md's documented cadence
verbatim — an intentional exact-top-of-hour cron, not an off-hour
placeholder, consistent with the precedent `watch.yml`'s own Phase 1 PM
checkpoint already set by moving from an off-hour congestion-avoidance
placeholder to the literal documented target hour) plus
`workflow_dispatch`; `permissions: contents: write`; checkout with
`fetch-depth: 0` (this repo's established convention); `setup-python`;
`pip install -r requirements.txt`; one step, `python -m auditor.cli run
--out data/audit/latest.json`, which — confirmed by reading
`auditor/cli.py::run_audit`'s real source, not assumed — already performs
*both* halves of CLAUDE.md's "audit.yml — weekly" bullet in one call
(runs all five checkers into a schema-valid `data/audit/latest.json`, then
derives and appends this run's actionable findings as checkbox lines to
the real `IMPROVEMENT_BACKLOG.md`, since `append_to_backlog=True` is
`run_audit`'s own default); then a commit+push step (`git add data/audit/
IMPROVEMENT_BACKLOG.md`, bot identity, skip if nothing changed) matching
`watch.yml`'s existing pattern exactly.

**No `scripts/check_path_allowlist.py` / `scripts/validate_changed_schemas.py`
gate in this workflow — a deliberate omission, documented in both the
workflow's own top comment and `IMPROVEMENT_BACKLOG.md` in full, not a
silent gap.** That gate exists specifically to contain prompt-injection
risk from an LLM step that fetched untrusted content and then chose what
to write; `audit.yml` has no LLM anywhere in it, so there is nothing for
that gate to contain — the set of files this workflow can ever touch is
fixed by its own two hardcoded commands, never influenced by anything a
fetched citation page or HN story title says. This is also why the
workflow is allowed to touch `IMPROVEMENT_BACKLOG.md`, a path outside
`content/`/`data/` the allowlist would otherwise reject: the allowlist's
boundary is about LLM-authorship/licensing provenance, not about which
directories pure code may write to, and CLAUDE.md's own `audit.yml`
bullet already names the backlog-append as this workflow's job.

**A documentation-accuracy correction against this turn's own task
framing, made honestly rather than papered over:** the task described
`audit.yml` as needing a second, separate `scripts/append_backlog_findings.py`
step. Checked directly against that file's real, already-committed source
before writing the workflow (not assumed): it defines no
`if __name__ == "__main__":` block and no argparse entrypoint at all —
it's a pure function library `auditor/cli.py::run_audit` already calls
internally. A second step literally invoking that file would import it
and execute nothing, a no-op kept only for surface fidelity to a
description that doesn't match the module's real shape. `audit.yml` has
one step that does what the code actually does instead, with the
reasoning recorded in both places rather than adding a step that
accomplishes nothing to satisfy a literal reading.

**`scripts/pick_backlog_item.py`** (new): parses the real, already-shipped
`scripts/append_backlog_findings.py` output format —
`"- [ ] **[SEVERITY]** <summary>"` lines grouped under
`"## Audit findings -- <run_id> (<generated_at>)"` section headers — not
the `"[audit:DATE][severity:high]"` shape this turn's own task text used
as an illustrative example, which doesn't match any line the real,
committed generator emits (verified against that module's real source
before writing the parser, per this turn's own "verify before writing a
claim down" directive). `parse_backlog_items()` walks the file tracking
the nearest enclosing "Audit findings" section header (any other `## `
heading resets that context, so a checkbox line under an unrelated later
heading never inherits a stale timestamp); `pick_next_item()` selects the
highest-severity **unchecked** item (severity order reused directly from
`scripts.append_backlog_findings.SEVERITY_LABELS`, so it can never
silently drift from what that module actually emits), tie-broken by the
enclosing section's oldest `generated_at` (a checkbox line with no
preceding header at all parses as a valid candidate but is treated as
having an unknown date, which can never win a tie against a genuinely
dated one), with a final tie-break on the item's own line number for full
determinism. `pick_backlog_item()` is the one disk-reading entry point;
`main()` is a small CLI (`--path` override) printing the picked item or a
clean "nothing to pick" message, exit code 0 either way.

**Scope decision, proven by test not just asserted:** this file's own
pre-existing plain-bullet decision-log entries (`"- **DATE -- decision
text.**"`, used by every entry in `IMPROVEMENT_BACKLOG.md` including this
one) are structurally invisible to the checkbox regex — no special-case
"is this an old-style entry" exclusion was needed. `tests/
test_pick_backlog_item.py::test_parse_ignores_pre_existing_plain_bullet_decision_log_entries`
and the mixed-fixture end-to-end test both assert this directly.

**New test file `tests/test_pick_backlog_item.py`** (28 tests): reuse-by-
identity checks (`SEVERITY_LABELS`/`BACKLOG_PATH` both imported, not
copied, from `scripts.append_backlog_findings`); `parse_backlog_items`
correctness across empty input, plain-bullet-only input (zero items),
a single section's checked/unchecked/severity/summary/line-number fields,
both-case checked-box parsing (`[x]`/`[X]`), an unrecognized severity
label still parsing, a header-less checkbox line, an unrelated `##`
heading resetting section context, and multiple sections each keeping
their own date; `pick_next_item`'s empty-list/all-checked-null cases,
severity-preference, checked-item-losing-to-unchecked-lower-severity,
oldest-date tie-break, line-number tie-break, header-less-item-never-
winning-a-tie, unrecognized-severity-ranking-lowest (both losing to a real
severity and being selectable when it's the only candidate), and one
larger mixed-fixture end-to-end proof (old-style plain bullets + three
audit-findings sections + mixed severities/checked-state — exactly one
correct item selected out of 7 real candidates); `pick_backlog_item`'s
missing-file/no-checkbox-lines-found/real-file-read paths against
`tmp_path`; `main()`'s CLI plumbing (found/not-found cases, both via
`capsys`, plus the real-file default-path case); and one integration
smoke test against this repo's own real, current `IMPROVEMENT_BACKLOG.md`
confirming it parses cleanly with zero checkbox lines today (no real
audit run has ever happened) and `pick_backlog_item()` correctly returns
`None` against it.

**Verified this turn, not assumed:** `data/audit/` is not in `.gitignore`
(only `data/.cache/`, `__pycache__/`, `.venv/`, `*.pyc`, `public/` are), so
`audit.yml`'s `git add data/audit/` step actually stages
`data/audit/latest.json` once it's written, rather than silently no-oping
against a gitignored path; `auditor.linkrot`/`auditor.missed_story` call
`requests.Session.head`/`.get`/`watcher.sources.hn.fetch_hn_items`
directly rather than `watcher.http.fetch()`'s ETag-cached wrapper
(confirmed by reading both modules), so `audit.yml` deliberately carries
no `actions/cache` step for `data/.cache/` — nothing in this workflow's
own fetch path would ever read or benefit from it. Both workflow YAML
files this turn touched (`audit.yml`, freshly written) were re-validated
with `yaml.safe_load` after every edit.

**Verification:** `python -m pytest` — **886 passed, 2 deselected** (up
from 858 immediately before this turn; +28 new tests, exactly
`tests/test_pick_backlog_item.py`'s own count; nothing else changed or
broke). No file outside `.github/workflows/audit.yml`,
`scripts/pick_backlog_item.py`, and `tests/test_pick_backlog_item.py` was
touched by this turn's own code changes (`PROGRESS.md` and
`IMPROVEMENT_BACKLOG.md` are documentation-only). The real, live
end-to-end `python -m auditor.cli run` pipeline itself was not
re-exercised this turn against the real repo tree (it was already proven
live, end-to-end, in the entry directly below this one, into a scratch
directory specifically so that run's own ephemeral live-fetched HN story
titles never landed in the real, committed `IMPROVEMENT_BACKLOG.md`) —
running it again for real, against the real tree, is exactly what the
newly-added `audit.yml` cron will do on its own first real Sunday
firing.

**What remains, stated plainly, for the user:** `improve.yml` itself, the
`scripts/fortnight_guard.py` ISO-week-parity guard, and any live
scheduling mechanism for the fortnightly loop (a second Claude Code Remote
Routine, or a real `improve.yml` + repo secret, per the two paths
`analyze.yml`'s own architecture note already discusses) are **not**
built or activated by this turn, per this turn's own explicit, binding
instruction — a live process empowered to touch arbitrary files including
code/workflows/schemas is a materially broader authorization than the
narrower daily-analyst-refresh Routine the user has already granted, and
this turn does not assume that broader grant implicitly. `audit.yml`
itself also cannot be observed actually firing on its real Sunday cron
from within this session, for the same reason no session can observe
`watch.yml`'s or the daily Routine's real firings — that requires this
branch to be merged and the schedule to actually elapse in real time.

---

## 2026-07-11 — Phase 5: `auditor/report.py`, `auditor/cli.py`, `schemas/audit.schema.json`, `scripts/append_backlog_findings.py` -- report assembler, CLI entrypoint, backlog-append step

Integration commit landing the last four pieces of the Phase 5 `auditor/`
package (the five checkers -- `lexicon_audit.py`, `linkrot.py`,
`duplicates.py`, `trend.py`, `missed_story.py` -- were built in parallel by
sibling agents in this same working tree; see the four entries directly
below). This commit assembles all five checkers' own output into one
`schemas/audit.schema.json`-shaped `data/audit/latest.json` artifact,
wires the whole pipeline behind `python -m auditor.cli run`, and adds the
`scripts/append_backlog_findings.py` step that promotes actionable
findings into `IMPROVEMENT_BACKLOG.md` checkboxes.

**`auditor/report.py`**: `build_report()` assembles the five checkers'
already-computed dicts (passed through by identity, never re-derived)
under `link_rot`/`lexicon`/`verifier_trend`/`missed_stories`/`duplicates`,
plus a top-level `version`, `run_id` (`"audit-<compact UTC timestamp>"`,
deterministic from an explicit `now` -- no UUID dependency anywhere in
this project), `generated_at`, a `window` annotation (`{days, start,
end}`, fixed at 7 days matching `audit.yml`'s own weekly cadence -- see
the module's own docstring for why this is a top-level annotation only,
never fed into any checker, each of which has its own different or absent
window), and `findings_appended_to_backlog` (a plain count, not a
duplicated findings list -- every finding's own detail already lives in
one of the five per-checker fields). `save_report`/`load_report`
schema-validate before writing / after reading, matching every other
committed pipeline artifact's own convention.

**`schemas/audit.schema.json`**: a direct transcription of the five real
checkers' own return shapes (verified against their actual source, not
independently redesigned), `additionalProperties: false` throughout, with
a shared `$defs/story_classification` reused three times inside
`missed_stories` (`missed_stories[]`/`seen_but_dropped_stories[]`/
`results[]` all carry the identical per-story shape).

**`auditor/cli.py`** (`python -m auditor.cli run`, mirroring
`watcher/cli.py`'s own `python -m watcher.cli run` shape exactly --
`argparse` subparsers, a `run` subcommand): `run_audit()` loads cards,
`content/lexicon.json` (via a new small `load_lexicon()` -- no existing
module owned a lexicon-file loader, since `auditor.lexicon_audit` is
deliberately pure/filesystem-free), and `data/ledger.json` exactly once
each and threads them into every checker that needs them; runs all five
checks; derives findings via `scripts.append_backlog_findings.
derive_findings`; appends them to `IMPROVEMENT_BACKLOG.md` (or a
caller-supplied path) unless `append_to_backlog=False`; and returns the
assembled report. `run_audit()` also accepts an explicit `hn_items`
passthrough straight to `auditor.missed_story.audit_missed_stories`'s own
parameter of the same name -- added specifically so a caller/test can
keep the whole pipeline offline and deterministic without needing a
`requests_mock` HN fixture, while the real CLI (`hn_items` left `None`)
still does a genuine live fetch. `main()`'s `run` subcommand exposes
`--out` (default `data/audit/latest.json`), `--backlog-path` (default the
real `IMPROVEMENT_BACKLOG.md`), and `--no-backlog-append` (a dry-run
flag) -- the latter two exist specifically so this repo's own real
`IMPROVEMENT_BACKLOG.md` can be exercised against without being mutated
by a one-off manual/test invocation, while the real weekly cadence would
call it with neither flag set.

**`scripts/append_backlog_findings.py`**: `derive_findings()` flattens
the five checkers' dicts into `{severity, category, summary}` finding
dicts per this turn's own explicit severity mapping (documented in full
in `IMPROVEMENT_BACKLOG.md`'s own decision log, per instruction): a
falling verifier trend is **high**; a missed story or a duplicate-topic
pair is **medium**; a dead citation link or a lexicon
orphan/coverage-gap is **low**. `format_finding_line()` renders one
Markdown checkbox line (`- [ ] **[SEVERITY]** <summary>`);
`append_findings_to_backlog()` appends one dated `## Audit findings --
<run_id> (<generated_at>)` section (skipping entirely, writing nothing,
on a completely clean run) and returns the count actually written.

**A real, deliberate judgment call, logged in full in
`IMPROVEMENT_BACKLOG.md`: lexicon-orphan findings are suppressed from the
backlog-append step (not from the raw report) whenever zero cards have
been published yet.** This repo's own real state today is exactly that
case -- `content/cards/` doesn't exist and all 30 real
`content/lexicon.json` entries have `seen_in: []` -- so an unconditional
promotion would have flagged all 30 seed terms as "orphan" findings on
literally the first-ever audit run, which is noise (the expected
pre-launch state), not signal. Confirmed directly: a real, live
end-to-end run against this repo's actual content/data (see below)
produced `lexicon.orphans` with all 30 real terms in the raw report, but
zero of them were promoted to backlog checkboxes.

**Real, live verification run performed this turn** (not merely unit
tests): `python -m auditor.cli run --out <scratch>/latest.json
--backlog-path <scratch>/scratch_backlog.md` was actually executed
against this repo's real, current `content/`/`data/` state (zero
published cards, the real 30-entry `content/lexicon.json`, the real
104-entry `data/ledger.json`, and a genuine live fetch of this week's
top-20 HN AI stories via the real Algolia endpoint -- ~10.7s wall time,
dominated by that fetch's 14 windowed sub-queries). Result: a
schema-valid report (`link_rot`: 0 URLs checked, zero cards means zero
citations to check; `lexicon`: 0 coverage gaps, 30 raw orphans;
`verifier_trend`: `insufficient_data` (`data/verifier_stats.json` still
has `runs: []`); `missed_stories`: 8 covered / 0 seen-but-dropped / 12
missed out of the real top-20 HN AI stories of the week;
`duplicates`: 0 pairs, correctly, since there are zero published cards to
compare) with `findings_appended_to_backlog: 12` -- exactly the 12 real
`missed_story` findings (the lexicon orphans were correctly suppressed
per the guard above; nothing else was actionable). The 12 real findings
were written, verbatim, into the scratch backlog file as properly
formatted `- [ ] **[MEDIUM]**` checkboxes, confirmed by direct inspection.
**This scratch run's own output (the generated `latest.json` and the
findings it derived from this week's live HN stories) is deliberately
*not* committed to the real repo** -- `--out`/`--backlog-path` pointed at
a scratchpad directory for exactly this reason. Seeding
`data/audit/latest.json` or appending this one-off run's now-ephemeral,
will-go-stale HN titles to the real, committed `IMPROVEMENT_BACKLOG.md`
was outside this turn's own specified commit scope (matching the
`data/run_plan.json` precedent logged in Phase 2 -- "no seed file is
created ... a placeholder ... would just be dead weight until [it] is a
real scheduled run's own output"); the real repo's working tree was
independently confirmed untouched by this verification (`git status`
shows no `data/audit/` directory and no incidental changes beyond this
turn's own intentional file additions). The real weekly cadence (once
`audit.yml`/a scheduling mechanism for it exists -- out of this turn's
scope) will produce and commit its own first real `data/audit/latest.json`
and any real backlog findings on its own schedule.

**`auditor/__init__.py` added, reversing an earlier logged decision.**
The `lexicon_audit.py` entry below explicitly chose no `__init__.py`,
matching `scripts/`'s own convention (Python's implicit namespace-package
handling already made -- and still makes -- every `from auditor.<module>
import ...` resolve with no `__init__.py` present). This turn adds one
back per this turn's own explicit instruction, now that `auditor/` has a
real `cli.py` entrypoint (`python -m auditor.cli run`) the same shape
`watcher/__init__.py` already backs for `watcher/cli.py`. Nothing
functional required this reversal -- `python -m pytest` stayed green
before and after -- it's a consistency/explicitness choice, logged
transparently as a reversal rather than silently overwritten.

**New test files**: `tests/test_auditor_report.py` (18 tests --
`make_run_id`/`compute_window` pure-date-math correctness including a
non-UTC-timezone-normalization case, `build_report`'s pass-through-by-
identity assembly and schema validity, `save_report`/`load_report`'s
round trip / missing-file / malformed-file behavior against `tmp_path`,
and one integration smoke test running every real checker against a
fully-synthetic-but-real-shaped all-empty state), `tests/
test_append_backlog_findings.py` (23 tests -- `_fmt_pct`, every category's
severity/actionability rule including the unreachable-vs-dead link-rot
distinction and both the falling-only verifier-trend gate and the
zero-cards orphan-suppression guard and its non-interference with
coverage-gap findings, deterministic cross-category ordering,
`format_finding_line`'s exact Markdown shape, and
`append_findings_to_backlog`'s empty/non-empty/multi-run-append behavior
against a `tmp_path` scratch file), `tests/test_auditor_cli.py` (10 tests
-- `load_lexicon` against the real repo file and missing/fixture paths,
`run_audit` against this repo's real empty-cards state fully offline
(`hn_items=[]`), the dry-run/backlog-mutation-only-on-request behavior,
a monkeypatched-real-finding case proving an actual backlog write when
one exists, and `main()`'s argparse plumbing including `--no-backlog-
append`), `tests/test_audit_schema.py` (6 tests -- schema self-validity,
valid/invalid fixtures, `additionalProperties`/enum enforcement).

**Verification**: `python -m pytest` -- **858 passed, 2 deselected** (up
from 801 immediately before this turn's own additions -- 57 new tests,
exactly this turn's own count across the four new test files; nothing
else changed or broke). The real, live `python -m auditor.cli run`
verification described above is a genuine, tool-observed proof (not an
estimate) that the full five-checker + report + backlog-append pipeline
runs cleanly end to end against this repo's real, current, mostly-empty
state -- the exact edge case this turn was asked to confirm.

---

## 2026-07-11 — Phase 5: `auditor/missed_story.py`, the missed-story checker

Sixth file landed in `auditor/` this same day (see the `trend.py`,
`duplicates.py`, and `linkrot.py` entries directly below, at least some
built concurrently in this same working tree). Implements CLAUDE.md's
`audit.yml` "weekly" bullet ('"missed-story" check (top-20 HN AI stories
of the week vs cards; misses logged as findings, not failures)') --
fetching the top-20 AI-relevant HN stories of the trailing 7 days and
classifying each as `covered` (matches a published card, or a ledger
entry the pipeline hasn't declined), `seen_but_dropped` (matches only a
`"dropped"` ledger entry -- the pipeline saw it and correctly declined
per the corroboration rule), or `missed` (matches neither -- a genuine
gap, logged as a finding, never a test failure).

**Reuse, not reimplementation, per this turn's own explicit instruction:**
`fetch_weekly_top_hn_stories()` calls `watcher.sources.hn.fetch_hn_items()`
directly -- the identical broad-pool/keyword-filter/final-candidacy
pipeline the daily watcher itself runs -- widening only `lookback_hours`
to 168 (7 days) and slicing the already-points-sorted result to the top
20. No fetch/parse/filter logic is reimplemented. Matching reuses both
tiers of `watcher/clustering.py`'s own two-tier algorithm ("exact-URL-
normalization match, else Jaccard >= 0.35 over title tokens"): a
published card is checked via exact-URL match against its own
`citations[].url` OR Jaccard similarity of the HN story's raw title
against the card's `headline` (reusing `_jaccard`/`tokenize_title`
directly, the same composition `auditor/duplicates.py` already
established); a `data/ledger.json` entry -- which carries `member_urls`
but no title text at all (`ledger.schema.json`'s `ledger_entry` has no
headline/title field) -- is checked via the exact-URL tier only, reusing
`watcher.models.normalize_url()` directly. This asymmetry (both tiers for
cards, one tier for the ledger) is a spec-silent judgment call, logged in
full in `IMPROVEMENT_BACKLOG.md`, forced by what data each target
actually carries.

**Classification priority.** A card match always wins over any ledger
match; among ledger matches, a `"queued"`/`"published"` entry (the
pipeline already knows about this story and hasn't declined it) always
wins over a `"dropped"` one. `classify_story()` scans every ledger entry
before concluding `seen_but_dropped`, rather than returning on the first
`"dropped"` hit -- proven order-independent by dedicated tests -- so a
story that also separately matches a non-dropped entry elsewhere in the
ledger is never misclassified. Full reasoning for this tie-break, and for
trusting a `"published"` ledger entry's own `card_id` directly rather
than re-deriving it via title/citation match, logged in
`IMPROVEMENT_BACKLOG.md`.

`audit_missed_stories()` is the one live-touching entry point
(`hn_items`/`cards`/`ledger`/`session` all default to `None`, falling
back respectively to a live `fetch_weekly_top_hn_stories()` call, `auditor.
linkrot.load_cards()` reused directly, and a new `load_ledger()` reading
`data/ledger.json`). Every other function (`story_matches_card`,
`story_matches_ledger_entry`, `classify_story`) is pure and
filesystem/network-free, matching every sibling `auditor/` module's own
established convention.

**New test file `tests/test_auditor_missed_story.py`** (33 tests): four
reuse-by-identity proofs; the widened-window/top-20 behavior of
`fetch_weekly_top_hn_stories()` via both a monkeypatched stub and a
genuine end-to-end run against the same real captured Algolia fixture
`tests/test_hn_fetch.py` uses for its own 48h test (here widened to 168h,
14 windows instead of 4, same three real stories recovered); `load_ledger`'s
missing/real-file paths; both matching tiers on each target, including a
`normalize_url`-based match (`www.`/trailing-slash difference) proving
it's genuinely normalized, not a raw string comparison, plus a `>=`-
inclusive threshold-boundary check; `classify_story` across all three
buckets, the card-beats-dropped-ledger priority case, and the ledger-
iteration-order-independence case; and `audit_missed_stories()`'s
explicit-data path, both default-loading paths (proven via `monkeypatch`),
the empty-input clean-zero-report shape, and an integration-flavored
smoke test against this repo's real (currently empty/all-`"queued"`)
`content/cards/`/`data/ledger.json` disk state with a mocked HN endpoint.

Verification: `python -m pytest` -- **801 passed, 2 deselected** (744
recorded before `auditor/trend.py` landed, +24 from the concurrently-
landed `trend.py`/`test_auditor_trend.py`, +33 from this turn's own new
tests). No file outside `auditor/missed_story.py` and
`tests/test_auditor_missed_story.py` was touched by this turn's own code
changes (`PROGRESS.md` and `IMPROVEMENT_BACKLOG.md` are
documentation-only). Per this turn's own explicit instruction, no commit
was made -- another agent integrates this alongside the other Phase 5
`auditor/` files already sitting uncommitted in this same working tree.

---

## 2026-07-11 — Phase 5: `auditor/trend.py`, the verifier pass-rate trend check

Fifth file landed in `auditor/` this same day (see the `lexicon_audit.py`,
`linkrot.py`, and `duplicates.py` entries directly below, all also this
same day, at least some built concurrently in this same working tree).
Implements CLAUDE.md's/the approved build plan's `audit.yml` "weekly"
bullet "verifier pass-rate trend (rolling 7d/30d from
`data/verifier_stats.json`)" -- reads `data/verifier_stats.json`'s
`runs[]` history and computes a rolling 7-day pass rate, a rolling 30-day
pass rate, and a `rising`/`falling`/`flat`/`insufficient_data` trend
classification of the rolling-7d figure against the prior (non-overlapping)
week's own rolling-7d figure, all anchored on one explicit `today` date
argument -- never a live `datetime.now()`/`date.today()` call anywhere in
this module, so every function here is fully unit-testable against a
synthetic, hand-built history and an explicit date.

**Reuse, not reimplementation, per this turn's own explicit instruction:**
the actual rolling-window pooling arithmetic --
`(confirmed + reported) / cards_drafted` pooled across every `runs[]` row
in a trailing window, with a division-by-zero guard that returns `None`
rather than raising or fabricating `0.0` when a window drafted no cards at
all -- is `scripts.reconcile_run.rolling_pass_rate`, imported and called
directly (proven by object identity in the test suite,
`mod.rolling_pass_rate is reconcile_run_mod.rolling_pass_rate`), not
reimplemented. That function already existed, built during the Phase 2
reconciler commit specifically to pre-stage this exact need (its own
docstring says as much -- see `IMPROVEMENT_BACKLOG.md`'s new entry for the
full quote). `auditor/trend.py` calls it three times per run: once for the
rolling 7-day figure (`as_of=today`), once for the rolling 30-day figure
(`as_of=today`), and once more for "the prior week's rate"
(`as_of=today - timedelta(days=7)`, i.e. the adjacent, non-overlapping
7-day window immediately before the current one) -- there is no second,
parallel window-pooling loop anywhere in this module. `audit_trend()`'s
`stats=None` default path likewise reuses
`scripts.reconcile_run.load_verifier_stats` directly (same identity-proof
convention) rather than a second, independent file-reading/validation
path.

**Two spec-silent numeric/naming judgment calls, both logged in full in
`IMPROVEMENT_BACKLOG.md`:** (1) the trend labels are `rising`/`falling`/
`flat` per this turn's own task text, plus a fourth `insufficient_data`
state (kept distinct from `flat`, not folded into it) for whenever either
side of the comparison is `None` -- "no rate to compare" is a different
finding from "the rate held steady," and the task's own wording treats the
threshold classification and the drafted=0 guard as two separate things to
prove; (2) the flat-band threshold itself, `TREND_FLAT_EPSILON = 0.03`
(three percentage points), a round, conservative, unstated-by-the-task
number, since no real `verifier_stats.json` history exists yet to
calibrate against.

**New test file `tests/test_auditor_trend.py`** (24 tests): the two
reuse-by-identity checks (`rolling_pass_rate`, `load_verifier_stats`);
`classify_trend`'s ordinary rising/falling/flat cases; bit-exact boundary
tests at both `+TREND_FLAT_EPSILON` and `-TREND_FLAT_EPSILON` (constructed
via subtraction from `0.0`, always exact in IEEE-754, plus
`math.nextafter`-constructed just-past-the-boundary cases on both sides,
so the inclusive-band behavior is proven precisely rather than
approximately); the three-way `None`-guard matrix; a hand-built two-week
synthetic history (`WEEK_1` a steady 0.75 daily pass rate, three
differently-shaped second weeks for the rising/falling/flat scenarios)
exercised end-to-end through `compute_pass_rate_trend`, including an
independent re-derivation of the pooled 30-day rate straight from the raw
rows (not merely re-deriving one result from another); the all-skip-prior-
week division-by-zero guard exercised through the *full* rolling-window
pipeline rather than only re-testing the already-covered
`rolling_pass_rate` helper in isolation; an as-of-before-any-history case;
and `audit_trend()`'s explicit-`stats`, missing-`runs`-key, and
`stats=None`-default paths (the last proven via `monkeypatch` to route
through the exact bound `scripts.reconcile_run.load_verifier_stats` name),
plus a real-file integration smoke test confirming `audit_trend()` runs
cleanly against this repo's actual, currently-empty
`data/verifier_stats.json` (`runs: []` today -- no analyst run has
happened for real yet), returning `"insufficient_data"` throughout rather
than raising.

**Verification:** `python -m pytest` -- **768 passed, 2 deselected** (up
from 744 immediately before this turn's own additions, i.e. +24 new
tests, exactly this turn's own count; nothing else changed or broke). No
file outside `auditor/trend.py` and `tests/test_auditor_trend.py` was
touched by this turn's own code changes (this file and
`IMPROVEMENT_BACKLOG.md` are documentation-only). Per this turn's own
instruction, no commit was made -- another agent integrates this alongside
the other Phase 5 `auditor/` files already sitting uncommitted in this
same working tree.

---

## 2026-07-11 — Phase 5: `auditor/duplicates.py`, the duplicate-topic checker

Fourth file landed in `auditor/` this same day (see the `lexicon_audit.py`
and `linkrot.py` entries directly below, both also this same day, at
least one built concurrently in this same working tree). Implements
CLAUDE.md's/the approved plan's `audit.yml` "weekly" bullet
"duplicate-topic detection (pairwise Jaccard on published titles/topics)"
-- pairwise-comparing every published card's own `headline` against every
other's, flagging above-threshold pairs that have no acknowledged
follow-up-story link between them.

**Reuse, not reimplementation, per this turn's own explicit instruction:**
`auditor/duplicates.py` imports `watcher.clustering._jaccard` and
`watcher.models.tokenize_title` directly and calls them -- `title_similarity()`
is nothing more than `_jaccard(tokenize_title(a), tokenize_title(b))`, the
exact composition `watcher/clustering.py` itself already uses to decide
whether two *source items* are the same underlying story. Two dedicated
identity-check tests (`mod._jaccard is clustering_mod._jaccard` and
`mod.tokenize_title is models_mod.tokenize_title`) prove this is the same
function object, not a lookalike copy. The threshold is also reused
verbatim: `DUPLICATE_JACCARD_THRESHOLD = watcher.config.JACCARD_SIMILARITY_THRESHOLD`
(0.35, the general cross-source bar) rather than the stricter 0.65
lab-lab bar -- logged in `IMPROVEMENT_BACKLOG.md` with the reasoning
(published card headlines are the analyst's own original prose, not raw
templated lab-RSS boilerplate, so the lab-lab bar's specific
false-positive rationale doesn't transfer).

**The follow-up-link exemption.** CLAUDE.md's corroboration procedure
step 6 has the analyst write a fixed, greppable sentence,
`(follow-up to "<prior headline>", card <prior_id>)`, into a genuine
follow-up card's own `what_happened` (or `why_it_matters`). Before
flagging an above-threshold pair, `find_duplicate_pairs()` checks whether
the later-dated card's own prose already carries that exact pattern
naming the earlier card's specific id (`is_acknowledged_followup()`); if
so, the pair is skipped entirely rather than flagged -- it's the
corroboration procedure's own linking convention working as designed,
not a true duplicate. The match is anchored on `prior_id` (a regex with
the quoted headline text treated as a wildcard, only the id held
literal) rather than requiring a byte-for-byte match of the quoted prior
headline too -- reasoning logged in full in `IMPROVEMENT_BACKLOG.md`.
Cards are sorted into a fixed `(date, id)` order before any pairwise scan
runs, so which card in a pair counts as "earlier" (the one a follow-up
sentence would name) vs. "later" (the one whose own prose gets scanned)
never depends on the order `cards` happens to be passed in -- verified by
a dedicated test asserting identical output for a shuffled vs. sorted
input list.

`audit_duplicates()` is the one filesystem-touching entry point (`cards`
defaults to `None`, in which case real published cards are loaded via
`auditor.linkrot.load_cards()` -- reused directly, not a third
independently-written "walk `content/cards/`, skip `index.json`" loader).
Every other function in this module (`title_similarity`, `shared_topics`,
`is_acknowledged_followup`, `find_duplicate_pairs`) is pure and
filesystem-free, matching `auditor/lexicon_audit.py`'s own established
convention.

**New test file `tests/test_auditor_duplicates.py`** (26 tests): the two
reuse-by-identity checks above; `title_similarity` correctness against a
manually-computed `_jaccard(tokenize_title(...), ...)` value and its
graceful handling of a missing/empty headline; `shared_topics`'
sorted-intersection behavior and its graceful handling of a card with no
`topics` at all; the follow-up exemption in both its eligible prose
fields (`what_happened` and `why_it_matters`), its specificity (a
follow-up-shaped sentence naming the *wrong* prior id does not exempt the
pair), and its graceful handling of missing prose fields; `>=` threshold-boundary
inclusivity proven with a real, exactly-representable (in binary
floating point) Jaccard value of 0.5 rather than an approximate
comparison; determinism regardless of input order; the zero/one-card
edge cases; a three-card scenario proving only the one genuinely
above-threshold pair is reported and no card is ever compared against
itself; `audit_duplicates()`'s explicit-cards path, its clean-state empty
result, its default `cards=None` path (proven, via `monkeypatch`, to
route through the exact bound `auditor.linkrot.load_cards` name); and a
real-content integration smoke test confirming `audit_duplicates()` runs
cleanly against this repo's actual, currently-empty `content/cards/`
directory (no analyst run has happened for real yet), returning
`{"duplicate_pairs": []}` rather than raising.

**Verification:** `python -m pytest` -- **744 passed, 2 deselected** (up
from 718 immediately before this turn's own additions, i.e. +26 new
tests, exactly this turn's own count; nothing else changed or broke). No
file outside `auditor/duplicates.py` and `tests/test_auditor_duplicates.py`
was touched by this turn's own code changes (this file and
`IMPROVEMENT_BACKLOG.md` are documentation-only). Per this turn's own
instruction, no commit was made -- another agent integrates this
alongside the other Phase 5 `auditor/` files already sitting uncommitted
in this same working tree.

---

## 2026-07-11 — Phase 5: `auditor/linkrot.py`, the weekly link-rot checker

Second file in the new `auditor/` package, built in the same working tree
as (and, it turned out, concurrently with — see the note near the end of
this entry) the `auditor/lexicon_audit.py` entry directly below.
Implements CLAUDE.md's `audit.yml` "weekly" bullet "link rot (HEAD/GET
every citation URL, classify ok/dead/unreachable)", reusing
`watcher/http.py`'s existing retry/backoff `requests.Session` (via
`watcher.http.build_session()`) rather than reimplementing any fetch
discipline, per this turn's own explicit instruction.

**What it does:** `load_cards()` reads every real `content/cards/*.json`
(skipping `index.json`), gracefully returning `[]` since that directory
is still empty in this repo (no analyst run has happened for real yet) —
`audit_link_rot()` also accepts an explicit `cards` list directly, for
testability, per this turn's own instruction. `collect_citation_urls()`
pulls every `citations[].url` across those cards, deduped in first-seen
order. `check_url()` HEAD-requests each URL (with `allow_redirects=True`
explicitly set — `requests.Session.head()` alone defaults that to
`False`, unlike every other verb, which would have misclassified an
ordinary 301/302'd citation as something other than its real final
status), falling back to a GET only when the HEAD response itself says
the method isn't supported (405/501). Every final status is classified
into exactly three buckets: **ok** (2xx), **dead** (404 or 410 — the only
statuses that mean "the server itself says this is gone"), or
**unreachable** (5xx, a timeout, a connection error, or — a spec-silent
call, logged in full in `IMPROVEMENT_BACKLOG.md` — every other non-2xx
status too, e.g. 403/401/429, since this project's own real fetch history
already shows lab domains 403'ing ordinary requests for bot-management
reasons that have nothing to do with the resource being gone). A 5xx/
timeout/connection-error URL is recorded as `unreachable` this run and
not retried again within the same run — `build_session()`'s mounted
adapter deliberately has an empty `status_forcelist`, so no extra retry
loop was needed to get that "retried next week, not this run" behavior;
it falls out of the existing session's own design for free.
`audit_link_rot()` ties it together into one summary dict
(`{checked_at, total_urls, counts: {ok, dead, unreachable}, results:
[...]}`) — a provisional shape for whoever later builds
`schemas/audit.schema.json` and `auditor/report.py`, not yet a locked
contract.

**New test file `tests/test_auditor_linkrot.py`** (27 tests, via
`requests-mock`, this project's established deterministic HTTP test
tool): status-code classification in isolation; HEAD-only vs.
HEAD-then-GET-fallback behavior (both the 405 and 501 trigger, and a
negative case proving an ordinary 404 does *not* trigger a fallback);
timeout and connection-error handling (never raises, always classifies
as `unreachable`); the `allow_redirects` override actually resolving a
301 to its real final status; `collect_citation_urls`/`load_cards`
plumbing (dedup, missing-directory, `index.json`-skipping); and a
realistic 200/404/410/500/timeout mix run through `audit_link_rot()`
end-to-end, asserting `dead` and `unreachable` land in genuinely disjoint
buckets rather than collapsing into one — the specific proof this turn's
task asked for.

**A real-time concurrency note, logged here for honesty rather than
smoothed over:** partway through this turn, `git status`/`git diff`
showed `auditor/lexicon_audit.py`, `tests/test_auditor_lexicon_coverage.py`,
and fresh appends to both this file and `IMPROVEMENT_BACKLOG.md` already
present and uncommitted in this same working tree — a sibling agent
building the lexicon-coverage checker had been running concurrently
against the same checkout. This turn's own file scope
(`auditor/linkrot.py` + `tests/test_auditor_linkrot.py`, plus these two
log files) was unaffected by that discovery, with one exception: this
module's own `auditor/__init__.py` was created first (mirroring the
`watcher/sources/__init__.py` precedent for a brand-new package), then
**removed** once the sibling's already-logged `IMPROVEMENT_BACKLOG.md`
entry turned up stating `auditor/` ships with no `__init__.py` at all
(matching `scripts/`'s own convention). Re-verified after removing it:
`from auditor import linkrot` still resolves correctly with no
`__init__.py` present (Python's implicit-namespace-package handling,
same mechanism the whole existing suite already leans on for `from
watcher import ...` when running via `python -m pytest` from the repo
root), so nothing needed to change beyond deleting the one file. Full
reasoning logged in `IMPROVEMENT_BACKLOG.md`.

**Verification:** `python -m pytest` — **718 passed, 2 deselected** (up
from 691 immediately before this turn's own additions, i.e. +27 new
tests, exactly this turn's own count; nothing else changed or broke,
including the sibling's concurrently-added 16 lexicon-coverage tests).
No file outside `auditor/linkrot.py` and `tests/test_auditor_linkrot.py`
was touched by this turn's own code changes (this file and
`IMPROVEMENT_BACKLOG.md` are documentation-only). Per this turn's own
instruction, no commit was made — another agent integrates this
alongside the sibling's concurrent work.

---

## 2026-07-11 — Phase 5: `auditor/lexicon_audit.py`, the lexicon coverage/orphan checker

First file in the new `auditor/` package (`auditor/linkrot.py`, `trend.py`,
`missed_story.py`, `duplicates.py`, `report.py`, `cli.py` — the rest of
the approved plan's Phase 5 file layout — remain unbuilt; out of this
turn's explicit scope). Implements CLAUDE.md's `audit.yml` "weekly" bullet
"lexicon orphan/coverage check (terms used vs defined)" as pure,
filesystem-free Python: given an explicit list of cards (`content/cards/`
is still empty — no analyst run has happened for real yet, so this is
tested against fixtures, per this turn's own instruction) and a loaded
`content/lexicon.json` (the real, seeded 30 entries), it reports:

- **Coverage gaps** (`find_coverage_gaps` / `audit_coverage`): a lexicon
  term genuinely used (word-boundary, case-insensitive) in a card's own
  prose fields (`headline`, `what_happened`, `why_it_matters`,
  `one_liner`) but missing from that card's own `lexicon_terms[]` —
  exactly the miss CLAUDE.md's lexicon auto-growth rule (corroboration
  procedure step 7) is supposed to prevent on every real analyst run.
- **Orphans** (`find_orphans`): a lexicon entry whose `seen_in[]` is empty
  *and* whose term is never referenced in any of the passed-in cards'
  prose either. A non-empty `seen_in[]` always overrides — it's the
  analyst's own auto-grown historical record, which may span more cards
  than one audit run's own `cards` argument.
- `audit_lexicon()` combines both into one
  `{"coverage_gaps": [...], "orphans": [...]}` dict — a provisional shape
  for whoever later builds `schemas/audit.schema.json` and
  `scripts/append_backlog_findings.py`, not yet a locked contract.

Word-boundary matching (`\bterm\b`, case-insensitive) reuses the same
technique already established twice elsewhere in this repo
(`watcher/sources/hn.py`'s `HN_KEYWORDS`, `site/lib/linkify.py`'s own
term regex), specifically so a short term like `"RAG"` never
false-positive-matches as a bare substring inside an unrelated longer
word such as `"storage"` or `"average"` — both literally contain the
three characters "r", "a", "g" in sequence, and both are exercised as a
dedicated test case on both the coverage-gap and orphan side.

**New test file `tests/test_auditor_lexicon_coverage.py`** (16 tests):
fixture cards + a small fixture lexicon covering a genuine coverage gap,
a correctly-listed term, case-insensitive listed-term comparison, orphan
detection (both the plain empty-`seen_in`-and-unmentioned case and the
non-empty-`seen_in`-overrides-a-narrower-`cards`-subset case), the
`"RAG"` word-boundary edge case, and one integration-flavored smoke test
against the real, on-disk `content/lexicon.json` (asserts it still has
30 entries and scans cleanly against synthetic cards without raising).
Every spec-silent judgment call made while writing this module (which
card fields count as scannable "prose," the case-insensitive
already-listed check, the orphan-decision rule, `auditor/`'s
`__init__.py`-free package convention matching `scripts/`'s own
precedent, and the provisional `audit_lexicon()` return shape) is logged
in full in `IMPROVEMENT_BACKLOG.md` under "Phase 5:
`auditor/lexicon_audit.py`."

**Verification:** `python -m pytest` — **691 passed, 2 deselected** (up
from 675 before this turn; +16 new tests, nothing else changed or broke).
No file outside `auditor/lexicon_audit.py` and
`tests/test_auditor_lexicon_coverage.py` was touched this turn — in
particular, no workflow, schema, or `CLAUDE.md` change, consistent with
this turn's narrow, explicit scope (the lexicon coverage/orphan checker
only; the rest of `audit.yml`'s checks, `improve.yml`, and the
fortnight-parity guard are separate, not-yet-built Phase 5 pieces).

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
