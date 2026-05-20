"""Parse and apply unified-diff format patches.

Pure parsing module — no I/O. Returns dataclass instances that
``patch_apply`` feeds back to ``apply_patch_to_text``.

Supports standard ``diff -u`` output:

    --- a/foo.py
    +++ b/foo.py
    @@ -10,3 +10,3 @@
     def foo():
    -    return 1
    +    return 2
     def bar():

Multi-file patches: include multiple --- / +++ pairs in one string.
Multi-hunk patches: include multiple @@ blocks within one file.
"""

from __future__ import annotations

import dataclasses
import re

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Hunk:
    """One hunk of a unified diff."""

    old_start: int  # 1-indexed line in the old file
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]]
    """List of (operation, content) where operation is ' ', '+', or '-'."""


@dataclasses.dataclass
class FilePatch:
    """All hunks for a single file."""

    old_path: str
    new_path: str
    hunks: list[Hunk]


@dataclasses.dataclass
class Patch:
    """Parsed unified diff with one or more files."""

    files: list[FilePatch]


class PatchParseError(ValueError):
    """Raised when a patch can't be parsed OR when context doesn't
    match the file content during apply."""


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------


_FILE_HEADER_RE = re.compile(r"^--- (?:a/)?(.+?)\s*(?:\t.*)?$")
_FILE_HEADER2_RE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*(?:\t.*)?$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_patch(text: str) -> Patch:
    """Parse a unified-diff string into a ``Patch``.

    Tolerates leading garbage (e.g. commit messages, signed-off-by
    lines) before the first ``---`` line — useful when an LLM
    surrounds the patch with prose.
    """
    lines = text.splitlines()
    i = 0
    files: list[FilePatch] = []

    while i < len(lines):
        # Skip leading non-diff content until we find a "--- " header.
        while i < len(lines) and not lines[i].startswith("--- "):
            i += 1
        if i >= len(lines):
            break

        m1 = _FILE_HEADER_RE.match(lines[i])
        if not m1:
            raise PatchParseError(f"Bad --- header at line {i + 1}: {lines[i]!r}")
        old_path = m1.group(1)
        i += 1

        if i >= len(lines) or not lines[i].startswith("+++ "):
            raise PatchParseError(f"Expected +++ header after --- at line {i + 1}")
        m2 = _FILE_HEADER2_RE.match(lines[i])
        if not m2:
            raise PatchParseError(f"Bad +++ header at line {i + 1}: {lines[i]!r}")
        new_path = m2.group(1)
        i += 1

        # Parse hunks until next --- or EOF.
        hunks: list[Hunk] = []
        while i < len(lines) and lines[i].startswith("@@"):
            hunk, consumed = _parse_hunk(lines, i)
            hunks.append(hunk)
            i += consumed

        if not hunks:
            raise PatchParseError(f"No hunks for {old_path}")

        files.append(FilePatch(old_path=old_path, new_path=new_path, hunks=hunks))

    return Patch(files=files)


def _parse_hunk(lines: list[str], start: int) -> tuple[Hunk, int]:
    """Parse one hunk starting at ``lines[start]`` (the ``@@`` line).

    Returns ``(hunk, lines_consumed)``.
    """
    header = lines[start]
    m = _HUNK_HEADER_RE.match(header)
    if not m:
        raise PatchParseError(f"Bad hunk header: {header!r}")
    old_start = int(m.group(1))
    old_count = int(m.group(2)) if m.group(2) else 1
    new_start = int(m.group(3))
    new_count = int(m.group(4)) if m.group(4) else 1

    body: list[tuple[str, str]] = []
    i = start + 1
    old_seen = 0
    new_seen = 0
    while i < len(lines) and (old_seen < old_count or new_seen < new_count):
        line = lines[i]
        if line.startswith("@@") or line.startswith("--- "):
            break
        if line.startswith(" "):
            body.append((" ", line[1:]))
            old_seen += 1
            new_seen += 1
        elif line.startswith("-"):
            body.append(("-", line[1:]))
            old_seen += 1
        elif line.startswith("+"):
            body.append(("+", line[1:]))
            new_seen += 1
        elif line == "":
            body.append((" ", ""))
            old_seen += 1
            new_seen += 1
        elif line.startswith("\\ No newline at end of file"):
            # Marker — no body change; preserves the no-final-newline
            # property of the surrounding line during apply.
            pass
        else:
            raise PatchParseError(f"Bad hunk line: {line!r}")
        i += 1

    return (
        Hunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            lines=body,
        ),
        i - start,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_patch_to_text(original: str, file_patch: FilePatch) -> str:
    """Apply all hunks of ``file_patch`` to ``original``. Pure function.

    Returns the new file content. Raises ``PatchParseError`` if any
    hunk's context lines don't match the file.

    Hunks are applied in REVERSE order of ``old_start`` so earlier
    hunks' absolute line numbers stay valid as later hunks (which
    have larger ``old_start``) shift lines.
    """
    # splitlines(keepends=True) preserves the line terminator on
    # each line, which we want so the result can be joined back
    # without losing newlines or accidentally adding them.
    lines = original.splitlines(keepends=True)
    sorted_hunks = sorted(file_patch.hunks, key=lambda h: h.old_start, reverse=True)

    for hunk in sorted_hunks:
        lines = _apply_hunk_to_lines(lines, hunk)

    # Preserve original's trailing-newline property.
    out = "".join(lines)
    if not original.endswith("\n") and out.endswith("\n"):
        # If we added a trailing newline that the original lacked,
        # strip it. The hunk's "+ line" lines get newlines appended
        # below; if the last applied line was an addition at EOF, we
        # may have over-added one terminator.
        out = out.rstrip("\n") + ""
        if original.endswith("\n"):
            out += "\n"
    return out


def _apply_hunk_to_lines(lines: list[str], hunk: Hunk) -> list[str]:
    """Apply one hunk in place; return the new list.

    Verifies the hunk's context + deletion lines match the slice of
    ``lines`` starting at ``hunk.old_start - 1`` (1-indexed -> 0-indexed).
    """
    start_idx = hunk.old_start - 1

    # The "old" slice the hunk describes is every line marked ' ' or '-'.
    expected_old: list[str] = []
    for op, content in hunk.lines:
        if op in (" ", "-"):
            expected_old.append(content)

    actual_old = [
        _strip_line_terminator(line) for line in lines[start_idx : start_idx + len(expected_old)]
    ]
    expected_stripped = [_strip_line_terminator(s) for s in expected_old]
    if actual_old != expected_stripped:
        raise PatchParseError(
            f"Hunk @ line {hunk.old_start} does not match file content.\n"
            f"Expected:\n{expected_stripped!r}\n"
            f"Actual:\n{actual_old!r}"
        )

    # The "new" slice is every line marked ' ' or '+'.
    new_slice: list[str] = []
    for op, content in hunk.lines:
        if op in (" ", "+"):
            new_slice.append(content + "\n")

    return lines[:start_idx] + new_slice + lines[start_idx + len(expected_old) :]


def _strip_line_terminator(s: str) -> str:
    """Return ``s`` with a trailing ``\\n`` or ``\\r\\n`` removed."""
    if s.endswith("\r\n"):
        return s[:-2]
    if s.endswith("\n"):
        return s[:-1]
    return s
