# Capability broker

The capability broker is two cash-ins on the T5-01 capability
manifest. One side asks the manifest "which backend can do this
media operation?" and routes accordingly. The other side asks
the manifest "which of athena's distinctive capabilities can
this host actually run?" and re-exposes the available ones as
MCP tools.

Both halves are pure manifest lookups — no provider-name
special-casing.

## Media backend registry

`athena.media.MediaRegistry` resolves a named media capability
(`"vision"`, `"embeddings"`, future `"video_generation"`) to a
provider whose class-level `Capabilities` manifest declares it.

```python
from athena.media import MediaRegistry

mr = MediaRegistry(cfg=load_config())

# Class-level — cheap, no credential check.
backend = mr.backend_for("vision")            # → provider class, or None

# Credential-aware — uses runtime_resolver's available_providers_with_capability.
backend = mr.available_backend_for(
    "vision",
    pool=session.credential_pool,
)
```

Selection policy:

1. Filter the registry to providers whose manifest declares the
   capability.
2. If `cfg.media_backend_prefer == "local"` (default), pick the
   first one with `is_local=True`.
3. Otherwise (or no local declarer), fall back to the
   alphabetically-first declared provider for deterministic
   routing across runs.

Routing decisions land at INFO on `athena.media.registry`. The
no-backend case is logged too so operators see why a tool
wasn't advertised.

Convenience accessors:

| Method                  | Returns                                             |
|-------------------------|-----------------------------------------------------|
| `backend_for(cap)`      | provider class \| None                              |
| `available_backend_for(cap, *, pool)` | provider class \| None (credential-aware) |
| `candidates(cap)`       | sorted list of declaring provider names             |
| `can(cap)`              | `True` iff at least one declarer is registered     |

## Differentiated MCP surface

`athena mcp serve` exposes two layers:

| Layer            | Origin   | Names                                  |
|------------------|----------|----------------------------------------|
| Base (T3-02)     | curated  | `athena_snapshot_files`, `athena_rollback_files`, `athena_list_skills`, `athena_read_skill`, `athena_list_memories`, `athena_read_memory`, `athena_audit_query` |
| Differentiated (T5-05) | manifest-driven | `verified_write`, `rollback_to`, `list_checkpoints`, `analyze_image`, `analyze_video`, `recall` |

The differentiated layer is what makes athena worth putting in
another agent's call graph — capabilities a plain
OpenAI-compatible endpoint can't offer.

### Manifest-driven advertisement

A differentiated tool is *only listed* when its backing
capability is available on this host:

| Tool                                | Advertised iff                                          |
|-------------------------------------|---------------------------------------------------------|
| `verified_write`                    | always (T5-04 + path security are core)                 |
| `rollback_to`, `list_checkpoints`   | `checkpoint_manager` is non-None (T3-03 wired in)       |
| `analyze_image`, `analyze_video`    | `MediaRegistry.can("vision")` (≥1 provider declares it) |
| `recall`                            | always (FTS5 local)                                     |

`cfg.mcp_expose` lets an operator narrow further: when set to a
non-empty tuple, only listed tools survive the gate even if
they're otherwise available.

### Safety boundary

What the differentiated surface does **not** advertise:

- **No raw shell / code execution.** No `bash`, no `Execute`, no
  `Shell`. The T3-02 curation rule holds.
- **No raw `Write` / `Edit`.** The only write surface is
  `verified_write`, which:
  1. Routes the target path through `validate_path` (T1-07)
     so a remote agent can't escape the workspace.
  2. Runs the T5-04 verified-execution loop after the write
     and surfaces the report inline.
  3. On failure, the response is `isError=True` with the
     `/rollback-to <id>` hint inline so the calling agent can
     chain to `rollback_to`.

A test pins this: nothing matching `{bash, shell, exec,
athena_bash, Bash, Execute}` appears in the differentiated
descriptors.

### What each tool does

- **`verified_write`** — write a file, then run T5-04
  (diagnose + optional sandboxed run); response carries the
  verification report inline.
- **`rollback_to`** — invoke `CheckpointManager.rollback_to`;
  the rollback is itself undoable via the auto-created
  `pre-rollback-of-<id>` checkpoint.
- **`list_checkpoints`** — list available rollback targets for
  the session.
- **`analyze_image` / `analyze_video`** — route the request
  through `MediaRegistry.backend_for("vision")`. The wrapper
  surfaces the routing decision; the agent runtime handles the
  actual frame extraction / multimodal dispatch.
- **`recall`** — full-text search over session history (FTS5,
  local-only).

## Configuration

```toml
# How MediaRegistry breaks ties.
media_backend_prefer = "local"   # "local" (default) | "any"

# Narrow the MCP differentiated surface. Empty (default) → all
# host-available differentiated tools are advertised.
mcp_expose = ["verified_write", "recall"]
```

## Smoke

```bash
# Confirm the manifest is populated.
athena providers capabilities

# Start the server with the differentiated surface.
athena mcp serve

# From an MCP client: tools/list shows the curated `athena_*` set
# plus whatever differentiated tools are available on this host.
# Call analyze_image → routes through MediaRegistry; routing
# decision logged at INFO on athena.media.registry.
```

## Related

- [Verified execution](verified-execution.md) — `verified_write`'s
  underlying loop
- [Checkpoints](checkpoints.md) — `rollback_to` / `list_checkpoints`
- [MCP server](mcp-server.md) — the base curated surface
