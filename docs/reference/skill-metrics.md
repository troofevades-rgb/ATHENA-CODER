# Skill metrics

Per-skill usage tracking. Phase-16 observability counts *tools*;
skills aren't tools — they're progressive-disclosure docs the agent
reads via `skill_view` / `load_body`. T3-06R records when a skill
body is disclosed, surfaces top / stale / never-used skills in a
single CLI view, and feeds the same signal into the curator's
prune decisions.

## What's tracked

| Field | Meaning |
|-------|---------|
| `views` | Count of times the skill body was disclosed |
| `last_used_at` | UTC timestamp of the most recent disclosure |
| `sessions_used_in` | Distinct session ids that disclosed the skill |
| `outcomes` | Optional `{good, bad, preference_pair}` counts from `athena train review` |

Storage: `<profile_dir>/skill_metrics.jsonl` — append-only, one
event per line. Two shapes share the file:

```jsonl
{"event":"view","skill_name":"alpha","ts":"2026-05-20T...","session_id":"s1"}
{"event":"outcome","skill_name":"alpha","ts":"2026-05-20T...","good":1}
```

The store aggregates on read. T3-05R's
`athena.transform.suggestion.build_suggestion_fn` reads the same
file and uses the `good`/`bad` lines as the metrics-override
signal for the labeling TUI's auto-suggestion.

## The disclosure hook

Two places call `record_view`:

- `athena.skills.manager.skill_view` — when the `skill_view` MCP
  tool / programmatic call returns a body.
- `athena.skills.loader.load_body` — both cache-miss and cache-hit
  paths. Cached re-reads still count: the per-view signal is "the
  model paid attention to the skill on this turn," not "we hit the
  disk."

Misses (missing skill name) don't record. The progressive-disclosure
catalog injection (`skills/progressive_disclosure.py:build_catalog`)
also doesn't record — listing one-liners isn't disclosing bodies.

Hook fail-mode: any exception in the metrics path is swallowed.
Logging must never block the agent.

## Inspecting

```bash
athena skill metrics
# profile: default
#
# top 10 most-viewed skills:
#   api-client-generator           23 views  last=2026-05-19T...
#   build-pipeline                 12 views  last=2026-05-18T...
#
# stale (>30 days since last view):
#   legacy-wordpress-debugger       2 views  last=2026-03-15T...
#
# never viewed (3 skill(s) in the catalogue):
#   experimental-foo
#   bench-suite
#   onboarding-template
```

Flags:

- `--top N` (default 10) — how many top-viewed to show
- `--stale-days N` (default 30) — threshold for the stale list
- `--profile NAME` — non-active profile
- `-C / --cwd PATH` — workspace for the never-used join
- `--json` — machine-readable output

JSON shape:

```json
{
  "profile": "default",
  "stale_days": 30,
  "top":         [{"name": "...", "views": 23, "last_used_at": "...", "sessions_used_in": 4, "outcomes": {}}],
  "stale":       [{"name": "...", "views": 2,  "last_used_at": "...", "sessions_used_in": 1, "outcomes": {}}],
  "never_used":  ["experimental-foo", "..."]
}
```

## Curator integration

The curator (`athena curator run`) already decides which skills to
KEEP / DELETE / ABSORB. T3-06R feeds usage data into that decision
without overriding it:

1. **Prompt addendum.** Before the curator fork runs, athena
   appends a "Recent usage signal" section to the system addendum
   listing most-viewed, never-viewed (cap 20), and >30-day stale
   skills. The fork's model sees the data before making decisions;
   the curator's hard rules (untouchable `write_origin=foreground`,
   `pinned`, migration-origin) still override.

2. **REPORT.md enrichment.** `<logs_root>/curator/<ts>/REPORT.md`
   grows a "Per-skill usage (T3-06R)" section beside the decisions.
   A skill that's "never viewed" can still end up KEEP_AS_IS — the
   report shows both lines so the operator sees the tension. The
   T3-06R hard invariant *metrics inform, they don't override* is
   pinned by `test_metrics_inform_not_override`.

3. **`run.json`** gains a `"usage"` key with the same `{top,
   never_used, stale_30}` shape for downstream tooling.

## Configuration

```toml
skill_metrics_enabled = true
```

Default on. Set to `false` to install a `_NoopStore` at session
start — the hook fires but records nothing, and the curator skips
both the prompt addendum and the REPORT.md section. No need to
remove the metrics file; it just stops growing.

## Forks vs foreground

The active `SkillMetricsStore` is scoped to `Agent.run_turn` via
`ContextVar`. Fork threads spawn with their own ContextVar context
and don't inherit the parent's store — so curator and review forks
don't accidentally inflate the foreground session's view counts.

## What this is not

- Not a substitute for the audit log. The audit log records
  *mutations*; metrics record *disclosures* (read events).
- Not a substitute for Phase-16 observability. OTel spans still
  fire for the `skill_view` *tool*; metrics record which *skill*
  was loaded inside that tool call.
- Not auto-pruning. The curator's existing rule machinery still
  has the final say.
