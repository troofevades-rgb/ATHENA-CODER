# Training-data labeling TUI

`athena train review` defaults to a keyboard-driven `textual` UI
that labels trajectories ~5× faster than the original
one-at-a-time prompt. The labels feed straight into the closed
loop: `athena train build-dataset` reads them, `athena train run`
fine-tunes against the resulting JSONL, and `ollama create` flips
the running model over.

This is **not** a new label format. The TUI writes the same
`<profile_dir>/labels/<session_id>.json` sidecar that
`default_prompt` writes today — flat dict
`"<turn_start>-<turn_end>" → Label`. Both paths share the
`save_label` writer; switching between them is byte-safe.

## Install

The TUI ships under the `[train]` optional extra (textual is a
small terminal-only dep):

```bash
pipx install --force "athena-coder[train]"
```

Headless installs without the extra keep working — `athena train
review` automatically falls back to the existing one-at-a-time
prompt with a one-line notice.

## Quick start

```bash
athena train review                  # default: TUI
athena train review --no-tui         # original one-at-a-time prompt
athena train review --keymap basic   # no Ctrl combos
athena train build-dataset           # the labels you just set
athena train status                  # confirm the loop end-to-end
```

## Hotkeys (default keymap)

| Key | Action |
|-----|--------|
| `y` / `j` | label good and advance |
| `n` / `k` | label bad and advance |
| `p` | label preference_pair |
| `s` | skip (advance without persisting) |
| `space` | toggle multi-select |
| `Y` | batch-apply **good** to selection |
| `N` | batch-apply **bad** to selection |
| `enter` | accept the current suggestion |
| `h` / `←` | step back |
| `l` / `→` | step forward |
| `ctrl+z` | undo last label |
| `q` | quit |

### Alternative keymaps

- `--keymap vim` — alias for default (stable name)
- `--keymap basic` — `g`/`b` for good/bad, `G`/`B` for batch,
  `u` for undo, `,`/`.` for nav. No Ctrl combos for terminals
  that swallow them.

## Batch ops

Walking 10 trajectories that all look good:

1. Read trajectory 1 → `space` (select)
2. `l` to advance → `space` again …
3. After selecting the last one, `Y`. Every selected trajectory
   gets labelled `good`; cursor advances past the highest-selected
   index; selection clears.

Each batch item lands as its own undo entry so a single `ctrl+z`
reverses just the most recent write, not the whole batch.

## Undo

`ctrl+z` (or `u` in `--keymap basic`) pops the last label,
restoring the prior on-disk state — including the unreviewed
state if the trajectory was unlabelled before the write. Cursor
lands back on the reverted trajectory so you can relabel
immediately.

Undo stack is bounded at 50 entries.

## Auto-suggestion

Above the hotkey footer the TUI surfaces a recommendation when
one's available:

```
auto-suggestion: good  [source: classifier]
```

`enter` accepts it. `y` / `n` / `p` / `s` override it.

Two sources:

- **classifier** (always on) — `transform.classifier.auto_classify`
  inspects the trajectory's tool calls, error propagation,
  follow-up user messages, and synthetic `[/steer]` markers to
  emit a heuristic label.
- **metrics** (opt-in via T3-06) — if a skill's prior labelled
  invocations are ≥ 95% one-sided across ≥ 10 decisive labels,
  the metrics override beats the classifier. The TUI surfaces the
  source so you know which signal recommended what.

When T3-06 metrics aren't present, the TUI silently uses
classifier-only suggestions. No configuration step, no warning —
the optional dep is genuinely optional.

## What writes where

Every keypress that produces a non-skip label calls the existing
`athena.transform.review.save_label` and lands a sidecar update:

```
<profile_dir>/labels/<session_id>.json
```

```json
{
  "0-3": "good",
  "4-8": "bad",
  "9-12": "preference_pair"
}
```

The format predates T3-05R; the TUI exists only because it's
faster to drive than the prompt, not because the label shape
needed to change.

## Closed-loop flow

```
athena train review              ← TUI labels trajectories
athena train build-dataset       ← rolls labels into SFT/DPO JSONL
athena train run                 ← LoRA + GGUF + ollama create
athena train status              ← confirm a fresh model is registered
```

The `--no-tui` fallback is fully equivalent — same labels, same
sidecar, same downstream pipeline. The TUI is a throughput
upgrade, not a behaviour change.

## Limitations / reserved

- `/` filter widget is reserved — surfaces a hint today.
- `?` help overlay is reserved — relies on the footer for now.
- Live label-rate display ("ETA ~3m") is included; per-skill
  filter and per-status filter (labelled / unlabelled) come with
  the `/` widget in a follow-up.
- 5× speedup target is documented in the spec; measured in
  `docs/proof/labeling-ui-speedup.md` once the operator runs the
  manual smoke against a real 100-trajectory pool.
