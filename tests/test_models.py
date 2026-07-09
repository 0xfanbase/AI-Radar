"""Tests for watcher/models.py's pure helpers: tokenize_title (and its
dotted-version-number fix from the Phase 1 PM checkpoint) and
normalize_url's basic contract.
"""
from __future__ import annotations

from watcher.models import normalize_url, tokenize_title


# --------------------------------------------------------------------------
# tokenize_title -- dotted version numbers stay distinct tokens (Phase 1
# PM checkpoint fix: the original `[a-z0-9]+` word regex split "5.5" into
# two separate "5" tokens that a frozenset then collapsed into the same
# single token already produced by "GPT-5", making the two titles
# indistinguishable (Jaccard 1.0) despite being different releases).
# --------------------------------------------------------------------------


def test_dotted_version_number_is_kept_as_one_token():
    assert "5.5" in tokenize_title("Introducing GPT-5.5")
    assert "4.1" in tokenize_title("Introducing GPT-4.1 in the API")


def test_gpt_5_and_gpt_5_5_no_longer_tokenize_identically():
    # Before the fix: both tokenized to {"introducing", "gpt", "5"} --
    # Jaccard 1.0. After the fix, "5.5" is its own distinct token.
    tokens_5 = tokenize_title("Introducing GPT-5")
    tokens_5_5 = tokenize_title("Introducing GPT-5.5")

    assert tokens_5 != tokens_5_5
    assert "5" in tokens_5 and "5" not in tokens_5_5
    assert "5.5" in tokens_5_5 and "5.5" not in tokens_5


def test_gpt_5_4_and_gpt_5_2_are_distinct_tokens():
    tokens_5_4 = tokenize_title("Introducing GPT-5.4")
    tokens_5_2 = tokenize_title("Introducing GPT-5.2")

    assert "5.4" in tokens_5_4 and "5.4" not in tokens_5_2
    assert "5.2" in tokens_5_2 and "5.2" not in tokens_5_4


def test_trailing_period_is_not_absorbed_into_the_last_word():
    # A sentence-ending period must not be swallowed into a dotted-number
    # token match -- there is no further alphanumeric run after it.
    tokens = tokenize_title("A new way to write and code with ChatGPT.")
    assert "chatgpt" in tokens
    assert "chatgpt." not in tokens


def test_single_digit_version_numbers_still_kept():
    # Pre-existing behavior (single, undotted digits) must be unaffected.
    assert "2" in tokenize_title("Llama 2")
    assert "3" in tokenize_title("Llama 3")


# --------------------------------------------------------------------------
# normalize_url -- baseline sanity (already exercised indirectly by
# tests/test_ledger.py and tests/test_clustering.py; this covers the
# function directly and in isolation).
# --------------------------------------------------------------------------


def test_normalize_url_strips_tracking_params_and_www_and_trailing_slash():
    a = normalize_url("https://www.example.com/story/?utm_source=twitter")
    b = normalize_url("https://example.com/story")
    assert a == b


def test_normalize_url_drops_fragment():
    assert normalize_url("https://example.com/story#section") == normalize_url(
        "https://example.com/story"
    )
