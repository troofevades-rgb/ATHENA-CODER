"""Tests for athena.tools.fuzzy_match (T2-07.6)."""

from __future__ import annotations

from athena.tools.fuzzy_match import find_fuzzy_matches


def test_verbatim_match_returned() -> None:
    matches = find_fuzzy_matches("hello world", "world")
    assert len(matches) == 1
    assert matches[0].score == 1.0
    assert matches[0].matched_text == "world"
    assert matches[0].start == 6


def test_verbatim_multiple_matches() -> None:
    matches = find_fuzzy_matches("ababab", "ab")
    # Three verbatim occurrences at positions 0, 2, 4.
    assert len(matches) == 3
    assert {m.start for m in matches} == {0, 2, 4}
    assert all(m.score == 1.0 for m in matches)


def test_close_match_above_threshold() -> None:
    haystack = "the quick brown fox jumps over the lazy dog"
    needle = "quick brown fox jumps"  # verbatim — short-circuit
    matches = find_fuzzy_matches(haystack, needle, threshold=0.9)
    assert len(matches) == 1
    assert matches[0].score == 1.0


def test_typo_match() -> None:
    """Single-character difference between same-length needle and
    surrounding haystack: score lands above 0.9."""
    needle = "function definitionX"  # 20 chars
    haystack = "function definitionY etc etc etc"
    matches = find_fuzzy_matches(haystack, needle, threshold=0.9)
    assert len(matches) == 1
    assert matches[0].score >= 0.9
    assert matches[0].score < 1.0


def test_no_match_below_threshold() -> None:
    matches = find_fuzzy_matches("nothing like it here", "completely different", threshold=0.95)
    assert matches == []


def test_empty_needle_returns_empty() -> None:
    assert find_fuzzy_matches("anything", "") == []


def test_needle_longer_than_haystack_returns_empty() -> None:
    assert find_fuzzy_matches("ab", "abcdef") == []


def test_two_near_matches_both_returned() -> None:
    """Two separate occurrences of the same near-match in haystack
    both surface (caller is responsible for erroring on >1)."""
    haystack = "Mike's file at /tmp/a then Mike's file at /tmp/b"
    needle = "Mike's file"  # verbatim — two occurrences
    matches = find_fuzzy_matches(haystack, needle, threshold=0.9)
    assert len(matches) == 2


def test_threshold_boundary() -> None:
    """A threshold of 1.0 only accepts verbatim matches."""
    needle = "function definitionX"
    haystack = "function definitionY etc etc etc"
    matches = find_fuzzy_matches(haystack, needle, threshold=1.0)
    assert matches == []


def test_low_threshold_accepts_loose_match() -> None:
    """A threshold of 0.5 accepts more matches than 0.95."""
    haystack = "the quick red fox jumps over the lazy dog"
    needle = "the quick brown fox"
    # 0.95 should reject (different word "red" vs "brown").
    strict = find_fuzzy_matches(haystack, needle, threshold=0.95)
    # 0.7 should pick it up.
    loose = find_fuzzy_matches(haystack, needle, threshold=0.7)
    assert len(strict) == 0
    assert len(loose) >= 1


def test_default_threshold_is_0_95() -> None:
    """No threshold kwarg uses the documented 0.95 default."""
    needle = "function definitionX"
    haystack = "function definitionY etc etc etc"
    matches = find_fuzzy_matches(haystack, needle)
    # 20-char string with one-char difference -> ratio == 0.95.
    assert len(matches) == 1


def test_match_text_is_the_haystack_slice() -> None:
    """matched_text reflects what was actually in the haystack
    (the near-match), not the needle. Useful for debugging."""
    needle = "function definitionX"
    haystack = "function definitionY etc etc etc"
    matches = find_fuzzy_matches(haystack, needle, threshold=0.9)
    assert len(matches) == 1
    assert matches[0].matched_text == "function definitionY"
