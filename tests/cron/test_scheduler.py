"""CronScheduler: start/stop, add/remove, persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from athena.cron.jobs import CronJob
from athena.cron.scheduler import CronScheduler


@pytest.fixture
def scheduler(tmp_path: Path):
    s = CronScheduler(
        db_path=tmp_path / "scheduler.db",
        jobs_db_path=tmp_path / "cron_jobs.db",
    )
    yield s
    s.stop()


def test_start_and_stop(scheduler: CronScheduler):
    scheduler.start()
    assert scheduler._started is True
    scheduler.stop()
    assert scheduler._started is False


def test_start_is_idempotent(scheduler: CronScheduler):
    scheduler.start()
    scheduler.start()  # no exception
    assert scheduler._started is True


def test_paused_start_does_not_fire_a_due_job(tmp_path: Path, monkeypatch):
    """Regression: short-lived CLI commands open a scheduler just to
    read next_run_time. An unpaused start fires any job inside the 600s
    misfire grace window (e.g. an agent-mode job → a full LLM turn)
    right there in the CLI process. A paused start must register the
    trigger (so next_run_time works) but never execute a DUE job.

    This test has teeth: it forces the job's next_run_time into the past
    (a genuine misfire) and proves (a) paused doesn't run it, then (b)
    resuming DOES — so the paused assertion isn't vacuous."""
    import time
    from datetime import datetime, timedelta, timezone

    jobs_db = tmp_path / "j.db"
    # The watchdog runner (the APScheduler target) re-resolves its store
    # via _profile_cron_paths(); point it at this test's isolated DB so
    # the resumed run actually finds the job and writes the marker.
    monkeypatch.setattr(
        "athena.cli.cron._profile_cron_paths",
        lambda: (tmp_path / "s.db", jobs_db),
    )

    marker = tmp_path / "fired.txt"
    s = CronScheduler(db_path=tmp_path / "s.db", jobs_db_path=jobs_db)
    # A watchdog job whose script writes the marker when it runs.
    job = CronJob(
        cron_expr="* * * * *",
        mode="watchdog",
        script=f"python -c \"open(r'{marker}','w').write('x')\"",
    )
    try:
        s.start(paused=True)
        s.add_job(job)
        assert s.next_run_time(job.id) is not None  # trigger registered
        # Force a genuine misfire: next_run_time 1s in the past, well
        # within the 600s grace window. An unpaused scheduler would run
        # this on its next wakeup.
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        s._sched.modify_job(job.id, next_run_time=past)

        # Paused: the due job must NOT fire.
        time.sleep(1.0)
        assert not marker.exists(), "paused scheduler executed a due job"

        # Teeth: prove the job was genuinely due — resuming fires it.
        s._sched.resume()
        deadline = time.monotonic() + 5.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert marker.exists(), "resumed scheduler should run the due job"
    finally:
        s.stop()


def test_add_and_remove_job(scheduler: CronScheduler):
    scheduler.start()
    job = CronJob(cron_expr="* * * * *", mode="agent", prompt="hi")
    scheduler.add_job(job)
    listed_ids = {j.id for j in scheduler.list_jobs()}
    assert job.id in listed_ids
    assert scheduler.remove_job(job.id) is True
    assert all(j.id != job.id for j in scheduler.list_jobs())


def test_remove_nonexistent_returns_false(scheduler: CronScheduler):
    scheduler.start()
    assert scheduler.remove_job("never-existed") is False


def test_add_job_stores_metadata_when_not_started(tmp_path: Path):
    """Adding a job to a stopped scheduler still persists it for later."""
    scheduler = CronScheduler(
        db_path=tmp_path / "scheduler.db",
        jobs_db_path=tmp_path / "cron_jobs.db",
    )
    job = CronJob(cron_expr="* * * * *", mode="agent", prompt="x")
    scheduler.add_job(job)
    # Job persisted even though scheduler never started.
    assert scheduler.get_job(job.id) is not None


def test_lists_jobs(scheduler: CronScheduler):
    scheduler.start()
    job1 = CronJob(cron_expr="* * * * *", mode="agent", prompt="a")
    job2 = CronJob(cron_expr="0 0 * * *", mode="watchdog", script="echo b")
    scheduler.add_job(job1)
    scheduler.add_job(job2)
    listed = scheduler.list_jobs()
    assert len(listed) == 2
    assert {j.id for j in listed} == {job1.id, job2.id}


def test_persists_across_restart(tmp_path: Path):
    db_path = tmp_path / "scheduler.db"
    jobs_db = tmp_path / "cron_jobs.db"
    s1 = CronScheduler(db_path=db_path, jobs_db_path=jobs_db)
    s1.start()
    job = CronJob(cron_expr="0 9 * * *", mode="agent", prompt="hello")
    s1.add_job(job)
    s1.stop()

    s2 = CronScheduler(db_path=db_path, jobs_db_path=jobs_db)
    s2.start()
    try:
        assert s2.get_job(job.id) is not None
        # APScheduler also re-registered it.
        assert s2.next_run_time(job.id) is not None
    finally:
        s2.stop()


def test_disable_removes_from_scheduler_but_keeps_metadata(scheduler: CronScheduler):
    scheduler.start()
    job = CronJob(cron_expr="* * * * *", mode="agent", prompt="x")
    scheduler.add_job(job)
    assert scheduler.disable(job.id) is True
    # Metadata still there, just disabled.
    fetched = scheduler.get_job(job.id)
    assert fetched is not None
    assert fetched.enabled is False
    # Re-enable rewires it.
    assert scheduler.enable(job.id) is True
    assert scheduler.get_job(job.id).enabled is True


def test_disable_nonexistent_returns_false(scheduler: CronScheduler):
    scheduler.start()
    assert scheduler.disable("nope") is False
    assert scheduler.enable("nope") is False


def test_invalid_cron_expression_rejected(scheduler: CronScheduler):
    scheduler.start()
    bad = CronJob(
        cron_expr="not a cron expression",
        mode="agent",
        prompt="x",
    )
    with pytest.raises(Exception):
        scheduler.add_job(bad)
