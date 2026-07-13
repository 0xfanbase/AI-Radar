"""Wire builder (Phase 4, build-plan section 5) -- home page + monthly archive.

The Wire is the site's front page: the last ~14 days of published cards,
newest first, plus a `/wire/<YYYY-MM>/` archive page per calendar month a
card exists in. `content/cards/` is empty as of this build stage (no
analyst run has happened for real yet in this environment) -- every
function here is written to handle an empty (or missing) card list
gracefully, rendering an honest "no cards yet" message rather than
crashing or producing a broken-looking page. See
`site/tests/test_wire_builder.py` for the zero-cards proof.

This module deliberately does *not* wire itself into `site/generate.py`
(out of this turn's scope -- another turn integrates it); it exposes a
small, dependency-injected function surface instead:

* :func:`prepare_card_view` -- turn one raw `card.schema.json`-shaped dict
  into the plain-value view model `card.html` renders (status chip text,
  linkified prose, sources, topic chips, lexicon-fallback chips).
* :func:`build_wire_context` / :func:`build_month_context` -- assemble the
  full Jinja context for the home page / one month's archive page.
* :func:`render_wire_index` / :func:`render_wire_month` -- render a page to
  an HTML string given a Jinja `Environment` (caller supplies the env, e.g.
  `generate.py`'s own `build_jinja_env()`, so this module never has to own
  environment setup for its real callers).
* :func:`write_wire_pages` -- convenience wrapper a future `generate.py`
  integration can call directly: renders the home page plus every month
  archive page and writes them under a given `public_dir`.

Card prose auto-linking reuses `site/lib/linkify.py` verbatim (per this
turn's instructions) rather than reimplementing any lexicon-matching
logic here.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

BUILDERS_DIR = Path(__file__).resolve().parent
SITE_DIR = BUILDERS_DIR.parent
TEMPLATES_DIR = SITE_DIR / "templates"
LIB_DIR = SITE_DIR / "lib"


def _load_module_by_path(name: str, path: Path):
    """Load a module from an explicit file path, registering it in
    `sys.modules` *before* `exec_module` runs.

    Matches the convention every other Phase 4 module/test already uses
    (`site/generate.py`'s own test, `site/tests/test_linkify.py`,
    `site/tests/test_svg_sparkline.py`): `site/` is deliberately never turned
    into an importable package (it would shadow the stdlib `site` module
    for anything else sharing the interpreter's `sys.path`), so every
    cross-file reference within `site/` loads its target by path instead
    of via `import site.lib.linkify`. The early `sys.modules` registration
    is required because `linkify.py` uses `@dataclass` together with
    `from __future__ import annotations`; dataclasses' own annotation
    resolution looks up `sys.modules[cls.__module__]` and raises if that
    key isn't populated yet.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


linkify = _load_module_by_path("frontier_wire_site_lib_linkify", LIB_DIR / "linkify.py")
topics_lib = _load_module_by_path("frontier_wire_site_lib_topics", LIB_DIR / "topics.py")


DEFAULT_WINDOW_DAYS = 14

# Card prose fields run through linkify.py's lexicon auto-link. A term is
# only considered genuinely "unmatched" (and so falls back to a footer
# chip) if linkify() couldn't place it in *any* of these fields -- see
# `prepare_card_view`'s docstring for the intersection logic. `one_liner`
# is deliberately excluded: build-plan section 5 wants the one-liner kept
# large, plain, and skimmable ("the eye should be able to skim only the
# one-liners"), not busy with inline links -- a spec-silent call, logged
# in IMPROVEMENT_BACKLOG.md.
LINKIFY_FIELDS = ("what_happened", "why_it_matters")

# Status chip text is always the literal uppercased enum value
# (card.schema.json's `status` is exactly `confirmed|reported|corrected`),
# rendered as visible text regardless of styling -- the CSS class only
# adds a color accent on top, never replaces the text. Reuses the
# `.chip`/`.chip--confirmed|reported|corrected` classes already shipped in
# `site/static/css/components.css` (Phase 4 scaffold commit), so this
# builder introduces no new CSS.
STATUS_CHIP_CLASS = {
    "confirmed": "chip chip--confirmed",
    "reported": "chip chip--reported",
    "corrected": "chip chip--corrected",
}

EMPTY_WIRE_MESSAGE = (
    "No stories published yet -- check back soon. New fact-checked AI "
    "stories are added here daily, newest first."
)


def build_env(templates_dir: Path | None = None) -> Environment:
    """Build a Jinja `Environment` matching `site/generate.py`'s own
    `build_jinja_env()` configuration (autoescape on, `StrictUndefined`,
    `trim_blocks`/`lstrip_blocks`). Duplicated here (rather than importing
    `generate.py`) so this module stays independently loadable/testable
    without a cross-file `importlib` dependency on a sibling script that
    is outside this turn's scope to edit -- logged in
    IMPROVEMENT_BACKLOG.md. A future integration is free to instead pass
    `generate.py`'s own env into the `render_*`/`write_wire_pages`
    functions below, which all accept an `Environment` as an argument
    rather than building their own.
    """
    templates_dir = templates_dir or TEMPLATES_DIR
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _sort_key(card: Mapping[str, Any]) -> tuple[str, str, str]:
    """Newest-first sort key: calendar date, then generation timestamp,
    then id as a final deterministic tie-break (both dates and
    `generated_at` are ISO 8601 strings, so plain string ordering already
    matches chronological ordering)."""
    return (str(card["date"]), str(card.get("generated_at", "")), str(card["id"]))


def cards_in_window(
    cards: Iterable[Mapping[str, Any]],
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
) -> list[Mapping[str, Any]]:
    """Every card dated within the last `window_days` calendar days
    (inclusive of `today`), sorted newest first. `today` defaults to the
    real UTC date; tests pass a fixed value for determinism. An empty (or
    entirely-outside-the-window) input returns `[]`, never `None` or a
    raised error -- the empty-cards case is not a special case here at
    all, just the natural result of an empty/filtered list.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=window_days - 1)
    windowed = [c for c in cards if cutoff <= date.fromisoformat(str(c["date"])) <= today]
    return sorted(windowed, key=_sort_key, reverse=True)


def available_months(cards: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every distinct `YYYY-MM` a card exists in, newest first."""
    months = {str(c["date"])[:7] for c in cards}
    return sorted(months, reverse=True)


def month_label(year_month: str) -> str:
    """`"2026-07"` -> `"July 2026"`, for the archive page's human-readable
    heading/nav text."""
    return datetime.strptime(year_month, "%Y-%m").strftime("%B %Y")


def cards_for_month(cards: Iterable[Mapping[str, Any]], year_month: str) -> list[Mapping[str, Any]]:
    """Every card dated in the given `YYYY-MM`, sorted newest first."""
    month_cards = [c for c in cards if str(c["date"])[:7] == year_month]
    return sorted(month_cards, key=_sort_key, reverse=True)


def prepare_card_view(card: Mapping[str, Any], slug_map: Mapping[str, str]) -> dict[str, Any]:
    """Turn one raw `card.schema.json`-shaped dict into the plain-value
    view model `site/templates/card.html` renders -- all Jinja logic
    lives in Python here, not in the template.

    Lexicon handling: `linkify()` runs independently against every field
    in `LINKIFY_FIELDS`; a term only lands in `lexicon_fallback_terms`
    (the card-footer "Terms:" chip row) if it came back unmatched from
    *every* one of those fields -- a term linked inline in
    `what_happened` alone must not also show up as an orphaned fallback
    chip just because it doesn't separately appear in `why_it_matters`.
    Each fallback entry still carries its resolved lexicon slug when one
    exists (so the fallback chip is a real link to `/lexicon/<slug>/`,
    satisfying the "define any linked term in one tap" bar even on the
    fallback path) and `None` only for a term absent from the lexicon
    slug map entirely.
    """
    lexicon_terms = [str(t) for t in card.get("lexicon_terms", [])]

    linked_fields: dict[str, Any] = {}
    unmatched_sets: list[set[str]] = []
    for field_name in LINKIFY_FIELDS:
        result = linkify.linkify(str(card[field_name]), lexicon_terms, slug_map)
        linked_fields[field_name] = result.html
        unmatched_sets.append(set(result.unmatched_terms))

    if unmatched_sets:
        truly_unmatched = set.intersection(*unmatched_sets)
    else:
        truly_unmatched = set(lexicon_terms)

    fallback_terms = [
        {"term": term, "slug": slug_map.get(term.lower())}
        for term in lexicon_terms
        if term in truly_unmatched
    ]

    status = str(card["status"])

    return {
        "id": card["id"],
        "date": card["date"],
        "generated_at": card["generated_at"],
        "model": card["model"],
        "headline": card["headline"],
        "status": status,
        "status_label": status.upper(),
        "status_chip_class": STATUS_CHIP_CLASS.get(status, "chip"),
        "one_liner": card["one_liner"],
        "what_happened_html": linked_fields["what_happened"],
        "why_it_matters_html": linked_fields["why_it_matters"],
        "topics": [topics_lib.display_name(t) for t in card.get("topics", [])],
        "citations": list(card.get("citations", [])),
        "lexicon_fallback_terms": fallback_terms,
        "correction_note": card.get("correction_note"),
    }


def build_wire_context(
    cards: Iterable[Mapping[str, Any]],
    lexicon_entries: Iterable[Mapping[str, Any]],
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
    masthead_sparklines: Any = None,
) -> dict[str, Any]:
    """Full Jinja context for the Wire home page (`wire_index.html`).

    `masthead_sparklines` (a list of
    `site/builders/moving.py::MastheadSparklineView`, or `None`) is the
    Wire home page's own opt-in to `templates/base.html`'s shared masthead
    sparkline strip -- the strip is scoped to this page only (see
    `moving.py`'s top-of-file docstring for why), so `build_month_context`
    below deliberately has no equivalent parameter. `None`/falsy collapses
    to `[]`, matching `base.html`'s own
    `{% if masthead_sparklines is defined and masthead_sparklines %}`
    guard (an empty list renders nothing, same as never setting the key).
    """
    cards = list(cards)
    slug_map = linkify.build_slug_map(lexicon_entries)
    windowed = cards_in_window(cards, window_days=window_days, today=today)
    months = available_months(cards)
    return {
        "cards": [prepare_card_view(c, slug_map) for c in windowed],
        "window_days": window_days,
        "empty_message": EMPTY_WIRE_MESSAGE,
        "archive_months": [{"key": m, "label": month_label(m)} for m in months],
        "masthead_sparklines": masthead_sparklines or [],
    }


def build_month_context(
    cards: Iterable[Mapping[str, Any]],
    lexicon_entries: Iterable[Mapping[str, Any]],
    year_month: str,
) -> dict[str, Any]:
    """Full Jinja context for one month's archive page
    (`wire_month.html`). Never raises for a month with no cards (e.g. a
    directly-guessed URL) -- just yields an empty `cards` list, same as
    the home page's zero-cards handling."""
    cards = list(cards)
    slug_map = linkify.build_slug_map(lexicon_entries)
    month_cards = cards_for_month(cards, year_month)
    other_months = [
        {"key": m, "label": month_label(m)}
        for m in available_months(cards)
        if m != year_month
    ]
    return {
        "cards": [prepare_card_view(c, slug_map) for c in month_cards],
        "year_month": year_month,
        "month_label": month_label(year_month),
        "empty_message": f"No cards published for {month_label(year_month)} yet.",
        "archive_months": other_months,
    }


def render_wire_index(
    env: Environment,
    cards: Iterable[Mapping[str, Any]],
    lexicon_entries: Iterable[Mapping[str, Any]],
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
    masthead_sparklines: Any = None,
) -> str:
    context = build_wire_context(
        cards,
        lexicon_entries,
        window_days=window_days,
        today=today,
        masthead_sparklines=masthead_sparklines,
    )
    return env.get_template("wire_index.html").render(**context)


def render_wire_month(
    env: Environment,
    cards: Iterable[Mapping[str, Any]],
    lexicon_entries: Iterable[Mapping[str, Any]],
    year_month: str,
) -> str:
    context = build_month_context(cards, lexicon_entries, year_month)
    return env.get_template("wire_month.html").render(**context)


def write_wire_pages(
    env: Environment,
    cards: Iterable[Mapping[str, Any]],
    lexicon_entries: Iterable[Mapping[str, Any]],
    public_dir: Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
    masthead_sparklines: Any = None,
    index_output_dir: Path | None = None,
) -> list[Path]:
    """Render + write the Wire index page and every month's archive page
    (`<public_dir>/wire/<YYYY-MM>/index.html`) for every month any card
    exists in.

    `masthead_sparklines` is passed through to the index page only (see
    `build_wire_context`'s own docstring) -- month archive pages
    (`render_wire_month`/`build_month_context`) never take or render it,
    so the masthead sparkline strip stays scoped to wherever the caller
    points the index page, never any archive page.

    `index_output_dir` (defaulted to `None`, which preserves every
    pre-Phase-7 caller's behavior of writing the Wire's own index page
    at `<public_dir>/index.html`) lets a caller redirect just the index
    page's own output location. `site/generate.py`'s Phase 7 map-frontend
    integration passes `public_dir / "wire"` here, since the map homepage
    (`site/builders/map.py`) now owns `<public_dir>/index.html` and the
    Wire moved to `/wire/` (see PROGRESS.md's Phase 7 entry) -- month
    archive pages are unaffected either way, they always render under
    `<public_dir>/wire/<YYYY-MM>/`, which was already the right location
    both before and after that move.
    """
    cards = list(cards)
    lexicon_entries = list(lexicon_entries)
    public_dir = Path(public_dir)
    index_dir = Path(index_output_dir) if index_output_dir is not None else public_dir
    written: list[Path] = []

    index_html = render_wire_index(
        env,
        cards,
        lexicon_entries,
        window_days=window_days,
        today=today,
        masthead_sparklines=masthead_sparklines,
    )
    index_path = index_dir / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_html, encoding="utf-8")
    written.append(index_path)

    for year_month in available_months(cards):
        month_html = render_wire_month(env, cards, lexicon_entries, year_month)
        month_path = public_dir / "wire" / year_month / "index.html"
        month_path.parent.mkdir(parents=True, exist_ok=True)
        month_path.write_text(month_html, encoding="utf-8")
        written.append(month_path)

    return written
