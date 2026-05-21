# Computer use (desktop control)

> **The riskiest tool surface athena ships, and the only one with no
> isolation underneath.** Read the safety section *first*. The
> permission model **IS** the design.

Athena can observe your screen and (with explicit consent) drive
mouse / keyboard input. Two modes:

| Mode | What it does | Risk |
|---|---|---|
| **observe-only** (default) | Screenshots + a vision model describes what's there. Athena answers questions about your screen. NO input ever. | Low. Nothing happens to your machine. |
| **active control** | After the permission gate approves an action, athena clicks / types / presses keys / scrolls. | This is the inverse of the sandbox. There is no isolation. |

Off by default. Opt in per machine via `computer_use_enabled = true`.

## ⚠ Read this first

Computer use is the **inverse of T5-02's sandbox**. The sandbox
isolates code *away from* your machine; computer use points the
agent *at* your machine on purpose.

**There is no isolation here.** A click is a real click; a typed
character lands in whatever window has focus; a `Delete` press
is a `Delete` press. The permission model is the **only** safety
boundary.

That's not a flaw — it's the deal you accept when you ask an
agent to operate your computer. This page documents what the
boundary actually catches and what it doesn't.

## The permission model (T6-04R)

Every input action routes through athena's existing approval
infrastructure:

  - `athena.safety.approval_guard.request_approval_sync` — the
    ContextVar-scoped approval gate that already protects
    file-write operations, MCP tools, and verified-execution
    rollback. There is **no new consent machinery** for
    computer use; it's the same prompt surface every other
    sensitive tool uses.

  - `athena.safety.approval_callback` — the prompt callback
    bound to the current session. Defaults to interactive
    `ui.confirm`; switched to the ACP `permission_request` when
    you're driving athena from an IDE; switched to `AUTO_DENY`
    inside any fork (background review / curator / migration /
    system).

The gate classifies every proposed action into one of three
tiers and dispatches:

| Tier | Examples | Cache policy |
|---|---|---|
| **observe** | screenshot, describe, locate | No approval — never prompts. |
| **input** | click on a labeled button, type into a search box, scroll | Prompts once via the active approval callback; the grant caches under `computer_input` for the rest of the turn / scope. |
| **destructive** | click "Delete" / "Send" / "Pay" / "Confirm" / "Discard"; press Alt+F4 / Cmd+W / Delete / F5; click an *unreadable* button (we don't know what it does); type `sudo` / `rm -rf` | Prompts via a **per-action resource_id**. The cache never hits. Every destructive action freshly confirms. |

The classifier is **conservative by default** — an unknown or
unreadable target is treated as destructive. Better to ask an
extra time than to auto-execute something we can't describe.

What the gate decides, in order:

1. **observe-tier** → allowed; no prompt.
2. **Panic engaged** → refused; **no prompt**. The kill switch
   shorts every input/destructive check until the operator
   disengages.
3. **Denylisted app** → refused; **no prompt**.
4. **App not in the allowlist** → refused; **no prompt**.
5. **`observe_only` mode (or unknown mode)** → every input
   refused; **no prompt**.
6. **`/goal` autonomous loop active** + `computer_deny_during_goal_loop`
   (default `true`) → refused; **no prompt**. See "Goal-loop
   guarantee" below.
7. **Background context** (a fork / curator / migration / system
   origin) → `ApprovalDeniedInBackground` raised inside
   `request_approval_sync`; gate returns False **without
   invoking the approval callback**. The fork's `AUTO_DENY`
   default never even fires.
8. **destructive tier** → approval prompt fires with a per-
   action resource_id (SHA-256 short hash of
   `action.describe()`). Different destructive actions get
   distinct resource_ids — a "Delete file" grant **cannot**
   satisfy a "Submit form" check.
9. **input tier** → approval prompt fires with the stable
   `computer_input` resource_id. Caches per turn so the user
   isn't re-prompted on every keystroke within an approved
   task.

## The background-deny guarantee

**Autonomous runs cannot drive input.** A fork executing tools
(background review, curator, migration, anything inside a
non-FOREGROUND write_origin) hits `ApprovalDeniedInBackground`
the moment it touches an input or destructive action. Observe
tier still works — looking at the screen has no side effects.

This composes with athena's existing fork machinery (T3-04 +
T17.2). Every fork enters with `_approval_grants` reset by
`scope_fresh_approvals()` and an `AUTO_DENY` callback installed
at thread entry; even before either of those layers fires, the
background-origin check refuses computer-use prompts at the
gate.

The escape hatch (`auto_approve_in_background=True` per
resource) is intentionally NOT used for computer use. **Do not
flip it on for computer-use resource_ids.** A computer-use
action that auto-approves inside a background fork is the
worst possible default — silent desktop control by a fork the
operator isn't watching.

## The goal-loop guarantee

The `/goal` continuation loop (T5-07) injects synthetic turns
into the main agent context to drive autonomous work toward a
declared objective. **That loop runs in FOREGROUND origin**
(it re-uses the agent's session), so the background-deny check
alone wouldn't catch it.

The gate adds an extra check: when an active goal-state file
indicates the loop is driving and `cfg.computer_deny_during_goal_loop`
is True (default), input and destructive actions are refused
**without** burning a prompt. The autonomous loop never gets
to drive the desktop unless the operator deliberately disables
this guard.

To enable computer use under `/goal`: set
`computer_deny_during_goal_loop = false`. You should not do
this. Document the reason if you do.

## The kill switch

Two surfaces, complementary:

  - **`computer_use_panic()`** (T6-04R): engages a process-
    wide disable flag AND drops every cached grant via
    `clear_grants()`. The gate refuses every input/destructive
    check WITHOUT prompting until `computer_use_unpanic()` is
    called. This is the strongest brake.

  - **Hotkey + Ctrl+C** (T6-04.3 — still present): the
    pynput-backed global hotkey (default `ctrl+alt+k`) and the
    Ctrl+C SIGINT handler engage the loop-level killswitch
    that aborts the active task. The hotkey degrades silently
    when `pynput` isn't installed (Ctrl+C remains active).

Engagement is **always logged** at WARNING. The audit row
carries the reason so an operator can correlate a halt with
what athena was about to do.

## Safe defaults — what you get out of the box

| Knob | Default | Why |
|---|---|---|
| `computer_use_enabled` | `false` | Opt-in per machine. Tools refuse with `available=False` when off. |
| `computer_permission_mode` | `"observe_only"` | No input until you explicitly switch modes. |
| `computer_app_allowlist` | `[]` | Empty → no app may be controlled. Mode change alone isn't enough; you must allowlist the apps you want athena to drive. |
| `computer_app_denylist` | `["1password", "bitwarden", "lastpass", "keychain", "keepass", "banking", "wallet", "ledger live", "metamask"]` | Out-of-box guards for password managers + finance apps. The denylist always wins. |
| `computer_deny_during_goal_loop` | `true` | Autonomous /goal runs can't drive input. See "Goal-loop guarantee" above. |
| `computer_kill_hotkey` | `"ctrl+alt+k"` | Plus Ctrl+C, always. |
| `computer_max_actions_per_task` | `40` | Hard cap; the loop stops with `status=capped`. |
| `computer_max_actions_per_sec` | `2.0` | Rate-limit between actions. |
| `computer_audit_path` | `<profile_dir>/computer_audit.jsonl` | Every action — tier + target + approval decision — appends here. |

## Where prompts actually show up

The approval prompt surface is **whatever's bound to
`approval_callback.get_approval_callback()`** at the moment of
the prompt:

  - **REPL session**: the default `_interactive_approval`
    calls `ui.confirm("Run computer_input?", default=False)`
    in the terminal.
  - **IDE / ACP session**: the IDE installs a callback that
    routes the prompt over the ACP `permission_request` channel.
    Same approval state — same ContextVar grants — different
    UI.
  - **Gateway adapter** (Slack / Discord / Matrix / email / …):
    each platform installs its own callback that posts the
    prompt to the conversation and waits for a reply.
  - **Fork / background**: `AUTO_DENY` is installed at thread
    entry. With T6-04R the background-origin check refuses
    BEFORE this callback fires; `AUTO_DENY` is a belt-and-
    braces fallback for any future code path that reaches the
    prompt anyway.

You do not configure where prompts go for computer use
specifically — the existing approval-callback binding is the
single source of truth.

## Tier classification, by example

  - **observe**: `Action(type="screenshot")`. Always.
  - **input**: `Action(type="click", target_desc="Tab 2",
    app="Chrome")` — a click on a labeled element in an
    allowed app.
  - **destructive**:
    - `Action(type="click", target_desc="Delete row")` — the
      destructive-verb regex (`delete|remove|send|submit|pay|
      buy|purchase|order|confirm|overwrite|replace|discard|
      erase|format|wipe|sudo|trash|reset|restart|shutdown|
      reboot|uninstall|drop|destroy|sign out|log out|don't
      save|close without saving`) hits.
    - `Action(type="key", key="alt+f4")` — sensitive key chord.
    - `Action(type="click", target_desc=None)` — click with no
      readable target → conservative default → destructive.
    - `Action(type="type", text="sudo apt remove ...")` — the
      typed payload itself contains a destructive verb.

The regex tolerates apostrophe variants and arbitrary
spacing; case-insensitive. The keys list covers the most
common "close + discard" / "force quit" / "reset" chords on
Windows/macOS/Linux.

## Audit log

Every action (allowed AND denied) lands in
`<profile_dir>/computer_audit.jsonl`:

```json
{"ts":"2026-05-20T13:42:00.123456Z",
 "type":"click", "target_desc":"Save",
 "coords":[100, 240], "app":"VS Code",
 "tier":"input", "confirmed":true, "executed":true,
 "screenshot_sha256":"abcd…",
 "result":"ok"}
```

The screenshot **bytes** are never stored — only a SHA-256.
Provenance over volume. Same calculus as
`athena/vision/hashlog.py` and `athena/browser/capture.py`.

## What computer use is NOT

  - **Not a sandbox.** No isolation. Every action is real.
  - **Not a coverage of UI testing.** The destructive-verb
    regex catches the common worst cases; it's not exhaustive.
    An unlabeled "OK" button next to a "Delete?" dialog is
    safe under conservative classification (unlabeled click →
    destructive) but a labeled "OK" button on the same dialog
    is **input** — a click on it would be approved like any
    other input. The model + the operator are still the last
    line of defense.
  - **Not a CAPTCHA bypass / login-wall defeater.** The
    classifier doesn't try to recognise login forms vs other
    forms. A user that allowlists their bank's website + their
    1Password app is taking on the full risk of an autonomous
    agent driving both.
  - **Not always-on.** Disabled by default; opt-in per
    machine; `observe_only` mode + empty allowlist out of the
    box. You have to consciously turn it on.

## Glossary

  - **Tier** — `observe` / `input` / `destructive`. The single
    most important taxonomy in the system.
  - **Grant** — a cached approval for one `resource_id` in the
    `_approval_grants` ContextVar dict. Lives for the duration
    of the current scope; dropped by `clear_grants()` or
    `computer_use_panic()`.
  - **resource_id** — the cache key. `computer_input` for the
    stable input grant; `computer_destructive::<sha-prefix>`
    for the per-action destructive key (different actions
    get different keys, so destructive prompts are
    per-action by construction).
  - **Origin** — `FOREGROUND` / `BACKGROUND_REVIEW` /
    `CURATOR` / `MIGRATION` / `SYSTEM`. Anything non-FOREGROUND
    triggers `ApprovalDeniedInBackground` on input/destructive.

## Where this lives in the tree

  - `athena/computer/permission.py` — classifier + the
    `PermissionGate` that consults `request_approval_sync`
    via the per-tier resource_id.
  - `athena/computer/contract.py` — `Action` / `Screenshot` /
    `Tier` / `DesktopBackend` Protocol.
  - `athena/computer/backends/*` — per-platform backends (the
    OS specifics; isolated here so the rest of the module
    never imports a backend directly).
  - `athena/computer/tools.py` — the `@tool`-registered
    surface (`computer_screenshot`, `computer_observe`,
    `computer_click`, `computer_type`, `computer_key`,
    `computer_scroll`).
  - `athena/computer/loop.py` — the `computer_do` orchestrator
    (vision proposer + gate + audit + kill-switch poll).
  - `athena/computer/audit.py` — append-only JSONL audit log
    with screenshot SHA-256.
  - `athena/safety/approval_guard.py` — the approval gate
    primitive (sync + async siblings; `clear_grants` for the
    panic path).
  - `athena/computer/RECON.md` — the design recon doc for
    T6-04R, including the gap analysis for the goal-loop case.

## See also

  - `docs/reference/safety.md` — the project-wide safety model.
  - `docs/reference/vision-analyze.md` — the multimodal model
    that powers `computer_observe`.
