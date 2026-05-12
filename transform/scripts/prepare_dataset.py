"""Prepare a JSONL dataset for QLoRA fine-tuning.

Reads from these sources (any subset):
  - ./raw/*.md / *.txt        : long-form articles, methodology docs
  - ./raw/*.jsonl             : pre-formatted ChatML messages (passed through)
  - ./raw/qa/*.md             : Q&A pairs split by '---' separators

Emits:
  - ./train.jsonl   : 90% of examples
  - ./val.jsonl     : 10% holdout
  - stats.txt       : token-count summary for sanity-checking

Format target (one JSON object per line):
  {"messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]}

Optional system messages are honored if present.

Usage:
    python prepare_dataset.py --raw ./raw --out . --tokenizer Qwen/Qwen2.5-Coder-14B-Instruct
"""
from __future__ import annotations
import argparse
import json
import random
import re
from pathlib import Path


def parse_qa_md(text: str) -> list[dict]:
    """Parse a markdown file with '---'-separated Q/A pairs.

    Format expected:
        ## Q
        what is X?
        ## A
        explanation of X
        ---
        ## Q
        ...
    """
    examples = []
    blocks = re.split(r"\n-{3,}\n", text.strip())
    for block in blocks:
        q_match = re.search(r"##\s*Q\s*\n(.*?)(?=\n##\s*A|\Z)", block, re.S)
        a_match = re.search(r"##\s*A\s*\n(.*)", block, re.S)
        if q_match and a_match:
            examples.append({
                "messages": [
                    {"role": "user", "content": q_match.group(1).strip()},
                    {"role": "assistant", "content": a_match.group(1).strip()},
                ]
            })
    return examples


def article_to_example(text: str, title: str) -> dict | None:
    """Wrap a long-form article as 'write an article about X' -> article.

    Crude but works: assumes the first H1/H2 heading is the topic. If no
    heading, uses the filename. Returned example exemplifies long-form voice.
    """
    text = text.strip()
    if len(text) < 200:
        return None
    # Pull the first heading as topic if present
    m = re.search(r"^#{1,2}\s+(.+)$", text, re.M)
    topic = m.group(1).strip() if m else title.replace("_", " ").replace("-", " ")
    return {
        "messages": [
            {"role": "user", "content": f"Write an article on the topic: {topic}"},
            {"role": "assistant", "content": text},
        ]
    }


def passthrough_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file of pre-formatted messages."""
    out = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  ! {path.name}:{i} bad JSON: {e}")
                continue
            if "messages" not in obj or not isinstance(obj["messages"], list):
                print(f"  ! {path.name}:{i} missing 'messages' key")
                continue
            out.append(obj)
    return out


def gather(raw_dir: Path) -> list[dict]:
    examples: list[dict] = []
    if not raw_dir.exists():
        print(f"raw dir {raw_dir} does not exist; nothing to do")
        return examples

    # Pre-formatted JSONL (highest priority — already correctly shaped)
    for p in sorted(raw_dir.glob("*.jsonl")):
        added = passthrough_jsonl(p)
        examples.extend(added)
        print(f"  + {p.name}: {len(added)} examples")

    # Q/A markdown files in raw/qa/
    qa_dir = raw_dir / "qa"
    if qa_dir.exists():
        for p in sorted(qa_dir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            added = parse_qa_md(text)
            examples.extend(added)
            print(f"  + qa/{p.name}: {len(added)} Q/A pairs")

    # Long-form articles
    for p in sorted(raw_dir.glob("*.md")):
        if p.parent.name == "qa":
            continue
        text = p.read_text(encoding="utf-8")
        ex = article_to_example(text, p.stem)
        if ex:
            examples.append(ex)
            print(f"  + {p.name}: 1 long-form example")

    for p in sorted(raw_dir.glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        ex = article_to_example(text, p.stem)
        if ex:
            examples.append(ex)
            print(f"  + {p.name}: 1 long-form example")

    return examples


def token_stats(examples: list[dict], tokenizer_name: str | None) -> str:
    if not tokenizer_name:
        return "(skipped tokenizer stats — pass --tokenizer to enable)"
    try:
        from transformers import AutoTokenizer  # type: ignore
    except ImportError:
        return "(transformers not installed; skipped tokenizer stats)"
    tok = AutoTokenizer.from_pretrained(tokenizer_name)
    counts = []
    for ex in examples:
        text = tok.apply_chat_template(ex["messages"], tokenize=False)
        counts.append(len(tok.encode(text)))
    if not counts:
        return "(no examples)"
    counts.sort()
    n = len(counts)
    mean = sum(counts) / n
    p50 = counts[n // 2]
    p95 = counts[min(n - 1, int(n * 0.95))]
    return (
        f"  examples: {n}\n"
        f"  tokens — mean {mean:.0f}  median {p50}  p95 {p95}  max {counts[-1]}\n"
        f"  total tokens: {sum(counts):,}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=Path("raw"), help="Source directory")
    ap.add_argument("--out", type=Path, default=Path("."), help="Output directory")
    ap.add_argument("--val-frac", type=float, default=0.1, help="Validation fraction")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tokenizer", default=None,
                    help="HF model name to compute token stats (optional)")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"Reading from {args.raw}")
    examples = gather(args.raw)
    if not examples:
        print("No examples found.")
        return 1

    rng = random.Random(args.seed)
    rng.shuffle(examples)
    n_val = max(1, int(len(examples) * args.val_frac))
    val = examples[:n_val]
    train = examples[n_val:]

    train_path = args.out / "train.jsonl"
    val_path = args.out / "val.jsonl"
    with train_path.open("w", encoding="utf-8") as f:
        for ex in train:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    with val_path.open("w", encoding="utf-8") as f:
        for ex in val:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    stats = (
        f"train: {len(train)} examples -> {train_path}\n"
        f"val:   {len(val)} examples -> {val_path}\n\n"
        f"All:\n{token_stats(examples, args.tokenizer)}\n"
    )
    print("\n" + stats)
    (args.out / "stats.txt").write_text(stats, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
