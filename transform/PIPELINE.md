# PIPELINE — making your own model, end to end

The full process for "create my own model" on a single 16GB consumer GPU.
Each phase is independent — you can stop after any phase and have a usable
model. Most people only need phases 1 and 4. The full chain produces a
genuinely custom model.

```
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1  ─  Modelfile             (instant, no training)        │
│ Phase 2  ─  Merge                 (CPU only, ~30 min)           │
│ Phase 3  ─  Continued pretrain    (optional, ~12-48 hrs)        │
│ Phase 4  ─  SFT (LoRA)            (~2-6 hrs)                    │
│ Phase 5  ─  DPO                   (optional, ~2-6 hrs)          │
│ Phase 6  ─  Convert + deploy      (~30 min)                     │
└─────────────────────────────────────────────────────────────────┘
```

## Phase 1 — Modelfile

Quick win, zero training. Bake a system prompt and parameters onto an
existing base. See `Modelfile` and the main README. Use this until it
stops being enough.

## Phase 2 — Merge

Combine 2+ existing models into one. CPU only. Outputs a fresh set of
weights nobody else has.

```bash
cd merge
./run_merge.sh configs/slerp_coding_thinking.yaml ../merged_base
```

Result: `merged_base/` — HF format weights, ready as the base for any
later phase.

See `merge/README.md` for method choice and validation.

## Phase 3 — Continued pretraining (optional)

Extend the merged model's domain knowledge by training on a raw text
corpus (no instruction format) with the next-token prediction objective.

When to do this:
- You have lots of unstructured domain text (blog archive, methodology
  PDFs, book chapters, code with comments) — say, >10 MB.
- You want the model to "know" your domain at a deeper level than SFT
  can give you.

When to skip:
- If your corpus is <5 MB. Use it as SFT data instead.
- If your corpus is mostly Q&A-shaped already. Go to phase 4.

The training script for this is essentially `train_lora.py` with a
different dataset format — raw documents instead of chat messages, and
typically a lower learning rate (1e-5 to 5e-5 vs SFT's 2e-4).

```python
# pseudocode — adapt train_lora.py:
# 1. Replace chat-template formatting with concat-and-pack
# 2. Drop --epochs to 1 (continued pretrain rarely benefits from more)
# 3. Lower --lr to 5e-5
# 4. Increase --seq-len to 8192 or 16384 if VRAM allows
```

A real implementation lives in `pretrain/README.md`. (Not included as a
script here — it's a 30-line modification of train_lora.py.)

## Phase 4 — SFT (LoRA)

Inject voice and instruction-following behavior. Already documented at
the top-level README. The `--base` argument should point at your merged
model from Phase 2 (or the original base if you skipped merging):

```bash
python scripts/train_lora.py \
    --base ./merged_base \
    --train train.jsonl \
    --out ./lora_out \
    --rank 32 --epochs 3
```

## Phase 5 — DPO (optional)

Direct Preference Optimization. Train on (chosen, rejected) pairs to
shape behavior beyond what SFT can. Useful for:

- Restoring "I don't know" calibration if SFT eroded it
- Locking in subtle preferences (verbosity, formatting, when to ask
  clarifying questions)
- Suppressing specific failure modes you observed during evaluation

Format: JSONL with `{prompt, chosen, rejected}` triples. Easiest way to
build a preference dataset is to run your SFT model on a set of prompts,
then for each prompt generate two responses (different temperatures or
different sampling parameters) and label which is better. 100–500 pairs
is usually enough.

Training: TRL has `DPOTrainer`. Roughly the same code shape as the SFT
script, different trainer class, different dataset format.

This kit doesn't include DPO scripts — it's the same machinery as SFT
with a different objective. If you want it built out, ask.

## Phase 6 — Convert + deploy

Merge any LoRA adapters into the merged base, convert to GGUF, register
with Ollama. Already covered by `scripts/export_to_ollama.py`.

```bash
python scripts/export_to_ollama.py \
    --adapter ./lora_out \
    --base ./merged_base \
    --merged-out ./final \
    --gguf-out ./model.gguf \
    --quant q4_k_m \
    --llama-cpp /path/to/llama.cpp \
    --ollama-name my-model
```

## Realistic resource budget

For the 14B Qwen-family pipeline, on a 4070 Ti Super (16GB VRAM) +
typical workstation (32–64GB RAM):

| Phase | VRAM | RAM | Disk | Wall time |
|-------|------|-----|------|-----------|
| 2 — Merge (2 models) | 0 | 30–50 GB | ~80 GB | 30–60 min |
| 2 — Merge (3 models, DARE-TIES) | 0 | 40–60 GB | ~120 GB | 1–2 hr |
| 3 — Continued pretrain | 12 GB | 16 GB | corpus + ~30 GB | 12–48 hr |
| 4 — SFT (LoRA r=32) | 11–13 GB | 16 GB | ~2 GB | 2–6 hr |
| 5 — DPO | 12–14 GB | 16 GB | ~2 GB | 2–6 hr |
| 6 — Convert + GGUF | 0–4 GB | 30 GB | ~30 GB | 30 min |

Disk is the often-forgotten constraint: keeping intermediate HF weights
around for several phases adds up to a couple hundred GB. Clean up
intermediates between phases or use a separate fast drive for ML work.

## What you actually get

A model called whatever you want, with weights nobody else has, that
combines specific capabilities you chose, fine-tuned on your data,
deployed locally on Ollama. It will be roughly as capable as the most
capable ingredient in its merge — not better. Merging combines, it
doesn't multiply.

If you want a frontier-level model, no amount of merging gets you there
on a 4070 Ti Super. If you want a 14B-class model that's genuinely yours
and tuned for your work, this pipeline is exactly the path.
