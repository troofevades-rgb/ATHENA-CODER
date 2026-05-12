#!/usr/bin/env bash
# Run a mergekit recipe end-to-end and convert the result for Ollama.
#
# Prerequisites:
#   pip install mergekit
#   git clone + build llama.cpp (for GGUF conversion downstream)
#
# Usage:
#   ./run_merge.sh configs/slerp_coding_thinking.yaml my-merged-model
#
# The merge runs CPU-only by default and streams weights from disk —
# you do NOT need GPU memory for this step. RAM requirement: roughly
# 1.5x the largest single model in the merge (~50GB for 14B in bf16).
# If you don't have that much RAM, add --lazy-unpickle to the mergekit call;
# it will be slower but works on smaller systems.

set -euo pipefail

CONFIG="${1:?usage: run_merge.sh CONFIG.yaml OUT_DIR [extra mergekit args...]}"
OUT_DIR="${2:?usage: run_merge.sh CONFIG.yaml OUT_DIR [extra mergekit args...]}"
shift 2 || true

if ! command -v mergekit-yaml >/dev/null 2>&1; then
  echo "error: mergekit-yaml not on PATH. Install with:  pip install mergekit"
  exit 1
fi

if [[ -e "$OUT_DIR" ]]; then
  echo "error: $OUT_DIR exists. Remove or pick a new name."
  exit 1
fi

echo "==> merging from $CONFIG  -->  $OUT_DIR"
mergekit-yaml "$CONFIG" "$OUT_DIR" \
  --copy-tokenizer \
  --allow-crimes \
  --out-shard-size 5B \
  --lazy-unpickle \
  "$@"

echo
echo "==> merge complete. weights in: $OUT_DIR"
echo
echo "Next steps:"
echo "  1. Convert to GGUF:"
echo "       python ../scripts/export_to_ollama.py \\"
echo "           --skip-merge --merged-out $OUT_DIR \\"
echo "           --gguf-out ${OUT_DIR}.gguf --quant q4_k_m \\"
echo "           --llama-cpp /path/to/llama.cpp \\"
echo "           --ollama-name $(basename $OUT_DIR)"
echo
echo "  2. (optional) Now run SFT on top of this merged base:"
echo "       python ../scripts/train_lora.py --base $OUT_DIR ..."
