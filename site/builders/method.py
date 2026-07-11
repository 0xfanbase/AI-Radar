"""Method & Audit builder (Phase 4, build-plan section 5).

Explains the pipeline -- watch, analyze/verify, the CI gate, the weekly
audit -- in this module/template's own words (not copy-pasted from
CLAUDE.md), and surfaces the pure-code pipeline's own basic stats.

Reads three data files:

* ``data/ledger.json`` -- the watcher's idempotency ledger. Read only for
  a basic status breakdown (how many distinct clusters the watcher has
  ever tracked, split queued/published/dropped); never for content.
* ``data/verifier_stats.json`` -- one row per completed analyst/verifier
  run. Empty today (``runs: []``) since ``analyze.yml`` has never
  executed for real in this environment -- summarized into a small
  aggregate that handles the empty-runs case as a normal, expected state
  (``total_runs: 0``, ``overall_pass_rate: None``), never a
  ``ZeroDivisionError``.
* ``data/audit/latest.json``, **only if it exists.** It does not, as of
  this build stage -- ``audit.yml`` is Phase 5 scope and hasn't been
  built or run yet. This is the one required graceful-degradation path
  this module is explicitly built and tested against: a missing file
  renders an honest placeholder ("No audit has run yet -- audit.yml is
  part of Phase 5") rather than raising ``FileNotFoundError`` or crashing
  the whole page build. See :func:`load_audit_latest` /
  :func:`build_audit_section`.

Two-step build usage (mirrors ``site/builders/board.py``'s own
convention):

1. Call :func:`build_method_context` once, passing the loaded
   ``data/ledger.json`` dict, the loaded ``data/verifier_stats.json``
   dict, and the result of :func:`load_audit_latest` (``None`` today), to
   get the fully-computed template context.
2. Render via :func:`render_method_page` (accepts a Jinja ``Environment``
   the caller supplies, or builds its own minimal one via
   :func:`build_jinja_env` when none is given).

This module deliberately does *not* wire itself into ``site/generate.py``
(out of this turn's scope -- another turn integrates every Phase 4
builder together); see ``IMPROVEMENT_BACKLOG.md``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
DATA_DIR = REPO_ROOT / "data"
LEDGER_PATH = DATA_DIR / "ledger.json"
VERIFIER_STATS_PATH = DATA_DIR / "verifier_stats.json"
AUDIT_LATEST_PATH = DATA_DIR / "audit" / "latest.json"

# The honest placeholder shown when data/audit/latest.json doesn't exist
# yet -- true today, since audit.yml is Phase 5 scope and hasn't run.
NO_AUDIT_MESSAGE = (
    "No audit has run yet -- audit.yml is part of Phase 5, which hasn't "
    "executed in this environment. Once it runs (weekly, pure code, no "
    "LLM involved), this section will show its findings directly: link "
    "rot on every citation, Lexicon coverage/orphan terms, the "
    "verifier's pass-rate trend, a missed-story check against that "
    "week's top Hacker News AI stories, and duplicate-topic detection."
)


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    """Load `data/ledger.json` (defaults to this repo's real, pure-code
    generated file)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_verifier_stats(path: Path = VERIFIER_STATS_PATH) -> dict[str, Any]:
    """Load `data/verifier_stats.json` (defaults to this repo's real,
    currently-empty-runs file)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_audit_latest(path: Path = AUDIT_LATEST_PATH) -> dict[str, Any] | None:
    """`data/audit/latest.json` if it exists, else `None` -- it doesn't,
    as of this build stage (Phase 5's `audit.yml` has never run in this
    environment). This is the one required graceful-degradation path this
    module is built and tested around: a missing file is an expected,
    everyday state here, not an error to raise on."""
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_ledger_stats(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Basic pipeline stats from the watcher's own ledger: how many
    distinct clusters (deduplicated stories) it has ever tracked, broken
    down by status. Every real cluster tracked today is `queued` (the
    analyst has never run for real in this environment yet) -- this
    function doesn't assume that, it just counts whatever's actually
    there, so it stays correct once real published/dropped entries exist
    too."""
    entries = ledger.get("entries", {})
    queued = published = dropped = 0
    for entry in entries.values():
        status = entry.get("status")
        if status == "queued":
            queued += 1
        elif status == "published":
            published += 1
        elif status == "dropped":
            dropped += 1
    return {
        "total_clusters": len(entries),
        "queued": queued,
        "published": published,
        "dropped": dropped,
    }


def build_verifier_summary(verifier_stats: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate `data/verifier_stats.json`'s per-run rows into one small
    summary. `runs == []` (true today -- see this module's own docstring)
    yields `total_runs: 0` and `overall_pass_rate: None` rather than a
    `ZeroDivisionError`."""
    runs = verifier_stats.get("runs", [])
    total_runs = len(runs)
    cards_drafted = sum(r.get("cards_drafted", 0) for r in runs)
    confirmed = sum(r.get("confirmed", 0) for r in runs)
    reported = sum(r.get("reported", 0) for r in runs)
    dropped = sum(r.get("dropped", 0) for r in runs)
    overall_pass_rate = (
        (confirmed + reported) / cards_drafted if cards_drafted else None
    )
    return {
        "total_runs": total_runs,
        "cards_drafted": cards_drafted,
        "confirmed": confirmed,
        "reported": reported,
        "dropped": dropped,
        "overall_pass_rate": overall_pass_rate,
    }


def build_audit_section(audit_latest: Mapping[str, Any] | None) -> dict[str, Any]:
    """The Audit section's own view model.

    `audit_latest is None` -- the real, current state of this
    environment -- renders the honest `NO_AUDIT_MESSAGE` placeholder
    (`available: False`). A real future `data/audit/latest.json`
    (Phase 5 -- `schemas/audit.schema.json` doesn't exist yet as of this
    build stage, so its exact shape is undefined) is read defensively
    (`.get()` with fallbacks, never assuming a required key) rather than
    hard-coding field names this codebase hasn't fixed yet; see
    IMPROVEMENT_BACKLOG.md.
    """
    if audit_latest is None:
        return {"available": False, "message": NO_AUDIT_MESSAGE}
    findings = audit_latest.get("findings", [])
    return {
        "available": True,
        "generated_at": audit_latest.get("generated_at"),
        "findings_count": len(findings) if isinstance(findings, list) else None,
    }


def build_method_context(
    ledger: Mapping[str, Any],
    verifier_stats: Mapping[str, Any],
    audit_latest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Full Jinja context for `/method/` (`method.html`)."""
    return {
        "ledger_stats": build_ledger_stats(ledger),
        "verifier_summary": build_verifier_summary(verifier_stats),
        "audit": build_audit_section(audit_latest),
    }


def build_jinja_env(template_dir: Path = TEMPLATES_DIR) -> Environment:
    """A minimal standalone Jinja environment for this builder, mirroring
    `site/generate.py`/`site/builders/board.py`'s own `build_jinja_env()`
    (autoescape on, `StrictUndefined`, `trim_blocks`/`lstrip_blocks`).
    Deliberately not imported from `generate.py` -- this builder isn't
    wired into `generate.py`'s render pipeline yet (out of this turn's
    scope; see IMPROVEMENT_BACKLOG.md)."""
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_method_page(
    ledger: Mapping[str, Any],
    verifier_stats: Mapping[str, Any],
    audit_latest: Mapping[str, Any] | None,
    *,
    env: Environment | None = None,
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_method_context(ledger, verifier_stats, audit_latest)
    return jinja_env.get_template("method.html").render(**context)


def write_method_page(
    env: Environment,
    ledger: Mapping[str, Any],
    verifier_stats: Mapping[str, Any],
    audit_latest: Mapping[str, Any] | None,
    public_dir: Path,
) -> Path:
    """Render + write `/method/` (`<public_dir>/method/index.html`).
    Convenience entry point for a future `site/generate.py` integration --
    not called by this module itself, and not called from `generate.py`
    yet (out of this turn's scope)."""
    html = render_method_page(ledger, verifier_stats, audit_latest, env=env)
    path = Path(public_dir) / "method" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
