"""Tests for scripts/fortnight_guard.py -- the ISO-week-parity
fortnightly-cadence guard.

Covers, in order: `iso_week_parity`'s correctness against hand-checked ISO
calendar dates; `decide_fortnight_mode`'s exact return shape and run/skip
decision, including the "ISO week 1 is always odd -> always a run week"
convention (`RUN_PARITY`'s direct analogue of `scripts/plan_run.py`'s own
"day 1 of any year is always a run day"); a real, ORDINARY (52-ISO-week
-year) boundary crossing, 2027->2028, proving clean run/skip alternation
holds straight through it with zero exceptions; the real 2026->2027
boundary -- 2026 is a genuine 53-ISO-week year, confirmed live against the
standard library's own `date(2026, 12, 28).isocalendar()` rather than
assumed -- proving the module docstring's documented, deliberate "two run
weeks in a row" quirk occurs at exactly one point and nowhere else nearby;
`write_github_output`'s exact emitted-line format and its append-not
-overwrite behavior; and `main()`'s CLI plumbing (`--date` parsing, stdout
content, `GITHUB_OUTPUT` being written only when that env var is actually
set, and the real-clock default-date path via a monkeypatched
`_default_today` seam).

No live network or wall-clock dependency anywhere in this file except the
one deliberately-isolated `_default_today` monkeypatch test -- every other
test passes an explicit `date`, matching this module's own "pure function
of an explicit date" design.
"""
from __future__ import annotations

from datetime import date

import pytest

import scripts.fortnight_guard as fortnight_guard_mod
from scripts.fortnight_guard import (
    RUN_PARITY,
    decide_fortnight_mode,
    iso_week_parity,
    main,
    write_github_output,
)

# ---------------------------------------------------------------------------
# iso_week_parity -- hand-checked against real ISO calendar dates
# ---------------------------------------------------------------------------


def test_iso_week_parity_odd_week():
    d = date(2026, 7, 13)  # confirmed: ISO week 29 of 2026 (odd)
    assert d.isocalendar()[1] == 29
    assert iso_week_parity(d) == 1


def test_iso_week_parity_even_week():
    d = date(2026, 7, 20)  # confirmed: ISO week 30 of 2026 (even)
    assert d.isocalendar()[1] == 30
    assert iso_week_parity(d) == 0


def test_iso_week_parity_week_one_is_always_odd_across_different_years():
    # Week 1 of ANY ISO year is numbered 1 -- trivially odd -- regardless
    # of which real calendar year it falls in. This is what makes
    # RUN_PARITY == 1 the direct analogue of plan_run.py's own "day 1 of
    # any year is always a run day" convention. Dates below are each
    # independently confirmed (not assumed) to be that year's own ISO
    # week 1 via date.isocalendar() itself.
    for d in (date(2024, 1, 1), date(2026, 1, 1), date(2028, 1, 3)):
        assert d.isocalendar()[1] == 1
        assert iso_week_parity(d) == 1 == RUN_PARITY


# ---------------------------------------------------------------------------
# decide_fortnight_mode -- exact shape + run/skip decision
# ---------------------------------------------------------------------------


def test_decide_fortnight_mode_run_week_full_shape():
    decision = decide_fortnight_mode(date(2026, 7, 13))
    assert decision == {
        "date": "2026-07-13",
        "iso_year": 2026,
        "iso_week": 29,
        "iso_weekday": 1,
        "parity": 1,
        "mode": "run",
        "reason": (
            "2026-07-13 is ISO week 29 of 2026 (odd parity=1); "
            "RUN_PARITY=1 -> run"
        ),
    }


def test_decide_fortnight_mode_skip_week_full_shape():
    decision = decide_fortnight_mode(date(2026, 7, 20))
    assert decision == {
        "date": "2026-07-20",
        "iso_year": 2026,
        "iso_week": 30,
        "iso_weekday": 1,
        "parity": 0,
        "mode": "skip",
        "reason": (
            "2026-07-20 is ISO week 30 of 2026 (even parity=0); "
            "RUN_PARITY=1 -> skip"
        ),
    }


@pytest.mark.parametrize(
    "d,expected_mode",
    [
        (date(2026, 7, 13), "run"),
        (date(2026, 7, 20), "skip"),
        (date(2026, 7, 27), "run"),
        (date(2026, 8, 3), "skip"),
    ],
)
def test_decide_fortnight_mode_alternates_week_to_week_ordinarily(d, expected_mode):
    assert decide_fortnight_mode(d)["mode"] == expected_mode


# ---------------------------------------------------------------------------
# Real year-boundary proofs
# ---------------------------------------------------------------------------

# Ten consecutive Sundays spanning the ORDINARY (52-ISO-week-year)
# 2027->2028 boundary. 2027 has 52 ISO weeks -- asserted directly below,
# not assumed -- so this is the common case: alternation must hold
# straight through the boundary with zero exceptions.
ORDINARY_BOUNDARY_SUNDAYS = [
    (date(2027, 12, 5), 2027, 48, 0, "skip"),
    (date(2027, 12, 12), 2027, 49, 1, "run"),
    (date(2027, 12, 19), 2027, 50, 0, "skip"),
    (date(2027, 12, 26), 2027, 51, 1, "run"),
    (date(2028, 1, 2), 2027, 52, 0, "skip"),
    (date(2028, 1, 9), 2028, 1, 1, "run"),
    (date(2028, 1, 16), 2028, 2, 0, "skip"),
    (date(2028, 1, 23), 2028, 3, 1, "run"),
    (date(2028, 1, 30), 2028, 4, 0, "skip"),
    (date(2028, 2, 6), 2028, 5, 1, "run"),
]


def test_2027_is_a_real_ordinary_52_iso_week_year():
    assert date(2027, 12, 28).isocalendar()[1] == 52


def test_ordinary_year_boundary_alternates_cleanly_2027_to_2028():
    for d, iso_year, iso_week, parity, mode in ORDINARY_BOUNDARY_SUNDAYS:
        decision = decide_fortnight_mode(d)
        assert (
            decision["iso_year"],
            decision["iso_week"],
            decision["parity"],
            decision["mode"],
        ) == (iso_year, iso_week, parity, mode), d

    modes = [row[-1] for row in ORDINARY_BOUNDARY_SUNDAYS]
    # Every single adjacent pair alternates -- no exceptions anywhere in
    # this ordinary-year boundary crossing.
    for a, b in zip(modes, modes[1:]):
        assert a != b


# Eight consecutive Sundays spanning the QUIRK (53-ISO-week-year)
# 2026->2027 boundary. 2026 has 53 ISO weeks -- confirmed live below, not
# assumed. 2027-01-03 (week 53 of 2026) and 2027-01-10 (week 1 of 2027)
# are BOTH odd, i.e. both "run" -- the module docstring's documented,
# deliberate quirk -- and this is the only adjacent pair in the whole
# sequence that shares a mode.
QUIRK_BOUNDARY_SUNDAYS = [
    (date(2026, 12, 6), 2026, 49, 1, "run"),
    (date(2026, 12, 13), 2026, 50, 0, "skip"),
    (date(2026, 12, 20), 2026, 51, 1, "run"),
    (date(2026, 12, 27), 2026, 52, 0, "skip"),
    (date(2027, 1, 3), 2026, 53, 1, "run"),
    (date(2027, 1, 10), 2027, 1, 1, "run"),  # <-- the quirk
    (date(2027, 1, 17), 2027, 2, 0, "skip"),
    (date(2027, 1, 24), 2027, 3, 1, "run"),
]


def test_2026_is_a_real_53_iso_week_quirk_year():
    assert date(2026, 12, 28).isocalendar()[1] == 53


def test_quirk_year_boundary_has_exactly_one_back_to_back_run_pair_2026_to_2027():
    decisions = [decide_fortnight_mode(d) for d, *_ in QUIRK_BOUNDARY_SUNDAYS]
    for (d, iso_year, iso_week, parity, mode), decision in zip(
        QUIRK_BOUNDARY_SUNDAYS, decisions
    ):
        assert (
            decision["iso_year"],
            decision["iso_week"],
            decision["parity"],
            decision["mode"],
        ) == (iso_year, iso_week, parity, mode), d

    modes = [d["mode"] for d in decisions]
    same_mode_adjacent_pairs = [
        i for i in range(len(modes) - 1) if modes[i] == modes[i + 1]
    ]
    assert same_mode_adjacent_pairs == [4]
    assert decisions[4]["date"] == "2027-01-03"
    assert decisions[5]["date"] == "2027-01-10"
    assert decisions[4]["mode"] == decisions[5]["mode"] == "run"


# ---------------------------------------------------------------------------
# write_github_output
# ---------------------------------------------------------------------------


def test_write_github_output_writes_expected_lines(tmp_path):
    out_file = tmp_path / "github_output"
    decision = decide_fortnight_mode(date(2026, 7, 13))
    write_github_output(decision, out_file)
    assert out_file.read_text(encoding="utf-8").splitlines() == [
        "mode=run",
        "iso_year=2026",
        "iso_week=29",
        "parity=1",
    ]


def test_write_github_output_appends_rather_than_overwrites(tmp_path):
    out_file = tmp_path / "github_output"
    out_file.write_text("preexisting=from-a-previous-step\n", encoding="utf-8")
    decision = decide_fortnight_mode(date(2026, 7, 20))
    write_github_output(decision, out_file)
    content = out_file.read_text(encoding="utf-8")
    assert content.startswith("preexisting=from-a-previous-step\n")
    assert "mode=skip\n" in content
    assert "iso_week=30\n" in content


# ---------------------------------------------------------------------------
# main() -- CLI plumbing
# ---------------------------------------------------------------------------


def test_main_with_explicit_date_prints_decision_and_returns_zero(capsys):
    exit_code = main(["--date", "2026-07-13"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "mode=run" in out
    assert "iso_year=2026 iso_week=29 parity=1" in out
    assert "reason: 2026-07-13 is ISO week 29 of 2026" in out


def test_main_with_explicit_skip_date(capsys):
    exit_code = main(["--date", "2026-07-20"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "mode=skip" in out


def test_main_writes_github_output_when_env_var_set(monkeypatch, tmp_path):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    exit_code = main(["--date", "2026-07-20"])
    assert exit_code == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == [
        "mode=skip",
        "iso_year=2026",
        "iso_week=30",
        "parity=0",
    ]


def test_main_does_not_touch_github_output_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    # Nothing to read back (there is no configured output path at all) --
    # this just confirms main() runs cleanly with no GITHUB_OUTPUT env var
    # present, matching a plain local/non-Actions invocation.
    exit_code = main(["--date", "2026-07-13"])
    assert exit_code == 0


def test_main_default_date_uses_default_today_seam(monkeypatch, capsys):
    # Monkeypatch the one real-clock seam so this test is deterministic
    # regardless of which real ISO week parity the suite happens to run
    # on -- the same "bind a real function, monkeypatch its name" pattern
    # already established elsewhere in this repo's test suite (e.g.
    # tests/test_auditor_cli.py's monkeypatched hn_items default path).
    monkeypatch.setattr(
        fortnight_guard_mod, "_default_today", lambda: date(2026, 7, 13)
    )
    exit_code = main([])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "iso_year=2026 iso_week=29 parity=1" in out
    assert "mode=run" in out
