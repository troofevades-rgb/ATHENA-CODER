# Verified-execution loop

After the agent (or a CLI user) writes a file, athena can verify
that the change didn't break anything before handing control
back. This is the **verified-execution loop** — a single
post-write cycle that fuses three earlier pieces:

| Phase  | Provides                                          |
|--------|---------------------------------------------------|
| T3-03  | Conversation + file-state checkpoints, rollback   |
| T5-03  | LSP diagnostics (`diagnose_paths`)                |
| T5-02  | Bubblewrap sandbox for running commands safely    |

The loop wraps those into one orchestrator that captures a
pre-write checkpoint, diagnoses the file, optionally runs a
project-level command, and on failure either offers the user a
`/rollback-to <id>` or auto-reverts.

## What you get

After a successful write the tool result stays clean — the loop
doesn't add noise on the green path:

```text
overwrote athena/foo.py (412 bytes)
```

After a failed write the tool result appends the verification
report. The model sees the introduced errors and the rollback
command inline:

```text
overwrote athena/foo.py (412 bytes)
✗ athena/foo.py: write introduced 1 error(s):
  - line 12:4 [reportUndefinedVariable] "x" is undefined
  Roll back with: /rollback-to cp-9f2a3b1c4d56
```

If `verify_auto_rollback = true`, the loop reverts on its own
and the trailing line becomes `(auto-rolled back to pre-write
checkpoint)` instead.

## Configuration

In `~/.athena/config.toml`:

```toml
# How much to verify after each write.
#   "off"          — disable (legacy behaviour)
#   "diagnose"     — LSP only; fast, no subprocess  (default)
#   "diagnose+run" — LSP plus verify_command under the sandbox
verify_on_write = "diagnose"

# Command to run when verify_on_write = "diagnose+run".
# Runs in the workspace dir, under the bwrap sandbox when
# sandbox_enabled = true.
verify_command = "pytest -q tests/"

# Revert automatically on failure instead of offering /rollback-to.
verify_auto_rollback = false

# When true and the agent supplied a retry callback, the loop
# asks for a corrected write on failure — capped at
# verify_max_retries. (The hook is opt-in: file_ops doesn't
# wire one in by default, so most users won't see this fire.)
verify_auto_retry = false
verify_max_retries = 2
verify_run_timeout_s = 120.0
```

## The four outcomes

| Outcome              | When                                                      |
|----------------------|-----------------------------------------------------------|
| `passed`             | Diagnose clean + run (if enabled) exit 0                  |
| `failed_diagnostics` | LSP reported one or more error-severity diagnostics       |
| `failed_run`         | `verify_command` exited non-zero                          |
| `skipped`            | `verify_on_write = "off"`                                  |

Warnings, info, and hints **don't** trigger `failed_diagnostics`
— only error severity does. The bar is "did this write actively
break something?", not "is this perfect?".

## Per-leg degradation

Each leg is independent. A leg failing degrades the outcome
rather than blocking the write:

| Leg                 | Failure mode → outcome                                        |
|---------------------|---------------------------------------------------------------|
| Checkpoint capture  | No rollback hint in the report; verify still runs             |
| Checkpoint manager absent | Same — no checkpoint_id; rollback offer omitted         |
| Diagnose raises     | Treated as "no errors" → outcome falls through to `passed`    |
| Diagnose weird shape | Non-`is_error` entries silently ignored                      |
| Runner raises       | `failed_run` with `exit_code=-1` and exception text in stderr |
| Runner timeout      | `failed_run` with `exit_code=124` and "timed out" in stderr   |

The verifier itself is wrapped in a defensive try/except at the
`file_ops` integration site — a bug in the loop becomes a
debug-log entry, never a blocked write.

## CLI: one-shot verify

```bash
# Verify a file using config defaults (diagnose only by default).
athena verify athena/foo.py

# Force diagnose+run for this invocation.
athena verify athena/foo.py --command "pytest -q tests/foo"

# Bypass the sandbox for this run (e.g. on macOS / no bwrap host).
athena verify athena/foo.py --command "ruff check" --no-sandbox

# Bound the run.
athena verify athena/foo.py --command "pytest -q" --timeout 60
```

Exit codes:

| Code | Meaning                                |
|------|----------------------------------------|
| 0    | `passed` or `skipped`                  |
| 1    | `failed_diagnostics`                   |
| 2    | `failed_run`                            |
| 3    | Path not found                         |

The CLI runs without an active checkpoint manager, so the
report carries no `/rollback-to` hint — it's a pure
verification surface, useful for CI and for testing a
candidate `verify_command` before turning it on agent-side.

## Programmatic surface

```python
from athena.verify import VerifiedExecution

v = VerifiedExecution(cfg=load_config(), workspace=Path.cwd())
outcome = v.verify_write("athena/foo.py")
print(outcome.report())

# Inspect:
outcome.passed                # True on passed / skipped
outcome.failed                # True on either failure outcome
outcome.checkpoint_id         # None when no checkpoint captured
outcome.introduced_errors     # List of "line:col [code] msg" strings
outcome.run_exit_code         # int | None
outcome.run_stderr_tail       # str | None
outcome.rolled_back           # True if verify_auto_rollback fired
```

For tests, every leg of the loop can be dependency-injected:

```python
v = VerifiedExecution(
    cfg=fake_cfg,
    diagnose=lambda paths: [FakeDiag(is_error=True, message="bad")],
    runner=lambda cmd, **kw: FakeResult(exit_code=1, stderr="..."),
    checkpoint_manager=fake_mgr,
)
```

The `latest_outcome` property exposes the most recent result
(read-only) for the deferred T5-07 goal-evaluation loop to
consult without re-running verification.

## What the loop does not do

- **Doesn't run when the write fails the syntax lint.** A write
  that doesn't parse never reaches the verifier — `file_ops`
  already returns "fix the syntax and re-call Write".
- **Doesn't propagate exceptions.** A bug in the verifier
  becomes a debug-log entry; the write itself still succeeds.
- **Doesn't run in `off` mode.** The whole pipeline (including
  the checkpoint capture) short-circuits when
  `verify_on_write = "off"`.

## Related

- [LSP diagnostics](lsp.md) — the diagnose backend
- [Checkpoints](checkpoints.md) — the rollback target
- [Sandbox](../sandbox.md) — bwrap wrap for `verify_command`
