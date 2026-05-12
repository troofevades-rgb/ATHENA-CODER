# Continued pretraining — notes, not code

Continued pretraining (CPT) takes a model and trains it further on raw
text with the next-token prediction objective — the same thing the model
was originally trained on, just on your corpus.

## When you actually want this

CPT shines when:
- You have >10 MB of clean domain text (articles, books, methodology
  documents, code with rich comments).
- The text is *not* in instruction/response format.
- You want the model to internalize domain vocabulary and idioms beyond
  what SFT examples can teach.

CPT is overkill when:
- Your corpus is small (<5 MB) — use it as SFT data instead.
- Your text is already in Q/A format — go to SFT.
- You just want voice transfer — Modelfile + SFT is enough.

## How to do it (modify train_lora.py)

You don't need a new script — the existing SFT trainer handles raw text
fine if you bypass the chat-template formatting step. Three edits to
`scripts/train_lora.py`:

### 1. Replace the chat-template formatter

```python
# OLD (from train_lora.py):
def format_example(example):
    text = tokenizer.apply_chat_template(
        example["messages"], tokenize=False, add_generation_prompt=False,
    )
    return {"text": text}

# NEW for CPT:
def format_example(example):
    return {"text": example["text"]}
```

### 2. Change the dataset shape

CPT data is JSONL with one document per line: `{"text": "..."}`.

A simple corpus loader (replaces prepare_dataset.py for this phase):

```python
import json
from pathlib import Path

raw_dir = Path("./pretrain_corpus")
out = Path("./pretrain.jsonl")

with out.open("w", encoding="utf-8") as f:
    for p in raw_dir.rglob("*.md"):
        text = p.read_text(encoding="utf-8")
        if len(text) < 200:
            continue
        f.write(json.dumps({"text": text}) + "\n")
    for p in raw_dir.rglob("*.txt"):
        text = p.read_text(encoding="utf-8")
        if len(text) < 200:
            continue
        f.write(json.dumps({"text": text}) + "\n")
```

### 3. Adjust hyperparameters

In `TrainingArguments`:
- `learning_rate=5e-5`  (much lower than SFT's 2e-4)
- `num_train_epochs=1`  (CPT rarely benefits from multiple epochs)
- `warmup_ratio=0.03`

In the LoRA config:
- `r=64`  (higher rank for CPT — you're updating world-knowledge, not
  just style)
- target *all* linear layers, not just q/k/v/o + MLP

In the SFTTrainer:
- `packing=True`  (concatenates short documents into longer sequences;
  much better throughput for raw-text CPT)

### 4. Run

```bash
python scripts/train_lora.py \
    --base ./merged_base \
    --train pretrain.jsonl \
    --rank 64 --lr 5e-5 --epochs 1 \
    --seq-len 8192 \
    --out ./pretrain_out
```

(You'll need to add the missing CLI flags to the script if they aren't
already there — `--lr` is, `--epochs` is, but `packing` isn't exposed.
Edit it inline.)

After CPT, the resulting adapter (or merged-back base) is your new "base"
for Phase 4 (SFT). Don't skip SFT after CPT — CPT teaches knowledge,
SFT teaches conversational behavior. You need both.

## What "deeper" looks like in practice

A non-CPT model fine-tuned on 200 forensic-acoustics examples will mimic
your voice on those examples. Ask it about a topic adjacent to but outside
those 200 examples and it falls back to whatever the base model knows.

A CPT-then-SFT model has had its world-knowledge representations updated
with your domain corpus before voice training. It will reach for your
domain's idioms and references on adjacent topics too. The downside: CPT
slightly degrades general capability outside your domain — pay attention
to whether non-domain tasks (basic coding, math, summarization) get
worse, and skip CPT if they do.
