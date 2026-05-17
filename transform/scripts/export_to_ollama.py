"""Merge a trained LoRA adapter into its base model, convert to GGUF, and
import the result as an Ollama model.

Workflow:
  1. Load base + adapter, merge weights (PEFT merge_and_unload).
  2. Save merged weights as HF format.
  3. Run llama.cpp's convert + quantize to produce a GGUF.
  4. Write a Modelfile that FROMs the GGUF and applies the SYSTEM prompt.
  5. `ollama create` the model.

Requires llama.cpp checked out somewhere (env var LLAMA_CPP, or --llama-cpp).
For Qwen2.5-Coder, llama.cpp's `convert_hf_to_gguf.py` is the right entrypoint.

Usage:
    python export_to_ollama.py \\
        --adapter ./lora_out \\
        --merged-out ./merged \\
        --gguf-out ./model.gguf \\
        --quant q4_k_m \\
        --ollama-name troofevades-tuned
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_SYSTEM = """\
You are a forensic acoustic analyst and investigative writer. Lead with the
answer, cite quantitative uncertainty (95% CI, RMSE), keep it terse and
technically precise. Reference the 10-phase forensic acoustic methodology
(Intake, Characterization, Environment, Classification, Geolocation, TOA,
Sync, TDOA Multilateration, Uncertainty, Reporting) when relevant.
"""


def run(cmd: list[str], **kw) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def merge_lora(adapter: Path, base: str | None, merged_out: Path) -> None:
    """Merge LoRA adapter into base model and save to disk in HF format."""
    print(f"Merging {adapter} into base, writing to {merged_out}")
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import torch

    # Adapter dir contains adapter_config.json with base_model_name_or_path
    adapter_cfg = json.loads((adapter / "adapter_config.json").read_text())
    base_id = base or adapter_cfg["base_model_name_or_path"]
    print(f"  base: {base_id}")

    tokenizer = AutoTokenizer.from_pretrained(base_id, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        base_id,
        torch_dtype=torch.bfloat16,
        device_map="cpu",          # merge on CPU to avoid VRAM spike
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, str(adapter))
    model = model.merge_and_unload()
    merged_out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(merged_out, safe_serialization=True)
    tokenizer.save_pretrained(merged_out)
    print(f"  merged weights -> {merged_out}")


def convert_to_gguf(merged: Path, gguf_out: Path, quant: str, llama_cpp: Path) -> None:
    """Use llama.cpp tooling to produce a quantized GGUF."""
    convert = llama_cpp / "convert_hf_to_gguf.py"
    if not convert.exists():
        # older llama.cpp checkouts called it convert-hf-to-gguf.py
        alt = llama_cpp / "convert-hf-to-gguf.py"
        if alt.exists():
            convert = alt
        else:
            sys.exit(f"could not find convert_hf_to_gguf.py under {llama_cpp}")

    # First produce an unquantized fp16 GGUF, then quantize.
    fp16_gguf = gguf_out.with_suffix(".fp16.gguf")
    run([sys.executable, str(convert), str(merged),
         "--outfile", str(fp16_gguf), "--outtype", "f16"])

    quantize = llama_cpp / "build" / "bin" / "llama-quantize"
    if not quantize.exists():
        # fallback to old layout
        quantize = llama_cpp / "llama-quantize"
    if not quantize.exists():
        sys.exit(
            f"could not find llama-quantize under {llama_cpp}. "
            "Build llama.cpp first: cmake -B build && cmake --build build -j"
        )
    run([str(quantize), str(fp16_gguf), str(gguf_out), quant])
    fp16_gguf.unlink(missing_ok=True)
    print(f"  GGUF -> {gguf_out}")


def write_modelfile(gguf: Path, system: str, modelfile_path: Path) -> None:
    body = f"""FROM {gguf.resolve()}
PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.05
PARAMETER num_ctx 32768
SYSTEM \"\"\"
{system.strip()}
\"\"\"
"""
    modelfile_path.write_text(body, encoding="utf-8")
    print(f"  Modelfile -> {modelfile_path}")


def ollama_create(name: str, modelfile: Path) -> None:
    if shutil.which("ollama") is None:
        print("ollama not found on PATH. Run manually:")
        print(f"    ollama create {name} -f {modelfile}")
        return
    run(["ollama", "create", name, "-f", str(modelfile)])
    print(f"  registered as ollama model: {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, type=Path)
    ap.add_argument("--base", default=None,
                    help="Override base model id (default: read from adapter config)")
    ap.add_argument("--merged-out", type=Path, default=Path("merged"))
    ap.add_argument("--gguf-out", type=Path, default=Path("model.gguf"))
    ap.add_argument("--quant", default="q4_k_m",
                    choices=["q4_k_m", "q5_k_m", "q6_k", "q8_0", "f16"])
    ap.add_argument("--llama-cpp", type=Path,
                    default=Path(os.environ.get("LLAMA_CPP", "./llama.cpp")))
    ap.add_argument("--ollama-name", default="troofevades-tuned")
    ap.add_argument("--system-file", type=Path, default=None,
                    help="Read SYSTEM prompt from a file (default: built-in)")
    ap.add_argument("--skip-merge", action="store_true",
                    help="Skip merge step (use existing --merged-out)")
    ap.add_argument("--skip-gguf", action="store_true",
                    help="Skip GGUF conversion (use existing --gguf-out)")
    args = ap.parse_args()

    if not args.skip_merge:
        merge_lora(args.adapter, args.base, args.merged_out)

    if not args.skip_gguf:
        convert_to_gguf(args.merged_out, args.gguf_out, args.quant, args.llama_cpp)

    system = (args.system_file.read_text(encoding="utf-8")
              if args.system_file else DEFAULT_SYSTEM)
    modelfile = args.gguf_out.with_suffix(".Modelfile")
    write_modelfile(args.gguf_out, system, modelfile)
    ollama_create(args.ollama_name, modelfile)

    print("\ndone. test it:")
    print(f"    ollama run {args.ollama_name}")
    print(f"or in athena: set model = \"{args.ollama_name}\" in ~/.athena/config.toml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
