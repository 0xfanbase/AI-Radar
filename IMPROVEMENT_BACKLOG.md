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
