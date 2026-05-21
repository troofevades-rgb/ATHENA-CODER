"""T7-03.1 — scorers + dataclasses tests."""

from __future__ import annotations

import json

import pytest

from athena.eval.scorers import (
    Score,
    get_scorer,
    list_scorers,
    register_scorer,
)
from athena.eval.summary import (
    EvalCase,
    EvalScore,
    EvalSummary,
    excerpt,
    mint_case_id,
    mint_eval_id,
)


# ---------------------------------------------------------------
# Registry
# ---------------------------------------------------------------


def test_list_scorers_includes_builtins():
    names = list_scorers()
    assert {"exact", "contains", "regex", "json_path"} <= set(names)


def test_get_scorer_unknown_raises_with_helpful_message():
    with pytest.raises(KeyError, match="unknown scorer"):
        get_scorer("nonexistent")


def test_register_then_get_round_trip():
    def _custom(actual, expected, *, context):
        return Score(passed=True, score=1.0, details="custom always passes")

    register_scorer("test_custom", _custom)
    try:
        fn = get_scorer("test_custom")
        result = fn("anything", "anything", context={})
        assert result.passed is True
        assert "custom" in result.details
    finally:
        # Clean up so other tests don't see it.
        from athena.eval.scorers import _REGISTRY
        _REGISTRY.pop("test_custom", None)


def test_register_empty_name_rejected():
    with pytest.raises(ValueError):
        register_scorer("", lambda a, e, **kw: Score(True, 1.0, "x"))


# ---------------------------------------------------------------
# exact
# ---------------------------------------------------------------


def test_exact_match_passes():
    fn = get_scorer("exact")
    s = fn("hello", "hello", context={})
    assert s.passed is True
    assert s.score == 1.0


def test_exact_strips_whitespace():
    fn = get_scorer("exact")
    assert fn("  hello\n", "hello", context={}).passed is True


def test_exact_case_sensitive():
    fn = get_scorer("exact")
    assert fn("Hello", "hello", context={}).passed is False


def test_exact_fail_details_quotes_both_sides():
    fn = get_scorer("exact")
    s = fn("apple", "banana", context={})
    assert s.passed is False
    assert '"apple"' in s.details
    assert '"banana"' in s.details


# ---------------------------------------------------------------
# contains
# ---------------------------------------------------------------


def test_contains_passes_when_substring_present():
    fn = get_scorer("contains")
    s = fn("the answer is 42", "42", context={})
    assert s.passed is True
    assert s.score == 1.0


def test_contains_fails_when_absent():
    fn = get_scorer("contains")
    assert fn("the answer is 42", "43", context={}).passed is False


def test_contains_empty_expected_fails():
    """Empty expected → no signal; explicitly fail rather than
    accidentally pass everything."""
    fn = get_scorer("contains")
    s = fn("anything", "", context={})
    assert s.passed is False
    assert "empty" in s.details


# ---------------------------------------------------------------
# regex
# ---------------------------------------------------------------


def test_regex_matches():
    fn = get_scorer("regex")
    s = fn("error: 404 not found", r"\b404\b", context={})
    assert s.passed is True
    assert "404" in s.details


def test_regex_no_match_fails():
    fn = get_scorer("regex")
    assert fn("hello world", r"^\d+$", context={}).passed is False


def test_regex_invalid_pattern_fails_cleanly():
    fn = get_scorer("regex")
    s = fn("anything", "[unclosed", context={})
    assert s.passed is False
    assert "invalid regex" in s.details


def test_regex_empty_pattern_fails():
    fn = get_scorer("regex")
    s = fn("anything", "", context={})
    assert s.passed is False
    assert "empty" in s.details


def test_regex_case_insensitive_inline_flag():
    fn = get_scorer("regex")
    assert fn("HELLO", "(?i)hello", context={}).passed is True


# ---------------------------------------------------------------
# json_path
# ---------------------------------------------------------------


def test_json_path_passes_on_simple_dict():
    fn = get_scorer("json_path")
    actual = '{"status": "ok", "value": 42}'
    s = fn(actual, {"path": "value", "value": 42}, context={})
    assert s.passed is True


def test_json_path_fails_on_value_mismatch():
    fn = get_scorer("json_path")
    actual = '{"value": 42}'
    s = fn(actual, {"path": "value", "value": 7}, context={})
    assert s.passed is False
    assert "42" in s.details
    assert "7" in s.details


def test_json_path_navigates_nested():
    fn = get_scorer("json_path")
    actual = '{"data": {"users": [{"id": 1}, {"id": 2}]}}'
    s = fn(actual, {"path": "data.users[1].id", "value": 2}, context={})
    assert s.passed is True


def test_json_path_fails_on_invalid_json():
    fn = get_scorer("json_path")
    s = fn("not json", {"path": "x", "value": 1}, context={})
    assert s.passed is False
    assert "not valid JSON" in s.details


def test_json_path_fails_when_path_missing():
    fn = get_scorer("json_path")
    actual = '{"a": 1}'
    s = fn(actual, {"path": "b", "value": 1}, context={})
    assert s.passed is False
    assert "did not resolve" in s.details


def test_json_path_requires_dict_expected_shape():
    fn = get_scorer("json_path")
    s = fn('{"x": 1}', "just a string", context={})
    assert s.passed is False
    assert "scorer requires" in s.details


def test_json_path_handles_top_level_array():
    fn = get_scorer("json_path")
    actual = "[10, 20, 30]"
    s = fn(actual, {"path": "[1]", "value": 20}, context={})
    assert s.passed is True


def test_json_path_value_can_be_complex():
    """Expected value isn't limited to scalars — nested
    objects work too."""
    fn = get_scorer("json_path")
    actual = '{"data": {"nested": {"key": "value"}}}'
    s = fn(actual, {
        "path": "data.nested",
        "value": {"key": "value"},
    }, context={})
    assert s.passed is True


# ---------------------------------------------------------------
# Score dataclass
# ---------------------------------------------------------------


def test_score_to_dict_shape():
    s = Score(passed=True, score=0.95, details="close enough")
    d = s.to_dict()
    assert d == {"passed": True, "score": 0.95, "details": "close enough"}


def test_score_rounds_to_4_decimals():
    s = Score(passed=False, score=0.123456789, details="x")
    assert s.to_dict()["score"] == 0.1235


# ---------------------------------------------------------------
# EvalCase
# ---------------------------------------------------------------


def test_evalcase_from_dict_required_fields():
    c = EvalCase.from_dict({
        "task": "what is 2+2?",
        "expected": "4",
    })
    assert c.task == "what is 2+2?"
    assert c.expected == "4"
    assert c.case_id is None
    assert c.scorer is None


def test_evalcase_from_dict_all_fields():
    c = EvalCase.from_dict({
        "task": "T",
        "expected": "E",
        "case_id": "e-explicit",
        "scorer": "regex",
        "cwd": "/tmp",
        "timeout_s": 30.5,
        "model": "m",
    })
    assert c.case_id == "e-explicit"
    assert c.scorer == "regex"
    assert c.timeout_s == 30.5


def test_evalcase_preserves_extras():
    """Unknown keys → c.extras so custom scorers can read them
    off the case context."""
    c = EvalCase.from_dict({
        "task": "T", "expected": "E",
        "category": "math", "difficulty": "hard",
    })
    assert c.extras == {"category": "math", "difficulty": "hard"}


def test_evalcase_rejects_empty_task():
    with pytest.raises(ValueError, match="task"):
        EvalCase.from_dict({"task": "", "expected": "x"})


def test_evalcase_rejects_missing_expected():
    with pytest.raises(ValueError, match="expected"):
        EvalCase.from_dict({"task": "x"})


def test_evalcase_accepts_complex_expected():
    """expected can be any JSON-safe value — int, dict, list, bool."""
    for v in [42, [1, 2], {"k": "v"}, True, None]:
        c = EvalCase.from_dict({"task": "t", "expected": v})
        assert c.expected == v


# ---------------------------------------------------------------
# mint_case_id / mint_eval_id
# ---------------------------------------------------------------


def test_mint_case_id_format():
    assert mint_case_id().startswith("e-")
    assert len(mint_case_id()) == 2 + 12


def test_mint_eval_id_format():
    assert mint_eval_id().startswith("v-")
    assert len(mint_eval_id()) == 2 + 12


def test_minted_ids_unique():
    ids = {mint_case_id() for _ in range(20)}
    assert len(ids) == 20


# ---------------------------------------------------------------
# EvalScore + EvalSummary to_dict
# ---------------------------------------------------------------


def test_evalscore_to_dict_shape():
    s = EvalScore(
        case_id="e-1", run_id="r-1",
        task_excerpt="t", actual_excerpt="a",
        passed=True, score=1.0, scorer="exact",
        details="match",
        run_status="ok",
        envelope_path="/tmp/r-1.json",
    )
    d = s.to_dict()
    assert set(d.keys()) >= {
        "case_id", "run_id", "task_excerpt", "actual_excerpt",
        "passed", "score", "scorer", "details",
        "run_status", "envelope_path",
    }


def test_evalsummary_to_dict_baseline_fields_only_when_set():
    """baseline_id / regressions / improvements only appear in
    the dict when baseline_id is set — keeps the summary clean
    when no baseline was passed."""
    s = EvalSummary(
        eval_id="v-1", batch_id="b-1",
        started_at="2026-05-21T00:00:00.000000Z",
        finished_at="2026-05-21T00:00:00.000000Z",
        duration_s=1.0,
        output_dir="/tmp/out",
        total=0, passed=0, failed=0, errored=0,
        pass_rate=0.0, avg_score=0.0,
    )
    d = s.to_dict()
    assert "baseline_id" not in d
    assert "regressions" not in d
    assert "improvements" not in d


def test_evalsummary_to_dict_with_baseline():
    s = EvalSummary(
        eval_id="v-1", batch_id="b-1",
        started_at="2026-05-21T00:00:00.000000Z",
        finished_at="2026-05-21T00:00:00.000000Z",
        duration_s=1.0,
        output_dir="/tmp/out",
        total=2, passed=1, failed=1, errored=0,
        pass_rate=0.5, avg_score=0.5,
        baseline_id="v-baseline",
        regressions=["e-1"],
        improvements=["e-3"],
    )
    d = s.to_dict()
    assert d["baseline_id"] == "v-baseline"
    assert d["regressions"] == ["e-1"]
    assert d["improvements"] == ["e-3"]


def test_evalsummary_to_json_round_trip():
    s = EvalSummary(
        eval_id="v-x", batch_id="b-x",
        started_at="2026-05-21T00:00:00.000000Z",
        finished_at="2026-05-21T00:00:00.000000Z",
        duration_s=0.0, output_dir="/o",
        total=0, passed=0, failed=0, errored=0,
        pass_rate=0.0, avg_score=0.0,
    )
    parsed = json.loads(s.to_json())
    assert parsed["eval_id"] == "v-x"


# ---------------------------------------------------------------
# excerpt helper
# ---------------------------------------------------------------


def test_excerpt_strips_newlines():
    assert excerpt("line\none\nline two") == "line one line two"


def test_excerpt_truncates_long_string():
    long = "x" * 300
    out = excerpt(long, limit=50)
    assert len(out) == 50
    assert out.endswith("…")


def test_excerpt_handles_none():
    assert excerpt(None) == ""
