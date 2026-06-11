"""Word-boundary allowlist + explicit denylist for the Bash tool.

athena v1's allowlist used Python's ``in`` operator against a list
of strings, which collapses two distinct categories of bug:

- Prefix shadowing: an allowlist containing ``git`` matches
  ``gitlab-cli`` and ``gitleaks``.
- Path-component leak: an allowlist containing ``rm`` matches
  ``rm -rf /home/user/.git/objects`` *and* ``mv x.rm dest``.

This module replaces that with two cooperating layers:

1. A denylist of regex patterns that always fires regardless of
   the allowlist. The default covers the high-damage verbs and
   pipeline shapes (``rm -rf /``, ``curl | sh``, ``mkfs.*``, ...).
2. A word-boundary allowlist: each entry compiles to
   ``^<escaped-entry>\\b`` and must match the *binary token*
   (the first non-environment-assignment token) of **every command
   segment**. A command is split into segments on unquoted shell
   control operators (``&&``, ``||``, ``;``, ``|``, ``&``) so
   ``git status && rm -rf ~/x`` needs BOTH ``git`` and ``rm`` on
   the allowlist — matching only the first binary let any chained
   command ride an allowlisted prefix past the confirmation prompt.
   Commands using substitution (``$(...)``, backticks, ``<(...)``)
   or unquoted newlines never auto-approve: their effective binaries
   can't be determined statically, so they fall through to the
   interactive confirmation prompt.

The denylist is the security floor — call
:meth:`ShellPolicy.evaluate_denylist_only` to enforce just that
without locking the user into an allowlist. The full strict mode
(``evaluate``) is what Phase 17's CI / safety tests rely on.

Known residual: an allowlisted binary can still redirect onto an
arbitrary file (``git log > ~/.bashrc``). Redirection targets are
arguments of the trusted binary, not separate commands, and policing
them needs path resolution this layer doesn't do.
"""

from __future__ import annotations

import dataclasses
import re
import shlex
from collections.abc import Iterable


@dataclasses.dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    matched_rule: str | None = None


# Characters shlex groups into punctuation-run tokens. Including the
# redirect chars means ``2>&1`` tokenizes as ('2', '>&', '1') instead
# of leaking a lone '&' that would falsely split the segment.
_PUNCT_CHARS = ";|&<>"
# Punctuation runs that are redirections, not command separators —
# they stay inside their segment.
_REDIRECT_TOKENS = frozenset({">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>", ">|"})
# Raw-string markers for command/process substitution. Both execute
# inside double quotes, so a raw scan is the safe over-approximation
# (a single-quoted literal ``$(`` merely falls back to the prompt).
_SUBSTITUTION_MARKERS = ("$(", "`", "<(", ">(")


def _is_separator(token: str) -> bool:
    """True when *token* is an unquoted shell control operator that
    starts a new command segment (``;``, ``&&``, ``||``, ``|``, ``&``,
    and mixed runs like ``;>``) — but not a pure redirection."""
    if not token or not set(token) <= set(_PUNCT_CHARS):
        return False
    if token in _REDIRECT_TOKENS:
        return False
    return any(c in ";|&" for c in token)


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """Split a token stream into command segments at control
    operators. Empty segments (trailing ``;``, ``cmd &``) drop out."""
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if _is_separator(tok):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _skip_env_assignments(segment: list[str]) -> int:
    """Index of the binary token after leading ``FOO=bar`` prefixes."""
    idx = 0
    while (
        idx < len(segment)
        and "=" in segment[idx]
        and not segment[idx].startswith("-")
        and not segment[idx].startswith("=")
        and segment[idx].split("=", 1)[0].isidentifier()
    ):
        idx += 1
    return idx


# -- building blocks for the rm recursive+force patterns -------------
# Detect an ``rm`` that has BOTH a recursive and a force flag, in any
# order or clustering (-rf, -fr, -r -f, -Rf, --recursive --force, ...),
# targeting a protected path. Earlier this used three stacked
# ``(?:-\S+\s+)*`` groups whose tokens could be claimed by either a
# flag matcher or a filler group — when the trailing target failed to
# match a benign relative/home path, the engine re-partitioned the
# flag run and backtracked cubically (a ReDoS: a ~2 KB ``rm -r -f …``
# stalled the synchronous policy check for seconds).
#
# The rewrite uses two ZERO-WIDTH lookaheads to assert the flags are
# present, plus a SINGLE consuming quantifier to reach the target — no
# nested ambiguity, so matching is linear. The flag matchers are
# single-star clusters (``-[A-Za-z]*[rR]``, not ``-[A-Za-z]*r[A-Za-z]*``)
# and the lookahead scan windows are bounded (``{0,160}``) so neither
# can blow up on a long argument; flags always sit right after ``rm``,
# so the bound never costs real coverage.
_SEG = r"[^;|&\n]"  # one command segment's worth of chars
_RECURSIVE = r"(?:-[A-Za-z]*[rR]|--recursive\b)"
_FORCE = r"(?:-[A-Za-z]*f|--force\b)"
# Targets the floor refuses any recursive+force rm: system roots
# (with carve-outs for paths UNDER /home/, /tmp/, /var/tmp/ — note
# ``\S`` so bare ``/home/`` itself stays blocked) and the home
# directory itself (~, ~/, $HOME, ${HOME} — but not ~/subdir).
_RM_TARGET = (
    r"[\"']?(?:/(?!home/\S|tmp/\S|var/tmp/\S)"
    r"|(?:~|\$HOME|\$\{HOME\})/?(?=[\s\"';&|)]|$))"
)

# Each pattern is a regex applied to the raw command string before
# tokenisation. Patterns are ordered roughly by severity. Patterns
# are case-INsensitive at compile time below so Windows / cmd-style
# verbs (``DEL``, ``Format``) match the same as their lowercase
# equivalents -- operators copy-pasting from shell history shouldn't
# evade the floor by accidental capitalization.
DEFAULT_DENYLIST: tuple[str, ...] = (
    # ---- POSIX / Linux ----
    # rm with recursive AND force flags (any order/clustering) hitting a
    # protected target. Lookaheads assert the flags; the lazy tail finds
    # the target at an argument boundary (preceded by whitespace) so a
    # carved-out path like /home/user/x can't match via an interior '/'.
    rf"\brm\s+(?={_SEG}{{0,160}}?{_RECURSIVE})(?={_SEG}{{0,160}}?{_FORCE}){_SEG}*?\s{_RM_TARGET}",
    r"\brm\b[^;|&\n]*--no-preserve-root",  # explicit root-wipe intent
    rf"\bsudo\s+rm\s+{_SEG}{{0,160}}?{_RECURSIVE}",  # any recursive rm under sudo
    r"\bmkfs\.",  # filesystem creation (any fs flavour)
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;:",  # fork bomb
    r"\bchmod\s+.*\b777\b\s+/",  # chmod 777 on system paths
    # ---- block-device destruction (covers macOS + Linux) ----
    # macOS uses /dev/disk* and /dev/rdisk*; FreeBSD adds /dev/ada*.
    # Without these, ``dd if=/dev/zero of=/dev/disk0`` evaded the
    # floor on Apple Silicon and Intel Macs alike.
    r"\bdd\s+.*\bof=/dev/(sd|nvme|hd|disk|rdisk|ada|vd)",
    r">\s*/dev/(sda|nvme|hda|disk|rdisk)",  # redirect to block device
    # ---- pipe-to-shell pattern ----
    r"\bcurl\b.*\|\s*(sudo\s+)?(sh|bash|zsh|dash|ksh|fish)\b",
    r"\bwget\b.*\|\s*(sudo\s+)?(sh|bash|zsh|dash|ksh|fish)\b",
    # Piping a download into a bare script interpreter executes the
    # stream as code; ``| python -m json.tool`` and friends (stdin
    # as DATA) stay allowed -- only the bare/`-` stdin-as-program
    # forms are blocked.
    r"\b(?:curl|wget)\b.*\|\s*(?:sudo\s+)?(?:python[0-9.]*|perl|ruby|node)\s*(?:-\s*)?(?:$|[;&|<])",
    # PowerShell download-and-execute (iwr | iex).
    r"\b(?:iwr|irm|invoke-webrequest|invoke-restmethod)\b.*\|\s*(?:iex\b|invoke-expression\b)",
    # ---- Windows / cmd / PowerShell ----
    # ATHENA.md treats Windows as first-class so the denylist must
    # cover Windows-native destruction verbs. ``del /s /q`` is the
    # Windows analogue of ``rm -rf``; ``format`` and ``cipher /w``
    # are direct disk-wipe vectors that no agent should ever issue.
    # ``rd /s /q`` (alias ``rmdir``) on a system root is equivalent
    # to del. Patterns are written so a lone ``del foo.txt`` is
    # untouched -- only the recursive/quiet+root combinations fire.
    r"\b(del|erase)\s+(/[sq]\s+)+",  # del /s /q or /q /s (any order)
    r"\b(rd|rmdir)\s+/s\b",  # recursive rmdir
    r"\bformat\b\s+[a-z]:",  # format C: / format D:
    r"\bcipher\s+/w:",  # cipher /w: secure-wipes free space
    r"\bdiskpart\b",  # interactive disk partition tool
    # PowerShell remove-item with -recurse + -force on system paths
    r"\bremove-item\b.*\-recurse\b.*\-force\b",
)


class ShellPolicy:
    """Evaluate shell commands against a denylist + allowlist."""

    def __init__(
        self,
        allowlist: Iterable[str] = (),
        denylist: Iterable[str] = DEFAULT_DENYLIST,
    ) -> None:
        allowlist = tuple(allowlist)
        self._allow_raw: tuple[str, ...] = allowlist
        self._allow_patterns: tuple[re.Pattern[str], ...] = tuple(
            re.compile(rf"^{re.escape(entry)}\b") for entry in allowlist
        )
        # Case-INsensitive on the denylist so Windows-style
        # capitalization (``DEL /S /Q``, ``Format C:``) doesn't
        # evade the floor. POSIX patterns are unaffected -- shell
        # binaries are conventionally lowercase, so a ``RM -RF /``
        # is non-functional but still indicative of intent and
        # worth blocking. Allowlist patterns stay case-sensitive
        # (the operator's allowlist is explicit configuration; we
        # don't second-guess the case they typed).
        self._deny_patterns: tuple[re.Pattern[str], ...] = tuple(
            re.compile(p, re.IGNORECASE) for p in denylist
        )

    # ---- public API ------------------------------------------------

    def evaluate_denylist_only(self, command: str) -> PolicyDecision:
        """Apply only the denylist + parseability checks.

        Used as the always-on safety floor: even when the agent has
        no explicit allowlist configured we still block ``rm -rf /``
        and friends. Allowlist enforcement is a separate opt-in.
        """
        cmd = command.strip()
        if not cmd:
            return PolicyDecision(False, "empty command", None)
        deny = self._scan_denylist(cmd)
        if deny is not None:
            return deny
        # Also verify shlex-parseable so unbalanced quotes don't slip
        # through to the shell; the agent has zero hope of debugging
        # the resulting subprocess error.
        try:
            shlex.split(cmd, posix=True)
        except ValueError as e:
            return PolicyDecision(False, f"unparseable: {e}", None)
        return PolicyDecision(True, "denylist clean", None)

    def evaluate(self, command: str) -> PolicyDecision:
        """Strict evaluation: deny on denylist match, deny when ANY
        command segment's binary is not in the allowlist.

        The command is split on unquoted control operators (``&&``,
        ``||``, ``;``, ``|``, ``&``) and every segment must pass —
        otherwise ``git status && rm -rf ~/x`` rides the ``git``
        allowlist entry past the confirmation prompt. Commands whose
        effective binaries can't be determined statically (command /
        process substitution, unquoted newlines) are never approved
        here; they fall through to the interactive prompt.
        """
        cmd = command.strip()
        if not cmd:
            return PolicyDecision(False, "empty command", None)

        deny = self._scan_denylist(cmd)
        if deny is not None:
            return deny

        for marker in _SUBSTITUTION_MARKERS:
            if marker in cmd:
                return PolicyDecision(
                    False,
                    f"command substitution ({marker!r}) requires explicit approval",
                    None,
                )

        try:
            lex = shlex.shlex(cmd, posix=True, punctuation_chars=_PUNCT_CHARS)
            lex.whitespace_split = True
            lex.commenters = ""  # match shlex.split(); '#' is not special
            tokens = list(lex)
        except ValueError as e:
            return PolicyDecision(False, f"unparseable: {e}", None)
        if not tokens:
            return PolicyDecision(False, "no tokens after shlex", None)

        # shlex treats unquoted newlines as plain whitespace, which
        # would silently merge two commands into one segment. Quoted
        # newlines survive inside their token; any newline that did
        # NOT survive was structural — refuse to auto-approve.
        if cmd.count("\n") > sum(t.count("\n") for t in tokens):
            return PolicyDecision(False, "multi-line command requires explicit approval", None)

        segments = _split_segments(tokens)
        if not segments:
            return PolicyDecision(False, "no command found", None)

        matched: list[str] = []
        for segment in segments:
            idx = _skip_env_assignments(segment)
            if idx >= len(segment):
                return PolicyDecision(False, "no command after env assignments", None)
            binary = segment[idx]
            for pat, raw in zip(self._allow_patterns, self._allow_raw):
                if pat.match(binary):
                    if raw not in matched:
                        matched.append(raw)
                    break
            else:
                return PolicyDecision(
                    False,
                    f"binary {binary!r} not in allowlist",
                    None,
                )
        rules = ", ".join(matched)
        return PolicyDecision(True, f"allowlist match: {rules}", rules)

    # ---- internals -------------------------------------------------

    def _scan_denylist(self, cmd: str) -> PolicyDecision | None:
        for p in self._deny_patterns:
            if p.search(cmd):
                return PolicyDecision(
                    False,
                    f"denylist match: {p.pattern}",
                    p.pattern,
                )
        return None
