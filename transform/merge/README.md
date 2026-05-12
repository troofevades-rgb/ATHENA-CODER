# merge — combine existing models into something new

This is how most "novel" community models actually get made. No GPU required;
mergekit runs on CPU with weights streamed from disk. Time: minutes to about
an hour for two 14B models, depending on disk speed and RAM.

## Methods, ranked by when to use them

### SLERP — for two models, simple and well-behaved
Spherical linear interpolation. Smooth blend along the parameter sphere.
Works well when both models share the same base lineage (e.g. two Qwen2.5
fine-tunes). Default choice for a coding+reasoning hybrid.

```bash
./run_merge.sh configs/slerp_coding_thinking.yaml my-coder-thinker
```

### DARE-TIES — for three or more models, with conflict resolution
DARE sparsifies each model's parameter delta from a base; TIES resolves
sign disagreements when models pull a parameter in opposite directions.
Use this when you're combining 3+ models, or when SLERP produces a model
that "averages out" the strengths instead of preserving them.

```bash
./run_merge.sh configs/dare_ties_three_way.yaml my-three-way-merge
```

### Passthrough (frankenmerge) — experimental, layer concatenation
Stack layers from different models into a deeper model. Result has more
parameters than any input. Sometimes brilliant, sometimes broken.

```bash
./run_merge.sh configs/passthrough_franken.yaml my-franken-17b
```

### Linear / Task Arithmetic — for behavior shaping
Not in the configs/ here; useful when you want to add or subtract specific
behaviors (`merged = base + α·(instruct_finetune - base)`). Read the
mergekit docs if you go down this path.

## What makes a clean merge

1. **Same architecture.** Layer dimensions, attention heads, hidden size
   must match. Qwen2.5 14B variants all merge cleanly with each other.
   Qwen + Llama? Don't.

2. **Same tokenizer.** If two models tokenize differently, merging the
   weights produces a model that doesn't know which tokenizer it has.
   The Qwen2.5 family all share one tokenizer; safe.

3. **Same chat template.** If you merge a chat-tuned and a base model,
   the output may not respond to either's prompt format reliably. Stick
   to instruct + instruct, or accept that you'll re-tune the template.

4. **Sensible weights.** Adding more models doesn't help if their weights
   sum to too much pull in any direction. Test with weights summing to
   ~1.0 first; deviate only with reason.

## How much RAM do you actually need?

For the 14B configs here, you want **~30–50GB of RAM** in bf16. mergekit
streams weights from disk in chunks, so peak usage is roughly the size of
one or two layer's worth of parameters at a time, plus the in-memory delta.
On a typical 32GB workstation: add `--lazy-unpickle` (already in the runner
script) and it works, just slower.

If you run out of RAM the symptom is OOM-kill mid-merge. mergekit doesn't
checkpoint, so you start over. Watch RAM during the first few minutes.

## Validating a merge

Merges fail in subtle ways. Signs of a broken merge:
- Model loops on the same token
- Output is grammatically OK but semantically nonsense
- Tool calls are malformed (wrong JSON, missing fields)
- Refuses everything or accepts everything (calibration destroyed)
- Severely degraded benchmark scores

Test plan:
1. Load merged weights in transformers, run 5 short prompts, eyeball.
2. Convert to GGUF, run in Ollama, hit it with 10 prompts of varied type
   (factual, code, reasoning, tool-call, creative).
3. Compare side-by-side with each ingredient model on the same prompts.
4. If a merge is worse than the worst ingredient, throw it out and tweak
   the YAML — not all merges land.

## After merging

The merged model is your new base. From here:

- **Use it as-is** in Ollama via the export script
- **Continue-pretrain** on a domain corpus to deepen knowledge
- **SFT** with `transform/scripts/train_lora.py --base <merged_dir>` to
  inject your voice on top of the merged capabilities
- **DPO** on preference pairs to refine behavior further

Each step is independent. You can do just a merge and stop, or chain all
the way through.
