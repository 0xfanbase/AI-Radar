"""Tests for scripts/plan_run.py -- the run-planner / degradation ladder.

Covers, in order: every degradation_level (0 normal, 1 capped, 2
every-other-day, 3 weekly digest), the unconditional empty-queue-skip case
at every level (queue emptiness always wins, regardless of level or date),
the level-2 day-of-year parity logic (including its known behavior across
a year boundary), proposed_card_id determinism/uniqueness, the
QUOTA_DEGRADATION_LEVEL environment-variable reader, and the
load/save/write schema-valid round trip. No live network anywhere -- every
queue here is a plain, hand-built list of dicts shaped like
queue.schema.json entries (cluster_hash/rank/sources[].title), matching
this module's own duck-typed reads.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from jsonschema import ValidationError

from scripts.plan_run import (
    CAPPED_CARDS_CAP,
    DEFAULT_DEGRADATION_LEVEL,
    DIGEST_CARDS_CAP,
    NORMAL_CARDS_CAP,
    PROFILE_STALE_THRESHOLD_DAYS,
    compute_proposed_card_id,
    compute_run_plan,
    decide_profile_target,
    decide_run_mode,
    find_board_upsert_candidate,
    find_stale_profile_candidate,
    kebab_slug,
    load_company_registry,
    load_run_plan,
    read_degradation_level,
    save_run_plan,
    write_run_plan,
)
from watcher.schema_validate import validate


def _entry(cluster_hash: str, rank: int, title: str = "A Sample Story Title") -> dict:
    """One minimal queue.schema.json-shaped entry -- only the fields this
    module actually reads (cluster_hash/rank/sources[0].title), same
    duck-typed-minimal style tests/test_ledger.py's own fixtures use.
    """
    return {
        "cluster_hash": cluster_hash,
        "rank": rank,
        "score": 100.0 - rank,
        "sources": [
            {
                "url": f"https://example.com/{cluster_hash}",
                "source_type": "hn",
                "title": title,
                "outlet": "example.com",
                "points": 100,
            }
        ],
    }


def _queue(n: int, *, title: str = "A Sample Story Title") -> list[dict]:
    return [_entry(f"hash{i:03d}", i, title=title) for i in range(1, n + 1)]


THURSDAY_EVEN_DOY = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)  # day-of-year 2
FRIDAY_ODD_DOY = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # day-of-year 1
MONDAY = datetime(2026, 7, 6, 8, 0, 0, tzinfo=timezone.utc)
NON_MONDAY = datetime(2026, 7, 9, 8, 0, 0, tzinfo=timezone.utc)  # Thursday


# --------------------------------------------------------------------------
# Unconditional empty-queue-skip -- regardless of degradation_level or date
# --------------------------------------------------------------------------


@pytest.mark.parametrize("degradation_level", [0, 1, 2, 3])
@pytest.mark.parametrize(
    "now",
    [
        THURSDAY_EVEN_DOY,  # level 2 would otherwise "skip" anyway
        FRIDAY_ODD_DOY,  # level 2 would otherwise "run" -- still must skip
        MONDAY,  # level 3 would otherwise "digest" -- still must skip
        NON_MONDAY,  # level 3 would otherwise "skip" anyway
    ],
)
def test_empty_queue_always_skips_regardless_of_level_or_date(degradation_level, now):
    plan = compute_run_plan([], degradation_level, now=now)
    assert plan["run_mode"] == "skip"
    assert plan["cards_cap"] is None
    assert plan["clusters"] == []
    assert "empty queue" in plan["reason"].lower()


def test_decide_run_mode_empty_queue_reason_does_not_mention_a_level_branch():
    # The empty-queue check must be structurally first, not merely a
    # fallthrough of level-specific logic -- assert the reason text is the
    # level-agnostic one, not e.g. a "capped"/"digest"/"parity" message.
    run_mode, cards_cap, clusters, reason = decide_run_mode([], 3, date(2026, 7, 6))
    assert run_mode == "skip"
    assert cards_cap is None
    assert clusters == []
    assert "digest" not in reason.lower()
    assert "parity" not in reason.lower()


# --------------------------------------------------------------------------
# Level 0 -- normal, up to 8 clusters
# --------------------------------------------------------------------------


def test_level_0_normal_includes_all_clusters_up_to_cap():
    queue = _queue(3)
    plan = compute_run_plan(queue, 0, now=THURSDAY_EVEN_DOY)
    assert plan["run_mode"] == "normal"
    assert plan["cards_cap"] == NORMAL_CARDS_CAP == 8
    assert len(plan["clusters"]) == 3
    assert [c["cluster_hash"] for c in plan["clusters"]] == [
        e["cluster_hash"] for e in queue
    ]


def test_level_0_normal_truncates_at_8_if_queue_somehow_has_more():
    # queue.schema.json caps at 8 in practice, but this function doesn't
    # itself re-validate the incoming queue -- assert the ladder's own cap
    # is enforced defensively regardless.
    queue = _queue(10)
    plan = compute_run_plan(queue, 0, now=THURSDAY_EVEN_DOY)
    assert plan["run_mode"] == "normal"
    assert len(plan["clusters"]) == 8


# --------------------------------------------------------------------------
# Level 1 -- capped, top-5 ranked clusters only
# --------------------------------------------------------------------------


def test_level_1_capped_keeps_only_top_5():
    queue = _queue(8)
    plan = compute_run_plan(queue, 1, now=THURSDAY_EVEN_DOY)
    assert plan["run_mode"] == "capped"
    assert plan["cards_cap"] == CAPPED_CARDS_CAP == 5
    assert len(plan["clusters"]) == 5
    assert [c["cluster_hash"] for c in plan["clusters"]] == [
        e["cluster_hash"] for e in queue[:5]
    ]


def test_level_1_capped_keeps_all_when_fewer_than_5_available():
    queue = _queue(3)
    plan = compute_run_plan(queue, 1, now=THURSDAY_EVEN_DOY)
    assert plan["run_mode"] == "capped"
    assert plan["cards_cap"] == 5
    assert len(plan["clusters"]) == 3


# --------------------------------------------------------------------------
# Level 2 -- every-other-day, day-of-year parity (NOT wall-clock time)
# --------------------------------------------------------------------------


def test_level_2_odd_day_of_year_runs_capped_top_5():
    # 2026-01-01 -> day-of-year 1 (odd) -> "run" parity.
    queue = _queue(8)
    plan = compute_run_plan(queue, 2, now=FRIDAY_ODD_DOY)
    assert plan["run_mode"] == "capped"
    assert plan["cards_cap"] == 5
    assert len(plan["clusters"]) == 5
    assert "day-of-year 1" in plan["reason"]
    assert "odd" in plan["reason"]


def test_level_2_even_day_of_year_skips():
    # 2026-01-02 -> day-of-year 2 (even) -> "skip" parity.
    queue = _queue(8)
    plan = compute_run_plan(queue, 2, now=THURSDAY_EVEN_DOY)
    assert plan["run_mode"] == "skip"
    assert plan["cards_cap"] is None
    assert plan["clusters"] == []
    assert "day-of-year 2" in plan["reason"]
    assert "even" in plan["reason"]


def test_level_2_alternates_within_a_single_year():
    queue = _queue(1)
    day_100 = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)  # day-of-year 100, even
    day_101 = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)  # day-of-year 101, odd

    plan_100 = compute_run_plan(queue, 2, now=day_100)
    plan_101 = compute_run_plan(queue, 2, now=day_101)

    assert plan_100["run_mode"] == "skip"
    assert plan_101["run_mode"] == "capped"


def test_level_2_day_of_year_parity_across_a_year_boundary():
    # 2026 is a 365-day (odd-length) year, so day-of-year parity does NOT
    # strictly alternate across the Dec-31 -> Jan-1 boundary: both
    # 2026-12-31 (day-of-year 365) and 2027-01-01 (day-of-year 1) are odd,
    # landing on the same "run" parity two days in a row -- a deliberate,
    # documented consequence of using day-of-year (not a continuous
    # ordinal-date) parity, per this module's own docstring.
    queue = _queue(1)
    dec_31 = datetime(2026, 12, 31, 12, 0, 0, tzinfo=timezone.utc)
    jan_1 = datetime(2027, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    assert date(2026, 12, 31).timetuple().tm_yday == 365
    assert date(2027, 1, 1).timetuple().tm_yday == 1

    plan_dec_31 = compute_run_plan(queue, 2, now=dec_31)
    plan_jan_1 = compute_run_plan(queue, 2, now=jan_1)

    # Both odd day-of-year -> both "run" (capped) -- NOT a skip/run
    # alternation across this particular boundary.
    assert plan_dec_31["run_mode"] == "capped"
    assert plan_jan_1["run_mode"] == "capped"


# --------------------------------------------------------------------------
# Level 3 -- weekly digest mode, designated weekday only (Monday)
# --------------------------------------------------------------------------


def test_level_3_on_monday_bundles_all_queued_clusters_into_digest():
    queue = _queue(8)
    assert date(2026, 7, 6).weekday() == 0  # Monday

    plan = compute_run_plan(queue, 3, now=MONDAY)
    assert plan["run_mode"] == "digest"
    assert plan["cards_cap"] == DIGEST_CARDS_CAP == 1
    assert len(plan["clusters"]) == 8  # ALL queued clusters, not capped to 1
    assert [c["cluster_hash"] for c in plan["clusters"]] == [
        e["cluster_hash"] for e in queue
    ]


def test_level_3_on_non_monday_skips():
    queue = _queue(8)
    assert date(2026, 7, 9).weekday() != 0  # Thursday, not Monday

    plan = compute_run_plan(queue, 3, now=NON_MONDAY)
    assert plan["run_mode"] == "skip"
    assert plan["cards_cap"] is None
    assert plan["clusters"] == []


# --------------------------------------------------------------------------
# Invalid degradation_level (defensive)
# --------------------------------------------------------------------------


def test_decide_run_mode_rejects_out_of_range_level():
    with pytest.raises(ValueError):
        decide_run_mode(_queue(1), 4, date(2026, 7, 9))


# --------------------------------------------------------------------------
# proposed_card_id: date + kebab-slug(title) + "-" + cluster_hash[:6]
# --------------------------------------------------------------------------


def test_kebab_slug_lowercases_and_hyphenates():
    assert kebab_slug("Introducing GPT-5.5") == "introducing-gpt-5-5"


def test_kebab_slug_handles_unicode_punctuation_like_non_breaking_hyphen():
    # The real fixture data (data/queue.json) contains a title with a
    # U+2011 non-breaking hyphen ("GPT‑Live") -- must degrade to a
    # plain ASCII hyphen, not crash or leave the raw codepoint in place.
    assert kebab_slug("GPT‑Live") == "gpt-live"


def test_kebab_slug_falls_back_to_untitled_for_no_alnum_content():
    assert kebab_slug("!!!") == "untitled"
    assert kebab_slug("") == "untitled"


def test_compute_proposed_card_id_shape():
    card_id = compute_proposed_card_id(
        date(2026, 7, 9), "Introducing GPT-5.5", "abcdef0123456789"
    )
    assert card_id == "2026-07-09-introducing-gpt-5-5-abcdef"


def test_proposed_card_id_is_deterministic_across_repeated_calls():
    args = (date(2026, 7, 9), "Some Model Release", "0123456789abcdef")
    assert compute_proposed_card_id(*args) == compute_proposed_card_id(*args)

    queue = _queue(1, title="Some Model Release")
    plan_a = compute_run_plan(queue, 0, now=THURSDAY_EVEN_DOY)
    plan_b = compute_run_plan(queue, 0, now=THURSDAY_EVEN_DOY)
    assert plan_a["clusters"] == plan_b["clusters"]


def test_proposed_card_id_is_unique_across_clusters_with_identical_titles():
    # Two distinct clusters sharing the exact same title must still get
    # distinct proposed_card_ids, thanks to the differing cluster_hash[:6]
    # suffix -- the slug alone is not relied on for uniqueness.
    queue = [
        _entry("hash_aaaaaa111111", 1, title="Introducing GPT-5.5"),
        _entry("hash_bbbbbb222222", 2, title="Introducing GPT-5.5"),
    ]
    plan = compute_run_plan(queue, 0, now=THURSDAY_EVEN_DOY)
    ids = [c["proposed_card_id"] for c in plan["clusters"]]
    assert len(ids) == len(set(ids)) == 2
    assert ids[0] != ids[1]


def test_proposed_card_id_uses_todays_date_from_now_not_wall_clock():
    queue = _queue(1)
    fixed_now = datetime(2030, 3, 3, 5, 0, 0, tzinfo=timezone.utc)
    plan = compute_run_plan(queue, 0, now=fixed_now)
    assert plan["clusters"][0]["proposed_card_id"].startswith("2030-03-03-")


# --------------------------------------------------------------------------
# QUOTA_DEGRADATION_LEVEL environment-variable reader
# --------------------------------------------------------------------------


def test_read_degradation_level_defaults_to_0_when_unset():
    assert read_degradation_level(env={}) == DEFAULT_DEGRADATION_LEVEL == 0


def test_read_degradation_level_defaults_to_0_when_blank():
    assert read_degradation_level(env={"QUOTA_DEGRADATION_LEVEL": "  "}) == 0


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_read_degradation_level_parses_valid_values(level):
    assert read_degradation_level(env={"QUOTA_DEGRADATION_LEVEL": str(level)}) == level


def test_read_degradation_level_defaults_to_0_on_unparseable_value():
    assert read_degradation_level(env={"QUOTA_DEGRADATION_LEVEL": "not-a-number"}) == 0


def test_read_degradation_level_clamps_above_range():
    assert read_degradation_level(env={"QUOTA_DEGRADATION_LEVEL": "9"}) == 3


def test_read_degradation_level_clamps_below_range():
    assert read_degradation_level(env={"QUOTA_DEGRADATION_LEVEL": "-5"}) == 0


# --------------------------------------------------------------------------
# Schema conformance
# --------------------------------------------------------------------------


@pytest.mark.parametrize("degradation_level", [0, 1, 2, 3])
def test_compute_run_plan_output_validates_against_schema(degradation_level):
    queue = _queue(8)
    plan = compute_run_plan(queue, degradation_level, now=MONDAY)
    validate(plan, "run_plan")  # raises on failure


def test_compute_run_plan_skip_output_validates_against_schema():
    plan = compute_run_plan([], 0, now=THURSDAY_EVEN_DOY)
    validate(plan, "run_plan")


# --------------------------------------------------------------------------
# load_run_plan / save_run_plan / write_run_plan -- schema-valid round trip
# --------------------------------------------------------------------------


def test_load_run_plan_missing_file_returns_none(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert load_run_plan(missing) is None


def test_save_then_load_round_trips_and_is_schema_valid(tmp_path):
    path = tmp_path / "run_plan.json"
    queue = _queue(2)
    plan = compute_run_plan(queue, 0, now=THURSDAY_EVEN_DOY)

    save_run_plan(plan, path)
    assert path.is_file()

    loaded = load_run_plan(path)
    assert loaded == plan


def test_save_run_plan_rejects_schema_invalid_payload_and_writes_nothing(tmp_path):
    path = tmp_path / "run_plan.json"
    bad = {"version": 1, "run_mode": "not-a-real-mode"}  # missing required fields

    with pytest.raises(ValidationError):
        save_run_plan(bad, path)

    assert not path.exists()


def test_write_run_plan_builds_saves_and_returns_payload(tmp_path):
    path = tmp_path / "run_plan.json"
    queue = _queue(3)

    result = write_run_plan(queue, 1, now=THURSDAY_EVEN_DOY, path=path)

    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == result
    assert result["run_mode"] == "capped"
    assert len(result["clusters"]) == 3


# --------------------------------------------------------------------------
# Phase 8: deterministic PROFILER-target selection
# --------------------------------------------------------------------------

ANTHROPIC = {
    "id": "anthropic",
    "name": "Anthropic",
    "aliases": ["Anthropic PBC"],
    "last_verified": "2026-07-01",
}
MISTRAL = {
    "id": "mistral",
    "name": "Mistral AI",
    "aliases": ["Mistral"],
    "last_verified": "2026-05-01",
}
OPENAI = {
    "id": "openai",
    "name": "OpenAI",
    "aliases": [],
    "last_verified": "2026-06-15",
}

COMPANIES = [ANTHROPIC, MISTRAL, OPENAI]


# --- find_board_upsert_candidate ---


def test_find_board_upsert_candidate_matches_company_name_in_title():
    selected = [_entry("h1", 1, title="Anthropic ships a new agentic model")]
    assert find_board_upsert_candidate(selected, COMPANIES) == "anthropic"


def test_find_board_upsert_candidate_matches_alias():
    selected = [_entry("h1", 1, title="Mistral AI announces open weights release")]
    assert find_board_upsert_candidate(selected, COMPANIES) == "mistral"


def test_find_board_upsert_candidate_word_boundary_avoids_false_positive():
    # "AI" must never spuriously match inside "OpenAI2" or similar -- only
    # a real word-boundary mention of a registered name/alias counts. Here
    # no registered company name/alias appears as a whole word/phrase.
    selected = [_entry("h1", 1, title="SuperAI2 unveils a new benchmark result")]
    assert find_board_upsert_candidate(selected, COMPANIES) is None


def test_find_board_upsert_candidate_case_insensitive():
    selected = [_entry("h1", 1, title="ANTHROPIC releases something big today")]
    assert find_board_upsert_candidate(selected, COMPANIES) == "anthropic"


def test_find_board_upsert_candidate_checks_clusters_in_order():
    selected = [
        _entry("h1", 1, title="Nothing relevant here at all"),
        _entry("h2", 2, title="OpenAI updates its flagship model"),
    ]
    assert find_board_upsert_candidate(selected, COMPANIES) == "openai"


def test_find_board_upsert_candidate_no_match_returns_none():
    selected = [_entry("h1", 1, title="A generic AI story about nothing specific")]
    assert find_board_upsert_candidate(selected, COMPANIES) is None


def test_find_board_upsert_candidate_empty_selected_returns_none():
    assert find_board_upsert_candidate([], COMPANIES) is None


def test_find_board_upsert_candidate_empty_companies_returns_none():
    selected = [_entry("h1", 1, title="Anthropic ships something")]
    assert find_board_upsert_candidate(selected, []) is None


# --- find_stale_profile_candidate ---


def test_find_stale_profile_candidate_returns_oldest_when_stale_enough():
    today = date(2026, 7, 13)
    # mistral's last_verified (2026-05-01) is well over 45 days before
    # today; it's the oldest of the three.
    assert find_stale_profile_candidate(COMPANIES, today) == "mistral"


def test_find_stale_profile_candidate_returns_none_when_oldest_is_fresh():
    today = date(2026, 5, 5)  # mistral's 2026-05-01 is only 4 days old here
    assert find_stale_profile_candidate(COMPANIES, today) is None


def test_find_stale_profile_candidate_exact_threshold_boundary_is_not_stale():
    # Exactly PROFILE_STALE_THRESHOLD_DAYS days old is NOT "older than" the
    # threshold -- the rule is a strict ">", matching board.py's own
    # is_pulse_eligible boundary-inclusive-the-other-way convention stated
    # explicitly (">45 days" means 46+ days triggers it, not 45 exactly).
    companies = [{"id": "solo", "name": "Solo Labs", "aliases": [], "last_verified": "2026-01-01"}]
    today = date(2026, 1, 1) + timedelta(days=PROFILE_STALE_THRESHOLD_DAYS)
    assert find_stale_profile_candidate(companies, today) is None
    today_plus_one = today + timedelta(days=1)
    assert find_stale_profile_candidate(companies, today_plus_one) == "solo"


def test_find_stale_profile_candidate_ties_break_on_id_ascending():
    companies = [
        {"id": "zeta", "name": "Zeta", "aliases": [], "last_verified": "2026-01-01"},
        {"id": "alpha", "name": "Alpha", "aliases": [], "last_verified": "2026-01-01"},
    ]
    today = date(2026, 1, 1) + timedelta(days=PROFILE_STALE_THRESHOLD_DAYS + 1)
    assert find_stale_profile_candidate(companies, today) == "alpha"


def test_find_stale_profile_candidate_empty_companies_returns_none():
    assert find_stale_profile_candidate([], date(2026, 7, 13)) is None


def test_find_stale_profile_candidate_skips_missing_or_bad_last_verified():
    companies = [
        {"id": "no-date", "name": "No Date", "aliases": []},
        {"id": "bad-date", "name": "Bad Date", "aliases": [], "last_verified": "not-a-date"},
    ]
    assert find_stale_profile_candidate(companies, date(2026, 7, 13)) is None


# --- decide_profile_target ---


def test_decide_profile_target_skip_run_mode_always_none():
    result, reason = decide_profile_target(
        "skip", [], COMPANIES, date(2026, 7, 13)
    )
    assert result is None
    assert "skip" in reason.lower()


def test_decide_profile_target_prefers_board_upsert_candidate_over_stale():
    selected = [_entry("h1", 1, title="OpenAI announces a big update")]
    result, reason = decide_profile_target(
        "normal", selected, COMPANIES, date(2026, 7, 13)
    )
    assert result == "openai"
    assert "openai" in reason.lower()


def test_decide_profile_target_falls_back_to_stale_when_no_cluster_match():
    selected = [_entry("h1", 1, title="A story mentioning nobody tracked")]
    result, reason = decide_profile_target(
        "normal", selected, COMPANIES, date(2026, 7, 13)
    )
    assert result == "mistral"
    assert "stale" in reason.lower()


def test_decide_profile_target_none_when_no_candidate_and_nothing_stale():
    selected = [_entry("h1", 1, title="A story mentioning nobody tracked")]
    result, reason = decide_profile_target(
        "normal", selected, COMPANIES, date(2026, 5, 5)
    )
    assert result is None
    assert "no profile is targeted" in reason.lower()


# --- compute_run_plan wiring ---


def test_compute_run_plan_defaults_to_no_profile_target_without_companies():
    queue = _queue(3)
    plan = compute_run_plan(queue, 0, now=THURSDAY_EVEN_DOY)
    assert plan["profile_target"] is None
    assert isinstance(plan["profile_reason"], str) and plan["profile_reason"]


def test_compute_run_plan_threads_companies_into_profile_target():
    queue = [_entry("h1", 1, title="Anthropic launches a new flagship model")]
    plan = compute_run_plan(
        queue, 0, now=datetime(2026, 7, 13, 8, 0, 0, tzinfo=timezone.utc), companies=COMPANIES
    )
    assert plan["profile_target"] == "anthropic"


def test_compute_run_plan_skip_run_forces_profile_target_none_even_with_stale_company():
    # Empty queue -> run_mode "skip" -- profile_target must be None
    # regardless of how stale the registry's companies are.
    plan = compute_run_plan(
        [], 0, now=datetime(2026, 7, 13, 8, 0, 0, tzinfo=timezone.utc), companies=COMPANIES
    )
    assert plan["run_mode"] == "skip"
    assert plan["profile_target"] is None


def test_compute_run_plan_output_with_profile_target_validates_against_schema():
    queue = [_entry("h1", 1, title="Anthropic launches a new flagship model")]
    plan = compute_run_plan(
        queue, 0, now=datetime(2026, 7, 13, 8, 0, 0, tzinfo=timezone.utc), companies=COMPANIES
    )
    validate(plan, "run_plan")


# --- load_company_registry ---


def test_load_company_registry_missing_dir_returns_empty_list(tmp_path):
    assert load_company_registry(tmp_path / "does-not-exist") == []


def test_load_company_registry_skips_index_json(tmp_path):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "index.json").write_text('{"version": 1, "companies": []}')
    (companies_dir / "anthropic.json").write_text(json.dumps(ANTHROPIC))

    companies = load_company_registry(companies_dir)

    assert len(companies) == 1
    assert companies[0]["id"] == "anthropic"


def test_load_company_registry_against_the_real_seeded_registry():
    # Real content/companies/*.json -- 13 seeded profiles per Phase 6.
    companies = load_company_registry()
    assert len(companies) == 13
    ids = {c["id"] for c in companies}
    assert "anthropic" in ids
    assert "deepseek" in ids
