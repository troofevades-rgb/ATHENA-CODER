"""T7-02.2 — `athena batch` CLI integration tests.

Stub run_headless so the suite doesn't boot a real Agent.
Tests the full CLI plumbing: argparse, output-dir resolution,
serial + parallel paths, --force, --json envelope, exit
code semantics.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from athena.headless.result import RunResult


def _build_run_result(
    *,
    task: str,
    run_id: str,
    workspace: Any,
    status: str = "ok",
    error: str | None = None,
):
    return RunResult(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        started_at="2026-05-21T00:00:00.000000Z",
        finished_at="2026-05-21T00:00:01.000000Z",
        duration_s=1.0,
        task=task,
        workspace=str(workspace),
        model="stub-model",
        profile="default",
        session_id="s-stub-1",
        tool_calls=[],
        tokens={"prompt": 0, "completion": 0, "cache_read": 0, "cache_creation": 0},
        cost_est=0.0,
        assistant_text=f"answer for: {task}",
        error=error,
    )


@pytest.fixture
def stub_runner(monkeypatch, tmp_path: Path):
    """Patch athena.headless.run_headless (the actual function
    the CLI imports) to a deterministic stub.  Tests just call
    the CLI's main() with argv."""
    import athena.headless as headless_pkg
    import athena.headless.runner as runner_mod

    def _stub(
        task,
        *,
        cfg,
        workspace,
        model=None,
        run_id=None,
        timeout_s=None,
        on_info=None,
        agent=None,
        _agent_factory=None,
    ):
        return _build_run_result(
            task=task,
            run_id=run_id or "r-auto",
            workspace=workspace,
        )

    monkeypatch.setattr(runner_mod, "run_headless", _stub)
    monkeypatch.setattr(headless_pkg, "run_headless", _stub)
    # Also patch the CLI's direct import of run_headless
    import athena.cli.batch as batch_cli

    monkeypatch.setattr(batch_cli, "_run_one", _wrap_run_one(_stub, batch_cli))

    # Make config + profile_dir predictable.
    monkeypatch.setattr(
        batch_cli,
        "profile_dir",
        lambda profile="default": tmp_path,
    )
    return _stub


def _wrap_run_one(stub, batch_cli):
    """The CLI's _run_one imports run_headless internally; we
    wrap it to use our stub so the batch CLI path doesn't
    accidentally hit the real one."""
    from athena.batch.manifest import ManifestEntry
    from athena.batch.runner import _safe_filename

    def _run_one(*, entry, cfg, workspace_default, output_dir, force):
        envelope_path = output_dir / f"{_safe_filename(entry.run_id)}.json"
        if envelope_path.exists() and not force:
            existing = json.loads(envelope_path.read_text())
            return ManifestEntry.from_run_result(
                envelope=existing,
                envelope_path=envelope_path,
            ), existing
        from pathlib import Path as _P

        workspace = _P(entry.cwd).expanduser().resolve() if entry.cwd else workspace_default
        result = stub(
            entry.task,
            cfg=cfg,
            workspace=workspace,
            model=entry.model,
            run_id=entry.run_id,
            timeout_s=entry.timeout_s,
        )
        envelope = result.to_dict()
        envelope_path.write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return ManifestEntry.from_run_result(
            envelope=envelope,
            envelope_path=envelope_path,
        ), envelope

    return _run_one


def _run_cli(argv: list[str], capsys) -> tuple[int, str, str]:
    """Call the batch CLI's main(argv) directly. Capture
    stdout + stderr."""
    from athena.cli.batch import main

    exit_code = main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _write_tasks(tmp_path: Path, lines: list[dict]) -> Path:
    f = tmp_path / "tasks.jsonl"
    f.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------


def test_missing_tasks_file_exits_2(stub_runner, capsys, tmp_path: Path):
    code, _out, err = _run_cli([str(tmp_path / "nope.jsonl")], capsys)
    assert code == 2
    assert "not found" in err


def test_bad_json_in_tasks_exits_2(stub_runner, capsys, tmp_path: Path):
    f = tmp_path / "tasks.jsonl"
    f.write_text("not json\n", encoding="utf-8")
    code, _out, err = _run_cli([str(f)], capsys)
    assert code == 2
    assert "not valid JSON" in err


def test_empty_tasks_file_exits_0(stub_runner, capsys, tmp_path: Path):
    f = tmp_path / "tasks.jsonl"
    f.write_text("", encoding="utf-8")
    code, _out, err = _run_cli([str(f), "-C", str(tmp_path)], capsys)
    assert code == 0
    assert "no entries" in err


# ---------------------------------------------------------------
# Serial path — happy path
# ---------------------------------------------------------------


def test_serial_run_writes_envelopes_and_manifest(stub_runner, capsys, tmp_path: Path):
    out_dir = tmp_path / "out"
    tasks_file = _write_tasks(
        tmp_path,
        [
            {"task": "first", "run_id": "r-001"},
            {"task": "second", "run_id": "r-002"},
        ],
    )
    code, _stdout, _stderr = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--quiet"],
        capsys,
    )
    assert code == 0
    # Per-run envelopes on disk.
    assert (out_dir / "r-001.json").exists()
    assert (out_dir / "r-002.json").exists()
    # Manifest written.
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["total"] == 2
    assert manifest["by_status"] == {"ok": 2}


def test_default_output_dir_under_profile(stub_runner, capsys, tmp_path: Path):
    tasks_file = _write_tasks(tmp_path, [{"task": "x", "run_id": "r-default"}])
    code, _out, _err = _run_cli(
        [str(tasks_file), "-C", str(tmp_path), "--quiet"],
        capsys,
    )
    assert code == 0
    # Default output dir is <profile_dir>/batch/<batch_id>/ —
    # we patched profile_dir to tmp_path. The batch_id is
    # auto-minted; find the directory under tmp_path/batch/.
    batches = list((tmp_path / "batch").iterdir())
    assert len(batches) == 1
    assert (batches[0] / "manifest.json").exists()
    assert (batches[0] / "r-default.json").exists()


# ---------------------------------------------------------------
# --json mode
# ---------------------------------------------------------------


def test_json_mode_writes_manifest_to_stdout(stub_runner, capsys, tmp_path: Path):
    out_dir = tmp_path / "out"
    tasks_file = _write_tasks(tmp_path, [{"task": "x", "run_id": "r-001"}])
    code, out, _err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--json", "--quiet"],
        capsys,
    )
    assert code == 0
    # Single line on stdout, parseable.
    body = out.rstrip("\n")
    assert "\n" not in body
    payload = json.loads(body)
    assert payload["total"] == 1
    assert payload["by_status"]["ok"] == 1


# ---------------------------------------------------------------
# --force / resume-safety
# ---------------------------------------------------------------


def test_resume_skips_existing(stub_runner, capsys, tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "r-existing.json").write_text(
        json.dumps(
            {
                "run_id": "r-existing",
                "status": "ok",
                "exit_code": 0,
                "duration_s": 0.0,
                "task": "old-task",
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    tasks_file = _write_tasks(
        tmp_path,
        [
            {"task": "new-task", "run_id": "r-existing"},
        ],
    )
    code, _out, _err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--quiet"],
        capsys,
    )
    assert code == 0
    # Envelope NOT overwritten — resume-safe.
    env = json.loads((out_dir / "r-existing.json").read_text())
    assert env["task"] == "old-task"


def test_force_reruns_existing(stub_runner, capsys, tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "r-existing.json").write_text(
        json.dumps(
            {
                "run_id": "r-existing",
                "status": "ok",
                "exit_code": 0,
                "duration_s": 0.0,
                "task": "old-task",
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    tasks_file = _write_tasks(
        tmp_path,
        [
            {"task": "new-task", "run_id": "r-existing"},
        ],
    )
    code, _out, _err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--force", "--quiet"],
        capsys,
    )
    assert code == 0
    # Envelope overwritten.
    env = json.loads((out_dir / "r-existing.json").read_text())
    assert env["task"] == "new-task"


# ---------------------------------------------------------------
# Exit code per status
# ---------------------------------------------------------------


def test_exit_1_when_any_entry_failed(
    monkeypatch,
    capsys,
    tmp_path: Path,
):
    """An entry that returns status=error → batch exit code 1
    (so a CI runner gates on it)."""
    import athena.cli.batch as batch_cli
    import athena.headless as headless_pkg
    import athena.headless.runner as runner_mod

    def _failing_stub(
        task,
        *,
        cfg,
        workspace,
        model=None,
        run_id=None,
        timeout_s=None,
        on_info=None,
        agent=None,
        _agent_factory=None,
    ):
        return _build_run_result(
            task=task,
            run_id=run_id or "r-auto",
            workspace=workspace,
            status="error",
            error="boom",
        )

    # Patch BOTH locations: runner_mod (the function), and the
    # package-level re-export that batch_run imports via
    # `from ..headless import run_headless`.
    monkeypatch.setattr(runner_mod, "run_headless", _failing_stub)
    monkeypatch.setattr(headless_pkg, "run_headless", _failing_stub)
    monkeypatch.setattr(batch_cli, "_run_one", _wrap_run_one(_failing_stub, batch_cli))
    monkeypatch.setattr(batch_cli, "profile_dir", lambda profile="default": tmp_path)

    out_dir = tmp_path / "out"
    tasks_file = _write_tasks(tmp_path, [{"task": "x", "run_id": "r-001"}])
    code, _out, _err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--quiet"],
        capsys,
    )
    assert code == 1


def test_exit_0_when_all_skipped(stub_runner, capsys, tmp_path: Path):
    """An entirely-resume batch (every envelope pre-existing)
    counts as ok — exit 0."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    for rid in ("r-001", "r-002"):
        (out_dir / f"{rid}.json").write_text(
            json.dumps(
                {
                    "run_id": rid,
                    "status": "ok",
                    "exit_code": 0,
                    "duration_s": 0.0,
                    "task": "done",
                    "error": None,
                }
            ),
            encoding="utf-8",
        )
    tasks_file = _write_tasks(
        tmp_path,
        [
            {"task": "x", "run_id": "r-001"},
            {"task": "y", "run_id": "r-002"},
        ],
    )
    code, _out, _err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--quiet"],
        capsys,
    )
    assert code == 0


# ---------------------------------------------------------------
# Parallel path
# ---------------------------------------------------------------


def test_parallel_runs_complete_in_input_order(
    stub_runner,
    capsys,
    tmp_path: Path,
):
    """With --parallel 4 over 10 entries, the manifest's
    entries list still reflects the input JSONL line ordering
    (workers may complete in any order, but the manifest is
    re-sorted to input order before write)."""
    out_dir = tmp_path / "out"
    entries = [{"task": f"task-{i}", "run_id": f"r-{i:03d}"} for i in range(10)]
    tasks_file = _write_tasks(tmp_path, entries)
    code, _out, _err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--parallel", "4", "--quiet"],
        capsys,
    )
    assert code == 0
    manifest = json.loads((out_dir / "manifest.json").read_text())
    run_ids_in_manifest = [e["run_id"] for e in manifest["entries"]]
    assert run_ids_in_manifest == [f"r-{i:03d}" for i in range(10)]
    assert manifest["by_status"]["ok"] == 10


def test_parallel_writes_all_envelopes(stub_runner, capsys, tmp_path: Path):
    out_dir = tmp_path / "out"
    entries = [{"task": f"t{i}", "run_id": f"r-{i:03d}"} for i in range(5)]
    tasks_file = _write_tasks(tmp_path, entries)
    code, _out, _err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--parallel", "2", "--quiet"],
        capsys,
    )
    assert code == 0
    for i in range(5):
        assert (out_dir / f"r-{i:03d}.json").exists()


# ---------------------------------------------------------------
# Stderr progress lines
# ---------------------------------------------------------------


def test_progress_lines_emitted_to_stderr_by_default(
    stub_runner,
    capsys,
    tmp_path: Path,
):
    out_dir = tmp_path / "out"
    tasks_file = _write_tasks(
        tmp_path,
        [
            {"task": "t1", "run_id": "r-001"},
            {"task": "t2", "run_id": "r-002"},
        ],
    )
    code, out, err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path)],
        capsys,
    )
    assert code == 0
    # One progress line per entry + final summary.
    assert "[   1/2]" in err
    assert "[   2/2]" in err
    # Run IDs in stderr.
    assert "r-001" in err
    assert "r-002" in err
    # Stdout is empty (no --json).
    assert out == ""


def test_quiet_suppresses_progress(stub_runner, capsys, tmp_path: Path):
    out_dir = tmp_path / "out"
    tasks_file = _write_tasks(
        tmp_path,
        [
            {"task": "t1", "run_id": "r-001"},
        ],
    )
    code, _out, err = _run_cli(
        [str(tasks_file), "-o", str(out_dir), "-C", str(tmp_path), "--quiet"],
        capsys,
    )
    assert code == 0
    # No per-entry "[N/M]" line on stderr in quiet mode.
    assert "[   1/" not in err
