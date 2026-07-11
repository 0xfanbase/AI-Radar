"""About builder (Phase 4, build-plan section 5) -- a fully static page.

Renders `/about/`: what the site is, the non-commercial and
fully-auto-published framing, the anonymity mechanics, the standing
disclaimer, and the MIT/CC BY 4.0 licensing split -- all drawn from
CLAUDE.md section 1's hard rules, reworded here for a site reader rather
than copy-pasted verbatim.

Unlike every sibling Phase 4 builder, this page reads no content/data
file and never changes shape based on the pipeline's own runtime state
-- there is no `build_*_context()` computing anything, because there is
nothing to compute. `templates/about.html` alone *is* the page; this
module's only job is to render it through the shared `base.html` shell
(skip-link, masthead, footer) the same way every other page is rendered.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

SITE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SITE_DIR / "templates"


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


def render_about_page(*, env: Environment | None = None) -> str:
    """Render the full `/about/` page HTML. Takes no data arguments --
    see this module's own docstring for why."""
    jinja_env = env or build_jinja_env()
    return jinja_env.get_template("about.html").render()


def write_about_page(env: Environment, public_dir: Path) -> Path:
    """Render + write `/about/` (`<public_dir>/about/index.html`).
    Convenience entry point for a future `site/generate.py` integration --
    not called by this module itself, and not called from `generate.py`
    yet (out of this turn's scope)."""
    html = render_about_page(env=env)
    path = Path(public_dir) / "about" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
