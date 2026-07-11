"""Primer builder (Phase 4, build-plan section 5) -- the fixed,
dependency-ordered ten-term on-ramp.

Reads `content/primer.json`'s ordered list of 10 lexicon slugs
(`{generated_at, terms: [...]}`, Phase 3's seeded on-ramp: foundation
model -> transformer -> attention -> parameter count -> context window ->
pretraining -> fine-tuning -> RLHF -> hallucination -> open weights) and
renders `/primer/` as a numbered reading sequence, each step linking to
its real `/lexicon/<slug>/` page.

Per this turn's explicit instruction, every step's descriptive text is
its lexicon entry's own `one_liner` **verbatim** -- this module invents
no new per-term copy. The only original prose on the page is the fixed
intro paragraph explaining *why* the sequence is ordered the way it is
(a structural framing statement, not a definition of any term), plus the
per-step "Step N of 10" labels -- both build-plan connective tissue, not
content this module is meant to originate term definitions for.

Two-step build usage (mirrors `site/builders/lexicon.py`'s own
convention):

1. Call :func:`build_primer_context` once, passing the loaded
   `content/primer.json` dict and the loaded `content/lexicon.json`
   array, to get the fully-computed template context.
2. Render via :func:`render_primer_page` (accepts a Jinja `Environment`
   the caller supplies, or builds its own minimal one via
   :func:`build_jinja_env` when none is given -- matching
   `board.py`/`lexicon.py`'s "self-sufficient, not wired into
   generate.py yet" turn scope).

This module deliberately does *not* wire itself into `site/generate.py`
(out of this turn's scope -- another turn integrates every Phase 4
builder together); see `IMPROVEMENT_BACKLOG.md`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

BUILDERS_DIR = Path(__file__).resolve().parent
SITE_DIR = BUILDERS_DIR.parent
REPO_ROOT = SITE_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
LIB_DIR = SITE_DIR / "lib"
CONTENT_DIR = REPO_ROOT / "content"
PRIMER_PATH = CONTENT_DIR / "primer.json"
LEXICON_PATH = CONTENT_DIR / "lexicon.json"


def _load_module_by_path(name: str, path: Path):
    """Load a module from an explicit file path, registering it in
    `sys.modules` *before* `exec_module` runs -- see
    `site/builders/lexicon.py`'s identical helper for the full rationale
    (`site/` is deliberately never an importable package)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Reused verbatim, not reimplemented -- `slugify()` must stay byte-for-byte
# identical to `site/lib/linkify.py`/`site/builders/lexicon.py`'s own, or
# a primer slug could fail to resolve to the lexicon page it's supposed to
# link to.
linkify = _load_module_by_path("frontier_wire_site_lib_linkify", LIB_DIR / "linkify.py")

slugify = linkify.slugify


@dataclass(frozen=True)
class PrimerStepView:
    """One rendered Primer step -- the template only reads these
    already-computed fields, it never branches on raw
    `content/primer.json`/`content/lexicon.json` data itself."""

    step: int
    total_steps: int
    term: str
    slug: str
    one_liner: str


def build_slug_to_entry(lexicon_entries: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """`{slug: lexicon entry}` for every loaded `content/lexicon.json`
    entry, keyed by the same `slugify()` transform `site/lib/linkify.py`
    already uses to build `/lexicon/<slug>/` routes."""
    return {slugify(str(e["term"])): e for e in lexicon_entries}


def build_steps(
    primer_terms: Sequence[str], lexicon_entries: Sequence[Mapping[str, Any]]
) -> list[PrimerStepView]:
    """Resolve `content/primer.json`'s ordered slug list against the
    loaded lexicon into the full ordered step sequence.

    Raises `KeyError` (naming the offending slug) if any primer slug has
    no matching lexicon entry -- a primer slug that can't resolve is a
    content-authoring bug in `content/primer.json`/`content/lexicon.json`
    themselves (both are hand-authored seed content, not user input), so
    this fails loudly at build time rather than silently skipping a step
    or rendering a broken link. All 10 of the real, committed primer
    slugs resolve today -- see `site/tests/test_primer_builder.py`.
    """
    slug_to_entry = build_slug_to_entry(lexicon_entries)
    total = len(primer_terms)
    steps: list[PrimerStepView] = []
    for i, slug in enumerate(primer_terms, start=1):
        if slug not in slug_to_entry:
            raise KeyError(
                f"content/primer.json references lexicon slug {slug!r}, "
                "which has no matching content/lexicon.json entry"
            )
        entry = slug_to_entry[slug]
        steps.append(
            PrimerStepView(
                step=i,
                total_steps=total,
                term=str(entry["term"]),
                slug=slug,
                one_liner=str(entry["one_liner"]),
            )
        )
    return steps


def build_primer_context(
    primer: Mapping[str, Any], lexicon_entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Full Jinja context for `/primer/` (`primer.html`)."""
    steps = build_steps(list(primer.get("terms", [])), lexicon_entries)
    return {
        "steps": steps,
        "total_steps": len(steps),
        "generated_at": primer.get("generated_at"),
    }


def load_primer(path: Path = PRIMER_PATH) -> dict[str, Any]:
    """Load `content/primer.json` (defaults to this repo's real seeded
    file)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_lexicon(path: Path = LEXICON_PATH) -> list[dict[str, Any]]:
    """Load `content/lexicon.json` (defaults to this repo's real seeded
    file)."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_jinja_env(template_dir: Path = TEMPLATES_DIR) -> Environment:
    """A minimal standalone Jinja environment for this builder, mirroring
    `site/generate.py`/`site/builders/board.py`/`site/builders/lexicon.py`'s
    own `build_jinja_env()` (autoescape on, `StrictUndefined`,
    `trim_blocks`/`lstrip_blocks`). Deliberately not imported from
    `generate.py` -- this builder isn't wired into `generate.py`'s render
    pipeline yet (out of this turn's scope; see IMPROVEMENT_BACKLOG.md)."""
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_primer_page(
    primer: Mapping[str, Any],
    lexicon_entries: Sequence[Mapping[str, Any]],
    *,
    env: Environment | None = None,
) -> str:
    jinja_env = env or build_jinja_env()
    context = build_primer_context(primer, lexicon_entries)
    return jinja_env.get_template("primer.html").render(**context)


def write_primer_page(
    env: Environment,
    primer: Mapping[str, Any],
    lexicon_entries: Sequence[Mapping[str, Any]],
    public_dir: Path,
) -> Path:
    """Render + write `/primer/` (`<public_dir>/primer/index.html`).
    Convenience entry point for a future `site/generate.py` integration --
    not called by this module itself, and not called from `generate.py`
    yet (out of this turn's scope)."""
    html = render_primer_page(primer, lexicon_entries, env=env)
    path = Path(public_dir) / "primer" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
