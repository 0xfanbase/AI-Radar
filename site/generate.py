#!/usr/bin/env python3
"""AI Frontier Wire -- static site generator entrypoint (Phase 4).

This is the Phase-4 **scaffold**: the load -> validate -> render -> write
pipeline runs end to end today, but only the shared page shell
(`site/templates/base.html`) exists as a template. Later Phase 4 commits
add `site/builders/{wire,board,lexicon,primer,moving,method}.py`, each
producing its own child template(s) that extend `base.html` and a real
route under `public/`. Until those land, `render_pages()` renders
`base.html` directly (using its own default `content` block) as a
placeholder `public/index.html`, so this scaffold proves the plumbing
works rather than deferring all verification to later commits. See
IMPROVEMENT_BACKLOG.md for this and other scaffold-stage decisions.

Usage:
    python -m site.generate [--out public] [-v]
    python site/generate.py [--out public] [-v]
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import jsonschema
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SITE_DIR.parent
CONTENT_DIR = REPO_ROOT / "content"
DATA_DIR = REPO_ROOT / "data"
SCHEMAS_DIR = REPO_ROOT / "schemas"
TEMPLATES_DIR = SITE_DIR / "templates"
STATIC_DIR = SITE_DIR / "static"
PUBLIC_DIR = REPO_ROOT / "public"

log = logging.getLogger("frontier_wire.site.generate")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def schema_for(content_path: Path) -> Path | None:
    """The schemas/*.schema.json counterpart for a content/data file, by
    filename-stem convention (frontier_board.json -> frontier_board.schema.json).
    Returns None if no such schema file exists yet."""
    candidate = SCHEMAS_DIR / f"{content_path.stem}.schema.json"
    return candidate if candidate.exists() else None


def iter_top_level_json(directory: Path) -> list[Path]:
    """Every *.json file directly inside `directory` (non-recursive --
    subdirectories like content/cards/ or data/.cache/ are handled by their
    own dedicated loaders, not swept up here)."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def load_cards() -> list[dict]:
    """Load every content/cards/<id>.json (excluding the generated
    index.json manifest). Returns [] if the directory doesn't exist yet or
    is empty -- true as of this build stage, since no analyst run has
    happened for real yet. Every template/builder touching cards must
    handle the empty-list case gracefully rather than crash or render a
    broken-looking page."""
    cards_dir = CONTENT_DIR / "cards"
    if not cards_dir.is_dir():
        return []
    cards = []
    for path in sorted(cards_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        cards.append(load_json(path))
    return cards


def load_and_validate_content() -> dict[str, Any]:
    """Load every top-level content/*.json and data/*.json artifact plus
    content/cards/*.json, jsonschema-validating each against its
    schemas/*.schema.json counterpart when one exists. A file with no
    matching schema is loaded unvalidated and logged, rather than treated
    as fatal -- two such gaps (content/primer.json, data/audit/latest.json)
    are inherited from earlier phases and are not this scaffold's to fix;
    see IMPROVEMENT_BACKLOG.md."""
    loaded: dict[str, Any] = {}
    for path in iter_top_level_json(CONTENT_DIR) + iter_top_level_json(DATA_DIR):
        payload = load_json(path)
        schema_path = schema_for(path)
        if schema_path is None:
            log.warning(
                "no schema found for %s (expected schemas/%s.schema.json) "
                "-- loaded unvalidated",
                path.relative_to(REPO_ROOT),
                path.stem,
            )
        else:
            schema = load_json(schema_path)
            jsonschema.validate(payload, schema)
        loaded[path.stem] = payload
    loaded["cards"] = load_cards()
    return loaded


def build_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def copy_static(public_dir: Path) -> None:
    dest = public_dir / "static"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(STATIC_DIR, dest)


def render_pages(env: Environment, content: dict[str, Any], public_dir: Path) -> None:
    """Render every page template that exists today. Only base.html ships
    in this scaffold commit, so it is rendered directly (via its own
    default `content` block) as a placeholder homepage -- later commits
    replace this with real builders that each produce
    `templates/<page>.html` extending base.html, plus the routes named in
    the build plan (/wire/, /board/, /lexicon/, /primer/, /moving/,
    /method/, /corrections/, /about/)."""
    template = env.get_template("base.html")
    html = template.render(cards=content.get("cards", []))
    (public_dir / "index.html").write_text(html, encoding="utf-8")


def generate(public_dir: Path = PUBLIC_DIR) -> Path:
    """Run the full build: load+validate content/data, render templates,
    copy static assets, write everything under `public_dir`. Returns the
    output directory."""
    public_dir.mkdir(parents=True, exist_ok=True)
    content = load_and_validate_content()
    env = build_jinja_env()
    render_pages(env, content, public_dir)
    copy_static(public_dir)
    return public_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=PUBLIC_DIR,
        help="Output directory for the built site (default: public/, gitignored)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    out = generate(args.out)
    print(f"Built site to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
