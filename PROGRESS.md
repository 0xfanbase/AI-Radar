# PROGRESS.md

Reverse-chronological build log for AI Frontier Wire. Newest entry on top.
Each entry corresponds to one commit or one phase checkpoint. See
`CLAUDE.md` for the standing rules and architecture, and
`IMPROVEMENT_BACKLOG.md` for every spec-silent decision made along the way.

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
