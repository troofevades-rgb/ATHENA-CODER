"""Bash execution tool. Subject to the agent's confirmation gate
(unless the command matches the bash_allowlist or auto_approve_tools is set).
"""
from __future__ import annotations
import os
import select
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .registry import tool
from . import file_ops  # for workspace
from ..ui import console

_MAX_OUTPUT = 64_000

# Background process registry (id -> dict with proc, started, command, output_buf)
_BG: dict[str, dict[str, Any]] = {}
_BG_LOCK = threading.Lock()
_NEXT_BG_ID = 1


def set_max_output(n: int) -> None:
    global _MAX_OUTPUT
    _MAX_OUTPUT = n


def _truncate(out: str) -> str:
    if len(out) > _MAX_OUTPUT:
        half = _MAX_OUTPUT // 2
        return out[:half] + f"\n\n... [truncated, {len(out)} total bytes] ...\n\n" + out[-half:]
    return out


@tool(
    name="Bash",
    aliases=["bash"],
    description=(
        "Executes a given bash command and returns its output. The working "
        "directory persists between commands within a turn. Output is captured "
        "(stdout+stderr merged) and truncated if very long. Default timeout "
        "120s, max 600s.\n\n"
        "Avoid `cat`/`head`/`tail`/`sed`/`awk`/`echo`; prefer the Read/Edit/"
        "Write tools instead. Quote paths with spaces. Use `description` to "
        "explain non-obvious commands. Set run_in_background=true for long-"
        "running processes (servers, watchers); poll output with bash_output."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute via /bin/bash -c"},
            "description": {"type": "string", "description": "Optional 5-10 word description for the user."},
            "timeout": {"type": "integer", "description": "Optional timeout in seconds (default 120, max 600)."},
            "run_in_background": {"type": "boolean", "description": "Run asynchronously and return a bg id."},
        },
        "required": ["command"],
    },
    requires_confirmation=True,
)
def Bash(
    command: str,
    description: str = "",
    timeout: int = 120,
    run_in_background: bool = False,
) -> str:
    timeout = max(1, min(int(timeout or 120), 600))
    if run_in_background:
        return _start_background(command)

    proc = subprocess.Popen(
        command,
        shell=True,
        executable="/bin/bash",
        cwd=str(file_ops._WORKSPACE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    buf: list[str] = []
    pending = b""
    deadline = time.time() + timeout
    timed_out = False

    console.print(f"[dim]$ {command}[/dim]")
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))
            if ready:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break  # EOF
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace") + "\n"
                    console.out(text, end="", highlight=False)
                    buf.append(text)
            elif proc.poll() is not None:
                break  # process exited and pipe drained
    finally:
        if timed_out and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        rc = proc.wait()
        if pending:
            tail = pending.decode("utf-8", errors="replace")
            console.out(tail, end="", highlight=False)
            buf.append(tail)

    if timed_out:
        buf.append(f"\n[ocode] command timed out after {timeout}s\n")
    out = "".join(buf)
    return f"exit={rc}\n{_truncate(out)}"


def _start_background(command: str) -> str:
    global _NEXT_BG_ID
    proc = subprocess.Popen(
        command,
        shell=True,
        executable="/bin/bash",
        cwd=str(file_ops._WORKSPACE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
    )
    with _BG_LOCK:
        bg_id = f"bg{_NEXT_BG_ID}"
        _NEXT_BG_ID += 1
        entry: dict[str, Any] = {
            "proc": proc,
            "started": time.time(),
            "command": command,
            "buf": [],
        }
        _BG[bg_id] = entry

    # Background reader to drain stdout into the ring buffer
    def _drain() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                with _BG_LOCK:
                    entry["buf"].append(line)
                    if len(entry["buf"]) > 5000:
                        entry["buf"] = entry["buf"][-5000:]
        except Exception:
            pass

    threading.Thread(target=_drain, name=f"ocode-bg-{bg_id}", daemon=True).start()
    return f"started {bg_id}: {command!r} (use bash_output {bg_id})"


@tool(
    name="bash_output",
    description=(
        "Read pending output from a background Bash process started with "
        "run_in_background=true. Returns whatever output has accumulated since "
        "the last call, plus an exit status if the process has finished."
    ),
    parameters={
        "type": "object",
        "properties": {
            "bash_id": {"type": "string"},
        },
        "required": ["bash_id"],
    },
)
def bash_output(bash_id: str) -> str:
    with _BG_LOCK:
        entry = _BG.get(bash_id)
        if not entry:
            return f"ERROR: no background process named {bash_id!r}"
        # Drain the buffer, leave the entry so further polls work
        chunk = "".join(entry["buf"])
        entry["buf"] = []
        proc = entry["proc"]
    rc = proc.poll()
    if rc is None:
        status = f"running, started {time.time() - entry['started']:.1f}s ago"
    else:
        status = f"exit={rc}"
        # Reap the entry once the process has exited and we've drained the
        # final chunk. Prevents _BG from growing unbounded across a session.
        with _BG_LOCK:
            if bash_id in _BG and not _BG[bash_id]["buf"]:
                del _BG[bash_id]
    return f"{status}\n{_truncate(chunk)}"


@tool(
    name="kill_bash",
    description="Kill a background Bash process by id. Returns its exit code.",
    parameters={
        "type": "object",
        "properties": {
            "bash_id": {"type": "string"},
        },
        "required": ["bash_id"],
    },
    requires_confirmation=False,
)
def kill_bash(bash_id: str) -> str:
    with _BG_LOCK:
        entry = _BG.get(bash_id)
    if not entry:
        return f"ERROR: no background process named {bash_id!r}"
    proc = entry["proc"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    rc = proc.poll()
    with _BG_LOCK:
        _BG.pop(bash_id, None)
    return f"{bash_id}: exit={rc}"
