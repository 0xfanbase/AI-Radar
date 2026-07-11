"""Corrections builder (Phase 4, build-plan section 5).

Reads the real, committed ``content/corrections.json`` (an empty array
today -- no card has ever needed a correction, since no analyst run has
happened for real in this environment yet) and renders ``/corrections/``:
every published correction, newest first, or an honest "no corrections
have been needed yet" empty state -- never a crash or a broken-looking
page for the (today, actual) zero-corrections case.

Two-step build usage (mirrors ``site/builders/board.py``'s own
convention):

1. Call :func:`build_corrections_context` once, passing the loaded
   ``content/corrections.json`` array, to get the fully-computed template
   context.
2. Render via :func:`render_corrections_page` (accepts a Jinja
   ``Environment`` the caller supplies, or builds its own minimal one via
   :func:`build_jinja_env` when none is given).

This module deliberately does *not* wire itself into ``site/generate.py``
(out of this turn's scope -- another turn integrates every Phase 4
builder together); see ``IMPROVEMENT_BACKLOG.md``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
CONTENT_DIR = REPO_ROOT / "content"
CORRECTIONS_PATH = CONTENT_DIR / "corrections.json"

EMPTY_CORRECTIONS_MESSAGE = (
    "No corrections have been needed yet. Every published card is "
    "adversarially re-checked by the verifier before it ever goes live "
    "(see the Method page); if a claim is later found to be wrong, the "
    "fix will be published here, permanently, alongside a note on the "
    "affected card itself -- nothing is ever silently edited."
)


@dataclass(frozen=True)
class CorrectionView:
    """One `content/corrections.json` entry -- the template only reads
    these already-computed fields, it never branches on the raw dict
    itself."""

    id: str
    card_id: str
    original_claim: str
    corrected_claim: str
    reason: str
    source_url: str
    corrected_at: str


def _sort_key(correction: Mapping[str, Any]) -> str:
    # ISO 8601 date-time strings (schemas/corrections.schema.json's
    # `corrected_at`) sort correctly as plain strings -- same convention
    # site/builders/wire.py's own card sort already relies on.
    return str(correction.get("corrected_at", ""))


def build_correction_view(raw: Mapping[str, Any]) -> CorrectionView:
    return CorrectionView(
        id=str(raw["id"]),
        card_id=str(raw["card_id"]),
        original_claim=str(raw["original_claim"]),
        corrected_claim=str(raw["corrected_claim"]),
        reason=str(raw["reason"]),
        source_url=str(raw["source_url"]),
        corrected_at=str(raw["corrected_at"]),
    )


def build_corrections_context(
    corrections: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Full Jinja context for `/corrections/` (`corrections.html`). An
    empty `corrections` list -- the real, current state of
    `content/corrections.json` -- yields an empty `corrections: []` and
    the honest `EMPTY_CORRECTIONS_MESSAGE`, never a crash or a
    broken-looking page."""
    ordered = sorted(corrections, key=_sort_key, reverse=True)
    return {
        "corrections": [build_correction_view(c) for c in ordered],
        "total_corrections": len(ordered),
        "empty_message": EMPTY_CORRECTIONS_MESSAGE,
    }


def load_corrections(path: Path = CORRECTIONS_PATH) -> list[dict[str, Any]]:
    """Load `content/corrections.json` (defaults to this repo's real
    seeded file -- `[]` today)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


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


def render_corrections_page(
    corrections: Sequence[Mapping[str, Any]], *, env: Environment | None = None
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_corrections_context(corrections)
    return jinja_env.get_template("corrections.html").render(**context)


def write_corrections_page(
    env: Environment, corrections: Sequence[Mapping[str, Any]], public_dir: Path
) -> Path:
    """Render + write `/corrections/` (`<public_dir>/corrections/index.html`).
    Convenience entry point for a future `site/generate.py` integration --
    not called by this module itself, and not called from `generate.py`
    yet (out of this turn's scope)."""
    html = render_corrections_page(corrections, env=env)
    path = Path(public_dir) / "corrections" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
