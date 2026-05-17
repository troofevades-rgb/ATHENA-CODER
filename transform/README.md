# transform — make your own model

A complete pipeline for building a custom local model on a single 16GB GPU.

For the full lifecycle and how the phases fit together, see **PIPELINE.md**.
This README covers the most common path: Modelfile (Phase 1) and SFT (Phase 4).
For merging, see `merge/README.md`. For continued pretraining, see
`pretrain/README.md`.

```
transform/
  Modelfile                  Phase 1 — system prompt baked into a custom model
  merge/                     Phase 2 — combine multiple models with mergekit
  pretrain/                  Phase 3 — notes on continued pretraining
  scripts/
    prepare_dataset.py       Phase 4 — dataset prep
    train_lora.py            Phase 4 — QLoRA SFT
    export_to_ollama.py      Phase 6 — merge LoRA, GGUF, Ollama import
  examples/
    sample_dataset.jsonl     reference data format
  PIPELINE.md                full lifecycle guide
```

Three layered approaches to "transform a model," in increasing effort:

1. **Modelfile** — bake a system prompt into a custom Ollama model. 10 minutes.
2. **Merge** — combine 2+ existing models into something new. CPU only, ~30 min.
   See `merge/`.
3. **QLoRA fine-tune** — train an adapter on your data. 4–12 hours including prep.
4. **(Pair with) RAG** — separate concern; ground specific facts at query time.
   Not in this kit.

The phases compose. A typical "made my own model" sequence is: merge two
ingredients (Phase 2) → SFT on your domain data (Phase 4) → deploy (Phase 6).
See `PIPELINE.md` for the full lifecycle.

## Base model choice — read this before you run anything

There are two reasonable starting points and they have different consequences:

**A. Regular `Qwen/Qwen2.5-Coder-14B-Instruct`** (this kit's default)
   - Fine-tuning on your domain content teaches the model that your topics
     are normal assistant work — no refusals needed.
   - Calibration stays intact: "I don't know" still fires when appropriate.
   - Tool-calling reliability stays sharp.
   - Refusals get suppressed only on topics adjacent to your training data.
     Topics outside your distribution still trigger normal alignment.
   - **Right answer for deep-but-narrow use cases.** Forensic acoustics,
     OSINT on your beats, ISSO work, your code stack.

**B. `huihui_ai/Qwen2.5-Coder-14B-Instruct-abliterated`**
   - Refusals globally suppressed before you even start training.
   - Calibration cost: model becomes more confident across the board, less
     willing to say "I don't know," more prone to fabrication.
   - Tool-calling can be subtly degraded.
   - 5–10% benchmark drop on coherence.
   - **Right answer when you want global de-alignment, not domain-specific.**
     Unusual creative work, controversial topics across many domains,
     research where you genuinely need the model to engage with anything.

Fine-tuning the abliterated variant on a narrow domain dataset is the worst
of both worlds: you pay the abliteration tax across the board AND deepen
the calibration problem (your training data is mostly confident answers,
which trains away appropriate hedging). Start from the regular base unless
you have a specific reason for global de-alignment.



**Will:**
- Lock in your voice, terseness, default structure
- Make the model start in your idiom without a giant system prompt
- Internalize domain vocabulary (TDOA, GCC-PHAT, STIG, NISP, etc.)
- Reduce sycophancy and filler if your training data lacks them
- Default to your preferred output formats (numbered phases, units explicit, etc.)

**Won't:**
- Memorize specific case facts (use RAG)
- Make a 14B model into a frontier model
- Survive contradictory instruction in a system prompt — it will obey the prompt
- Replace your judgment on technical correctness — verify outputs

## Layer 1: Modelfile (do this first)

This already gets you most of the way there. No training needed.

```bash
ollama create troofevades -f Modelfile
ollama run troofevades
```

Or point athena at it: `model = "troofevades"` in `~/.athena/config.toml`.

If layer-1 output is still close enough to "generic Qwen" after a couple
weeks of use, you have a real motivation to go to layer 2.

## Layer 2: QLoRA fine-tune

### Prerequisites

```bash
# CUDA 12.1 toolkit installed
# Driver ≥ 535
pip install --upgrade torch  # bf16-capable build for Ampere+
pip install --upgrade --no-cache-dir \
    "unsloth[cu121-ampere-torch240] @ git+https://github.com/unslothai/unsloth.git"
pip install --no-deps trl peft accelerate bitsandbytes datasets transformers
```

(Check the Unsloth README for the current install string — they update it
when PyTorch / CUDA versions move.)

For the export step you also need llama.cpp built locally:

```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build -j$(nproc)
```

### Step 1: collect your data

Put materials under `./raw/`:

- `raw/*.md` — long-form articles (Substack drafts, methodology docs).
  Each becomes a "write an article on: TOPIC" → article example.
- `raw/qa/*.md` — explicit Q/A pairs:

  ```
  ## Q
  Explain GCC-PHAT in two paragraphs.
  ## A
  Generalized Cross-Correlation with Phase Transform...
  ---
  ## Q
  ...
  ```

- `raw/*.jsonl` — pre-formatted ChatML messages (passed through unchanged):

  ```json
  {"messages":[{"role":"user","content":"..."},{"role":"assistant","content":"..."}]}
  ```

See `examples/sample_dataset.jsonl` for the format target.

**Realistic dataset size**: 100–500 examples is the sweet spot for voice
transfer. More is fine, but past ~2000 high-quality examples returns
diminish fast for a single-author style fine-tune. Quality over quantity.

**The calibration trap.** If your dataset is 100% confident technical
answers, you train the model to be *more* confident across the board —
including on questions where it should hedge. Include 10–20% examples
where the assistant explicitly:
- declines to answer because it lacks data ("I don't have that — pull X")
- asks for clarifying inputs before committing
- answers conditionally ("probably not, depends on geometry; need Y to confirm")
- declines to characterize a third party's work without seeing it

The sample dataset has six of these mixed in. Mirror that ratio in yours.
Fine-tuning is supervised — the model imitates patterns. If "I don't know"
isn't in your training set, it won't be in the model's behavior.

**Tool-use examples.** If you want the fine-tuned model to keep working
inside athena, your training data needs tool-call examples. The sample
dataset includes three (read_file, bash, grep) showing the assistant
producing a tool_calls payload with empty content. Without these, the
fine-tune may displace tool-calling patterns from the base model in favor
of prose responses.

**Avoid**: noisy chat logs, anything you wouldn't be proud to publish, content
that contradicts itself across examples.

### Step 2: prepare the dataset

```bash
python scripts/prepare_dataset.py \
    --raw ./raw \
    --tokenizer Qwen/Qwen2.5-Coder-14B-Instruct
```

Produces `train.jsonl`, `val.jsonl`, `stats.txt`. Read `stats.txt` — if your
p95 token count exceeds your training `--seq-len`, examples will be truncated.
Either bump `--seq-len` (costs VRAM) or split long articles into pieces.

### Step 3: train

For a smoke test first (50 steps, ~10 minutes on a 4070 Ti Super):

```bash
python scripts/train_lora.py --max-steps 50
```

If that completes without OOM, run full:

```bash
python scripts/train_lora.py \
    --base Qwen/Qwen2.5-Coder-14B-Instruct \
    --epochs 3 \
    --rank 32 \
    --seq-len 4096 \
    --batch 2 \
    --grad-accum 4 \
    --lr 2e-4 \
    --out lora_out
```

Full training time on a 4070 Ti Super for ~300 examples × 3 epochs ≈ 1–3 hours.

If you OOM:
- drop `--seq-len 2048`
- drop `--rank 16`
- drop `--batch 1` and bump `--grad-accum 8`

If validation loss starts climbing partway through, your dataset is too
small for 3 epochs — drop to 2 or even 1.

### Step 4: export to Ollama

```bash
python scripts/export_to_ollama.py \
    --adapter ./lora_out \
    --merged-out ./merged \
    --gguf-out ./troofevades-tuned-q5km.gguf \
    --quant q5_k_m \
    --llama-cpp /path/to/llama.cpp \
    --ollama-name troofevades-tuned
```

Then test: `ollama run troofevades-tuned`.

For athena: set `model = "troofevades-tuned"` in `~/.athena/config.toml`.

### Step 5: evaluate honestly

Side-by-side with the base model on 20 held-out prompts. Score qualitatively:

- Did voice transfer? (Should feel like reading your own writing)
- Did the model lose general capabilities? (Run a few non-domain questions)
- Are tool calls still working? (Test inside athena)

If voice didn't transfer: dataset too small, or too inconsistent in style.
If tool calls broke: rank too high (try 16) or training data lacked any
tool-use examples.

## Layer 3: RAG (mentioned for completeness, not implemented here)

For factual grounding (case files, exact methodology steps, prior findings):

- Embed your corpus with `sentence-transformers/all-MiniLM-L6-v2` or larger
- Store in chromadb / sqlite-vss / faiss locally
- At query time, retrieve top-k chunks, inject into the system prompt
- Tool-call from athena: add a `search_corpus` tool

This is a separate kit. Different concern: facts, not voice.

## Troubleshooting

**"unsloth not found"**: the install string is CUDA-version-specific. Visit
https://github.com/unslothai/unsloth for the current command.

**"AttributeError: ... bnb"**: bitsandbytes mismatch with torch. Pin
versions: `pip install bitsandbytes==0.43.3`.

**"CUDA out of memory"**: see "If you OOM" above.

**"convert_hf_to_gguf.py not found"**: pull a recent llama.cpp; the file
got renamed (used to be hyphenated).

**Trained model gives gibberish**: check that `tokenizer.apply_chat_template`
during training matches the template Ollama uses at inference. The Modelfile
inherits the base model's template by default — don't override it unless you
have a specific reason.
