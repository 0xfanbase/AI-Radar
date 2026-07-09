"""Smoke test for site/generate.py -- Phase 4 scaffold.

Scope for this build stage: prove the load -> validate -> render -> write
pipeline runs against this repo's *real* content/*.json and data/*.json
(not fixtures) without crashing, and produces at least public/index.html
and public/static/css/tokens.css, per this commit's explicit acceptance
bar. Deeper per-page assertions (schema conformance across every page,
board/lexicon count checks, contrast-ratio assertions parsed from
tokens.css, reduced-motion media-query presence, sparkline SVG
well-formedness, etc. -- the full test_build.py scope named in the build
plan) arrive incrementally in later Phase 4 commits alongside the page
builders those assertions depend on.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent.parent


def _load_generate_module():
    """Load site/generate.py by explicit file path rather than
    `import generate` off a sys.path insert, and specifically NOT as
    `import site` -- `site` is also a Python stdlib module name, so
    treating this directory as an importable package under that name
    risks shadowing it. Loading by path sidesteps the collision entirely.
    See IMPROVEMENT_BACKLOG.md."""
    spec = importlib.util.spec_from_file_location(
        "frontier_wire_site_generate", SITE_DIR / "generate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = _load_generate_module()


def test_generate_runs_against_real_repo_content_without_crashing(tmp_path):
    out_dir = generate.generate(public_dir=tmp_path)
    assert out_dir == tmp_path


def test_generate_produces_index_html(tmp_path):
    generate.generate(public_dir=tmp_path)
    index = tmp_path / "index.html"
    assert index.is_file()
    html = index.read_text(encoding="utf-8")
    assert "<html" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html
    assert "<h1" in html


def test_generate_produces_tokens_css(tmp_path):
    generate.generate(public_dir=tmp_path)
    tokens = tmp_path / "static" / "css" / "tokens.css"
    assert tokens.is_file()
    css = tokens.read_text(encoding="utf-8")
    assert "--color-bg: #0B0E17" in css
    assert "--color-signal-cyan: #43E5C4" in css


def test_generate_produces_components_css(tmp_path):
    generate.generate(public_dir=tmp_path)
    components = tmp_path / "static" / "css" / "components.css"
    assert components.is_file()


def test_load_cards_handles_empty_cards_dir_gracefully():
    # content/cards/ has no real analyst output yet -- must not crash, and
    # must return an empty list rather than None or raise.
    cards = generate.load_cards()
    assert cards == []


def test_generate_is_rerunnable_into_the_same_directory(tmp_path):
    # generate() must be safe to run twice into the same output dir
    # (e.g. a CI re-run) without leaving stale or half-written output.
    generate.generate(public_dir=tmp_path)
    generate.generate(public_dir=tmp_path)
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "static" / "css" / "tokens.css").is_file()
