"""Integration tests for Edit/str_replace fuzzy fallback (T2-07.7)."""

from __future__ import annotations

from pathlib import Path

from athena.tools.file_ops import Edit


def test_str_replace_fuzzy_disabled_by_default(tmp_path: Path) -> None:
    """A mistyped old_string fails with the standard error when
    fuzzy=False (the default). No silent rescue."""
    f = tmp_path / "a.txt"
    f.write_text("function definitionX etc etc etc\n", encoding="utf-8")

    result = Edit(
        file_path=str(f),
        old_string="function definitionY",  # typo: Y not X
        new_string="function definitionZ",
    )
    assert result.startswith("ERROR:")
    assert "fuzzy=true" in result
    # File unchanged.
    assert f.read_text(encoding="utf-8") == "function definitionX etc etc etc\n"


def test_str_replace_fuzzy_finds_typo(tmp_path: Path) -> None:
    """fuzzy=True recovers a one-char typo and applies the substitution."""
    f = tmp_path / "a.txt"
    f.write_text("function definitionX etc etc etc\n", encoding="utf-8")

    result = Edit(
        file_path=str(f),
        old_string="function definitionY",  # typo
        new_string="REPLACED",
        fuzzy=True,
    )
    assert "ERROR" not in result
    assert "fuzzy: score=" in result
    assert f.read_text(encoding="utf-8") == "REPLACED etc etc etc\n"


def test_str_replace_fuzzy_errors_on_multiple_matches(tmp_path: Path) -> None:
    """Two near-matches above threshold -> error (don't silently
    pick the first)."""
    f = tmp_path / "a.txt"
    # Two separate near-matches of "function definitionY":
    f.write_text(
        "function definitionA etc\nfunction definitionB etc\n",
        encoding="utf-8",
    )

    result = Edit(
        file_path=str(f),
        old_string="function definitionX",  # close to both
        new_string="REPLACED",
        fuzzy=True,
        fuzzy_threshold=0.9,
    )
    assert result.startswith("ERROR:")
    assert "fuzzy matches" in result
    # File unchanged.
    assert "definitionA" in f.read_text(encoding="utf-8")
    assert "definitionB" in f.read_text(encoding="utf-8")


def test_str_replace_fuzzy_errors_below_threshold(tmp_path: Path) -> None:
    """No near-match above threshold -> error, not a guess."""
    f = tmp_path / "a.txt"
    f.write_text("completely unrelated content here\n", encoding="utf-8")

    result = Edit(
        file_path=str(f),
        old_string="something nothing like the file",
        new_string="REPLACED",
        fuzzy=True,
        fuzzy_threshold=0.95,
    )
    assert result.startswith("ERROR:")
    assert "no fuzzy match" in result
    # File unchanged.
    assert f.read_text(encoding="utf-8") == "completely unrelated content here\n"


def test_str_replace_exact_match_unchanged_with_fuzzy_true(tmp_path: Path) -> None:
    """Exact-match path runs even when fuzzy=True is set, so enabling
    fuzzy doesn't slow down or change behaviour on a clean match."""
    f = tmp_path / "a.txt"
    f.write_text("hello world\n", encoding="utf-8")

    result = Edit(
        file_path=str(f),
        old_string="hello",
        new_string="goodbye",
        fuzzy=True,
    )
    # No "fuzzy:" annotation in the result line because the exact
    # path won.
    assert "fuzzy:" not in result
    assert f.read_text(encoding="utf-8") == "goodbye world\n"
