"""Regression test: every text-role design token clears WCAG AA contrast.

Phase 4's PM checkpoint (round 1) found that the plan-promised automated
contrast-ratio test never actually landed -- `site/static/css/tokens.css`'s
own header comment records the *claimed* ratios (recomputed by hand, once,
by a PM review) but nothing in the test suite protects those numbers from
regressing if a future commit edits a hex value.

This file closes that gap. It does NOT re-hardcode any of the hex values
tokens.css already defines -- every `--color-*` custom property is parsed
directly out of the real, on-disk `tokens.css` at test-collection time, and
WCAG 2.x relative-luminance contrast ratios are computed here, in Python,
from those parsed values. If a future edit changes a hex code in
tokens.css, this test recomputes against the new value automatically --
there is no second copy of "#39FF6E" etc. anywhere in this file.

Design-system role classification (which token is a *background*, which is
*border-only*, which is a *text* color) is not something that can be
mechanically inferred from a hex string alone -- that's read from
tokens.css's own header comment, which is prose, not machine-parseable
data. Those role groupings are therefore named directly below as the one
piece of structural knowledge this test does encode, matching tokens.css's
own documented intent:
  - `bg` / `panel` are the site's two background surfaces.
  - `hairline` is border/divider ONLY -- tokens.css's own comment states
    it must never be used for text or a focus ring (it fails AA on its own,
    by design). This test asserts that exclusion is enforced, not merely
    assumed: `hairline` must never appear in the set of tokens this test
    treats as a text color, AND (belt-and-suspenders) its own measured
    ratio against both backgrounds must actually be below 4.5:1, so if a
    future edit ever pushed hairline's hex to something that *would* pass
    AA, that would not silently justify reclassifying it as text-safe
    without a human revisiting this file.
  - Every other `--color-*` token (ink, signal-green, star-white,
    reported-amber, corrected-red, and any token added later) is treated
    as a text-role color and must clear 4.5:1 against both backgrounds.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SITE_DIR = Path(__file__).resolve().parent.parent
TOKENS_CSS_PATH = SITE_DIR / "static" / "css" / "tokens.css"

# Structural role knowledge (not hex values -- see module docstring).
BACKGROUND_TOKENS = {"bg", "panel"}
BORDER_ONLY_TOKENS = {"hairline"}  # never a text color, never a focus ring

_TOKEN_RE = re.compile(r"--color-([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\b")


def _parse_color_tokens(css_text: str) -> dict[str, str]:
    """Parse every `--color-<name>: #RRGGBB` custom property out of the
    real tokens.css text. Returns {name: "#RRGGBB"} (name lowercase, as
    written -- e.g. "signal-green", not the full "--color-signal-green")."""
    tokens = {}
    for name, hex_value in _TOKEN_RE.findall(css_text):
        tokens[name] = hex_value
    return tokens


def _srgb_channel_to_linear(channel_0_255: int) -> float:
    c = channel_0_255 / 255.0
    if c <= 0.03928:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of a #RRGGBB color."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    r_lin, g_lin, b_lin = (_srgb_channel_to_linear(c) for c in (r, g, b))
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 2.x contrast ratio between two #RRGGBB colors, >= 1.0."""
    l_a = _relative_luminance(hex_a)
    l_b = _relative_luminance(hex_b)
    lighter, darker = max(l_a, l_b), min(l_a, l_b)
    return (lighter + 0.05) / (darker + 0.05)


WCAG_AA_NORMAL_TEXT_MIN_RATIO = 4.5


@pytest.fixture(scope="module")
def color_tokens() -> dict[str, str]:
    css_text = TOKENS_CSS_PATH.read_text(encoding="utf-8")
    tokens = _parse_color_tokens(css_text)
    # Sanity check the parse itself actually found real tokens -- an empty
    # or trivially-small result here would mean the regex silently stopped
    # matching (e.g. tokens.css was restructured) and every test below
    # would vacuously pass over zero pairs. Fail loudly instead.
    assert len(tokens) >= 5, (
        f"expected to parse several --color-* tokens out of tokens.css, "
        f"got {tokens!r} -- has the file's format changed?"
    )
    assert BACKGROUND_TOKENS <= tokens.keys()
    assert BORDER_ONLY_TOKENS <= tokens.keys()
    return tokens


@pytest.fixture(scope="module")
def text_role_tokens(color_tokens: dict[str, str]) -> dict[str, str]:
    excluded = BACKGROUND_TOKENS | BORDER_ONLY_TOKENS
    return {name: hexv for name, hexv in color_tokens.items() if name not in excluded}


def test_hairline_is_never_treated_as_a_text_color(text_role_tokens):
    assert "hairline" not in text_role_tokens


def test_hairline_genuinely_fails_aa_contrast_against_both_backgrounds(color_tokens):
    # Belt-and-suspenders: hairline's exclusion above isn't just a name
    # match -- confirm its *actual* measured contrast is sub-AA against
    # both backgrounds, matching tokens.css's own header-comment claim
    # (1.26:1 against --color-panel). If this ever starts failing because
    # hairline's hex was edited to something AA-safe, that's a signal a
    # human should reconsider whether hairline still belongs in
    # BORDER_ONLY_TOKENS -- not a reason to silently loosen this test.
    hairline = color_tokens["hairline"]
    for bg_name in sorted(BACKGROUND_TOKENS):
        ratio = _contrast_ratio(hairline, color_tokens[bg_name])
        assert ratio < WCAG_AA_NORMAL_TEXT_MIN_RATIO, (
            f"hairline vs {bg_name} = {ratio:.2f}:1 -- no longer sub-AA; "
            f"re-examine whether hairline should still be border-only"
        )


@pytest.mark.parametrize("bg_name", sorted(BACKGROUND_TOKENS))
def test_every_text_role_token_clears_aa_against_every_background(
    color_tokens, text_role_tokens, bg_name
):
    bg_hex = color_tokens[bg_name]
    failures = []
    for text_name, text_hex in sorted(text_role_tokens.items()):
        ratio = _contrast_ratio(text_hex, bg_hex)
        if ratio < WCAG_AA_NORMAL_TEXT_MIN_RATIO:
            failures.append(f"{text_name} ({text_hex}) vs {bg_name} ({bg_hex}) = {ratio:.2f}:1")
    assert not failures, "sub-AA text/background pairing(s) found:\n" + "\n".join(failures)


def test_at_least_the_five_documented_text_roles_are_present(text_role_tokens):
    # Not a re-hardcoding of hex values (see module docstring) -- just
    # confirms the parse didn't accidentally drop a known role name, so a
    # future refactor that silently renames/removes a token is caught here
    # rather than by this test quietly covering fewer pairings than it
    # used to.
    expected_role_names = {
        "ink",
        "signal-green",
        "star-white",
        "reported-amber",
        "corrected-red",
    }
    assert expected_role_names <= text_role_tokens.keys()


# ---------------------------------------------------------------------------
# Opacity-composited text -- the raw-token checks above are blind to CSS
# `opacity`, which multiplies a text color toward whatever sits behind it.
# The open-weights filter's dimmed markers shipped at opacity 0.3 for
# months: raw ink-vs-bg is 13.7:1, but the COMPOSITED render was ~1.98:1
# -- far below AA for text that stays fully interactive -- while every
# token-pair test here passed. Each entry below names a CSS class that
# dims still-readable text, the file its rule lives in (the real opacity
# value is parsed out of that file at test time, never duplicated here),
# the text token it dims, and the two page surfaces it can sit over.
# Deliberately-inactive controls (the no-JS `disabled` map buttons) are
# NOT listed: WCAG's contrast minimum does not apply to inactive
# controls, and site/tests/test_map_builder.py separately asserts those
# buttons really are marked `disabled` rather than merely dimmed.
# ---------------------------------------------------------------------------

TEMPLATES_DIR = SITE_DIR / "templates"
COMPONENTS_CSS_PATH = SITE_DIR / "static" / "css" / "components.css"

# (source file, css class, text token) -- every entry is checked
# composited over BOTH background surfaces.
COMPOSITED_TEXT_OPACITY_ROLES = [
    (TEMPLATES_DIR / "map_index.html", "map-marker--dimmed", "ink"),
    (TEMPLATES_DIR / "map_index.html", "map-scan__item--dimmed", "ink"),
    (TEMPLATES_DIR / "map_index.html", "map-hint", "ink"),
    (COMPONENTS_CSS_PATH, "masthead-strip__topic", "ink"),
    (COMPONENTS_CSS_PATH, "wire-card__meta", "ink"),
    (COMPONENTS_CSS_PATH, "wire-card__why-label", "ink"),
]


def _parse_class_opacity(source_path: Path, css_class: str) -> float:
    """The `opacity:` value declared in `.{css_class} { ... }` in the
    real on-disk file -- parsed fresh so a future opacity edit is
    re-graded automatically, exactly like the hex tokens above."""
    text = source_path.read_text(encoding="utf-8")
    match = re.search(
        r"\." + re.escape(css_class) + r"\s*\{[^}]*?opacity:\s*([0-9.]+)",
        text,
        re.DOTALL,
    )
    assert match, (
        f"no `.{css_class} {{ ... opacity: ... }}` rule found in "
        f"{source_path.name} -- has the rule moved or been renamed?"
    )
    return float(match.group(1))


def _composite_over(fg_hex: str, bg_hex: str, alpha: float) -> str:
    """The effective #RRGGBB a text color renders as at `alpha` opacity
    over an opaque `bg_hex` surface (simple source-over per channel)."""
    fg = fg_hex.lstrip("#")
    bg = bg_hex.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        fg_c = int(fg[i : i + 2], 16)
        bg_c = int(bg[i : i + 2], 16)
        channels.append(round(fg_c * alpha + bg_c * (1 - alpha)))
    return "#" + "".join(f"{c:02x}" for c in channels)


@pytest.mark.parametrize(
    "source_path, css_class, text_token",
    COMPOSITED_TEXT_OPACITY_ROLES,
    ids=[entry[1] for entry in COMPOSITED_TEXT_OPACITY_ROLES],
)
def test_opacity_dimmed_text_still_clears_aa_when_composited(
    color_tokens, source_path, css_class, text_token
):
    alpha = _parse_class_opacity(source_path, css_class)
    failures = []
    for bg_name in sorted(BACKGROUND_TOKENS):
        bg_hex = color_tokens[bg_name]
        effective = _composite_over(color_tokens[text_token], bg_hex, alpha)
        ratio = _contrast_ratio(effective, bg_hex)
        if ratio < WCAG_AA_NORMAL_TEXT_MIN_RATIO:
            failures.append(
                f".{css_class} ({text_token} @ opacity {alpha} over {bg_name}) "
                f"composites to {effective} = {ratio:.2f}:1"
            )
    assert not failures, (
        "opacity-composited sub-AA text found:\n" + "\n".join(failures)
    )
