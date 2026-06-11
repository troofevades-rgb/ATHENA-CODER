"""Training runner: argv construction and exit-code passthrough.

Tests inject a fake ``runner=`` callable so no real subprocess fires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from athena.transform import runner as runner_mod
from athena.transform.runner import (
    TrainingRun,
    TransformScriptsNotFound,
    _default_transform_dir,
    export_to_gguf,
    find_lora_adapter,
    run_dpo,
    run_lora,
)


@pytest.fixture
def trun(tmp_path: Path) -> TrainingRun:
    return TrainingRun(
        base_model="Qwen/Qwen2.5-Coder-14B-Instruct",
        sft_dataset=tmp_path / "sft.jsonl",
        output_dir=tmp_path / "out",
        epochs=2,
        learning_rate=1e-4,
        batch_size=4,
        lora_rank=16,
        lora_alpha=16,
    )


def test_run_lora_calls_train_script_with_args(trun: TrainingRun, tmp_path: Path):
    """Argv must include every flag the existing train_lora.py understands,
    using its actual names (--base, --train, --out, --rank, --alpha, --batch,
    --lr) — not athena-side terminology."""
    captured: dict = {}

    def fake_call(cmd, cwd=None):
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        return 0

    rc = run_lora(trun, runner=fake_call)
    assert rc == 0
    cmd = captured["cmd"]
    # train_lora.py is in the command:
    assert any("train_lora.py" in part for part in cmd)
    # Existing v1 flag names:
    assert "--base" in cmd
    assert "--train" in cmd
    assert "--out" in cmd
    assert "--rank" in cmd
    assert "--alpha" in cmd
    assert "--batch" in cmd
    assert "--lr" in cmd
    # Values flow through:
    base_idx = cmd.index("--base")
    assert cmd[base_idx + 1] == "Qwen/Qwen2.5-Coder-14B-Instruct"
    rank_idx = cmd.index("--rank")
    assert cmd[rank_idx + 1] == "16"


def test_run_lora_returns_exit_code(trun: TrainingRun):
    def fake_call(cmd, cwd=None):
        return 7

    assert run_lora(trun, runner=fake_call) == 7


def test_run_lora_uses_transform_dir_as_cwd(trun: TrainingRun, tmp_path: Path):
    captured: dict = {}

    def fake_call(cmd, cwd=None):
        captured["cwd"] = cwd
        return 0

    fake_transform = tmp_path / "fake-transform"
    fake_transform.mkdir()
    (fake_transform / "scripts").mkdir()
    run_lora(trun, transform_dir=fake_transform, runner=fake_call)
    assert captured["cwd"] == str(fake_transform)


def test_run_lora_includes_extra_args(trun: TrainingRun):
    captured: dict = {}

    def fake_call(cmd, cwd=None):
        captured["cmd"] = list(cmd)
        return 0

    trun.extra_args = ["--seed", "42"]
    run_lora(trun, runner=fake_call)
    cmd = captured["cmd"]
    assert "--seed" in cmd
    assert "42" in cmd


def test_run_dpo_takes_sft_lora_path(trun: TrainingRun, tmp_path: Path):
    trun.dpo_dataset = tmp_path / "dpo.jsonl"
    captured: dict = {}

    def fake_call(cmd, cwd=None):
        captured["cmd"] = list(cmd)
        return 0

    sft_lora = tmp_path / "lora-out" / "lora_out"
    rc = run_dpo(trun, sft_lora, runner=fake_call)
    assert rc == 0
    cmd = captured["cmd"]
    assert any("train_dpo.py" in part for part in cmd)
    sft_idx = cmd.index("--sft-lora")
    assert cmd[sft_idx + 1] == str(sft_lora)
    # DPO output dir is sibling of LoRA's, suffix -dpo:
    out_idx = cmd.index("--out")
    assert cmd[out_idx + 1].endswith("-dpo")


def test_run_dpo_requires_dpo_dataset(trun: TrainingRun, tmp_path: Path):
    """A TrainingRun without a DPO dataset must reject before invoking the script."""
    assert trun.dpo_dataset is None  # default
    with pytest.raises(ValueError, match="dpo_dataset"):
        run_dpo(trun, tmp_path / "ignored", runner=lambda *a, **k: 0)


def test_export_to_gguf_calls_export_script(tmp_path: Path):
    captured: dict = {}

    def fake_call(cmd, cwd=None):
        captured["cmd"] = list(cmd)
        return 0

    rc = export_to_gguf(
        tmp_path / "lora_out",
        ollama_name="qwen-athena-1",
        runner=fake_call,
    )
    assert rc == 0
    cmd = captured["cmd"]
    assert any("export_to_ollama.py" in part for part in cmd)
    name_idx = cmd.index("--ollama-name")
    assert cmd[name_idx + 1] == "qwen-athena-1"
    adapter_idx = cmd.index("--adapter")
    assert cmd[adapter_idx + 1] == str(tmp_path / "lora_out")


def test_export_to_gguf_optional_base_model(tmp_path: Path):
    captured: dict = {}

    def fake_call(cmd, cwd=None):
        captured["cmd"] = list(cmd)
        return 0

    export_to_gguf(
        tmp_path / "lora_out",
        ollama_name="x",
        base_model="Qwen/Qwen2.5-Coder-1.5B",
        runner=fake_call,
    )
    cmd = captured["cmd"]
    base_idx = cmd.index("--base")
    assert cmd[base_idx + 1] == "Qwen/Qwen2.5-Coder-1.5B"


# ---------------------------------------------------------------------------
# transform-dir resolution (wheel-install regression)
# ---------------------------------------------------------------------------


def test_default_transform_dir_found_in_source_checkout():
    """In a source checkout, the resolver finds repo_root/transform."""
    d = _default_transform_dir()
    assert (d / "scripts").is_dir()


def test_default_transform_dir_env_override(tmp_path: Path, monkeypatch):
    override = tmp_path / "my-transform"
    (override / "scripts").mkdir(parents=True)
    monkeypatch.setenv("ATHENA_TRANSFORM_DIR", str(override))
    assert _default_transform_dir() == override


def test_default_transform_dir_bad_override_raises(tmp_path: Path, monkeypatch):
    """A pointed-but-script-less override must fail the preflight up
    front, not hand back a path the subprocess then chokes on."""
    override = tmp_path / "empty-transform"
    override.mkdir()  # no scripts/ subdir
    monkeypatch.setenv("ATHENA_TRANSFORM_DIR", str(override))
    with pytest.raises(TransformScriptsNotFound, match="no scripts/"):
        _default_transform_dir()


def test_default_transform_dir_raises_on_wheel_layout(tmp_path: Path, monkeypatch):
    """Regression: a pip/pipx install doesn't ship transform/scripts/,
    so the resolver used to hand back a non-existent path and the
    subprocess failed cryptically. Now it raises an actionable error."""
    monkeypatch.delenv("ATHENA_TRANSFORM_DIR", raising=False)
    # Point the resolver's __file__ base at a fake site-packages layout
    # with no sibling transform/scripts/.
    fake_pkg = tmp_path / "site-packages" / "athena" / "transform"
    fake_pkg.mkdir(parents=True)
    monkeypatch.setattr(runner_mod, "__file__", str(fake_pkg / "runner.py"))
    with pytest.raises(TransformScriptsNotFound, match="SOURCE CHECKOUT"):
        _default_transform_dir()


def test_run_lora_surfaces_missing_scripts(trun: TrainingRun, monkeypatch):
    """run_lora propagates the actionable error when scripts are absent
    and no transform_dir override is given."""
    monkeypatch.delenv("ATHENA_TRANSFORM_DIR", raising=False)

    def _raise() -> Path:
        raise TransformScriptsNotFound("no scripts")

    monkeypatch.setattr(runner_mod, "_default_transform_dir", _raise)
    with pytest.raises(TransformScriptsNotFound):
        run_lora(trun, runner=lambda *a, **k: 0)


# ---- find_lora_adapter -------------------------------------------------


def test_find_lora_adapter_under_output_dir(tmp_path: Path):
    out = tmp_path / "out"
    (out / "lora_out").mkdir(parents=True)
    assert find_lora_adapter(out) == out / "lora_out"


def test_find_lora_adapter_recognizes_adapter_config(tmp_path: Path):
    """An output dir that already IS the adapter (peft layout) is returned as-is."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert find_lora_adapter(out) == out


def test_find_lora_adapter_returns_none_when_absent(tmp_path: Path):
    assert find_lora_adapter(tmp_path / "nothing-here") is None
