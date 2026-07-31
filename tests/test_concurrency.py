"""Exercises storage.py's file locking under real concurrent processes.

test_storage.py only checks that a single write is atomic (temp file +
rename). It doesn't prove the advisory lock actually serializes concurrent
read-modify-write cycles - that requires genuine contention from separate
OS processes, which is what this file adds. Uses multiprocessing with the
'spawn' start method so each worker is a real independent Python
interpreter, the same as running `alarmclock add` from separate terminals.
"""
import multiprocessing
import os

import pytest

from alarmclock import storage
from alarmclock.models import Alarm

WORKERS = 4
ALARMS_PER_WORKER = 8


def _add_alarms(home: str, count: int) -> None:
    os.environ["ALARMCLOCK_HOME"] = home
    for _ in range(count):
        storage.add_alarm(Alarm(time="07:00:00"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARMCLOCK_HOME", str(tmp_path))
    yield tmp_path


def test_concurrent_adds_from_separate_processes_dont_lose_or_corrupt_alarms(tmp_path):
    home = str(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    processes = [
        ctx.Process(target=_add_alarms, args=(home, ALARMS_PER_WORKER)) for _ in range(WORKERS)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    alarms = storage.load()  # raises RuntimeError if the file got corrupted
    expected_total = WORKERS * ALARMS_PER_WORKER
    assert len(alarms) == expected_total
    assert len({alarm.id for alarm in alarms}) == expected_total  # no id collisions

    leftover_temp_files = list(tmp_path.glob(".alarms-*.tmp"))
    assert leftover_temp_files == []
