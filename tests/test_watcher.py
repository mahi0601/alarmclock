import multiprocessing
import os
import time
from datetime import datetime, timedelta

import pytest

from alarmclock import storage, watcher
from alarmclock.models import Alarm
from alarmclock.watcher import _fire_due_alarms, _sync_schedule

WORKER_COUNT = 8


def _race_for_lock(home: str, barrier, result_queue) -> None:
    os.environ["ALARMCLOCK_HOME"] = home
    from alarmclock import watcher as w

    barrier.wait()  # line everyone up so they hit acquire_run_lock() together
    try:
        w.acquire_run_lock()
        result_queue.put(("acquired", os.getpid()))
        time.sleep(0.3)  # hold it like a real long-running `run` process would
    except w.AlreadyRunningError:
        result_queue.put(("refused", os.getpid()))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARMCLOCK_HOME", str(tmp_path))
    yield tmp_path


def _fake_clock(monkeypatch, ticks):
    """Replace watcher.datetime.now() with a fixed sequence of naive local times.

    Regression test for a real bug: the watcher used to call
    next_occurrence(..., now) fresh every tick and compare its result against
    that same `now`. Since next_occurrence always returns a time strictly
    after the `now` it's given, `now >= due` could never be true and no
    alarm ever fired. Driving the clock through a fixed list of instants and
    asserting a fire actually happens is what catches that class of bug.
    """
    remaining = iter(ticks)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(remaining)

    monkeypatch.setattr(watcher, "datetime", FakeDateTime)
    monkeypatch.setattr(watcher.time_mod, "sleep", lambda s: None)


def test_one_off_alarm_fires_once_wall_clock_reaches_it_then_disables(monkeypatch):
    base = datetime(2026, 1, 1, 7, 0, 0)
    ticks = [base + timedelta(seconds=s) for s in range(0, 20, 2)]
    _fake_clock(monkeypatch, ticks)

    alarm = storage.add_alarm(Alarm(time="07:00:05"))

    fired = []
    monkeypatch.setattr(watcher, "_fire", lambda a: fired.append(a.id))

    with pytest.raises(StopIteration):
        watcher.run_forever()

    assert fired == [alarm.id]
    assert storage.load()[0].enabled is False


def test_recurring_alarm_reschedules_instead_of_disabling(monkeypatch):
    base = datetime(2026, 7, 31, 6, 59, 58)  # a Friday
    ticks = [base + timedelta(seconds=s) for s in range(0, 20, 2)]
    _fake_clock(monkeypatch, ticks)

    alarm = storage.add_alarm(Alarm(time="07:00:00", repeat=["fri"]))

    fired = []
    monkeypatch.setattr(watcher, "_fire", lambda a: fired.append(a.id))

    with pytest.raises(StopIteration):
        watcher.run_forever()

    assert fired == [alarm.id]
    reloaded = storage.load()[0]
    assert reloaded.enabled is True  # recurring alarms stay enabled after firing


def test_acquire_run_lock_writes_own_pid(tmp_path):
    watcher.acquire_run_lock()
    assert storage.pid_lock_path().read_text().strip() == str(os.getpid())


def test_acquire_run_lock_refuses_when_another_pid_is_alive(monkeypatch):
    other_pid = os.getpid() + 1  # must differ from our own pid to exercise this branch
    storage.pid_lock_path().write_text(str(other_pid))
    monkeypatch.setattr(watcher, "_pid_alive", lambda pid: True)
    with pytest.raises(watcher.AlreadyRunningError):
        watcher.acquire_run_lock()


def test_acquire_run_lock_takes_over_a_stale_lock(monkeypatch):
    storage.pid_lock_path().write_text("999999")  # a pid that isn't running
    monkeypatch.setattr(watcher, "_pid_alive", lambda pid: False)
    watcher.acquire_run_lock()  # must not raise
    assert storage.pid_lock_path().read_text().strip() == str(os.getpid())


def test_acquire_run_lock_serializes_across_real_concurrent_processes(tmp_path):
    """Regression test: a plain exists-check-then-write let two `run`
    processes launched at nearly the same instant both believe they held
    the lock (reproduced directly: 8 processes racing, up to 7 of 8 wrongly
    "acquired" it). Spawns real separate OS processes, not threads, to prove
    exactly one of them ever wins.
    """
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(WORKER_COUNT)
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(target=_race_for_lock, args=(str(tmp_path), barrier, result_queue))
        for _ in range(WORKER_COUNT)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    results = []
    while not result_queue.empty():
        results.append(result_queue.get())

    acquired = [r for r in results if r[0] == "acquired"]
    refused = [r for r in results if r[0] == "refused"]
    assert len(acquired) == 1
    assert len(refused) == WORKER_COUNT - 1


def test_disable_then_reenable_during_a_snooze_drops_the_stale_snooze(monkeypatch):
    """Regression test: disabling a snoozed alarm didn't clear its snooze entry.

    Re-enabling it later would then fire it off the old, stale snooze time
    instead of the freshly recomputed schedule - a one-off alarm snoozed at
    10:00, disabled, and re-enabled a moment later could still go off at the
    original 10:10 snooze time even though it should now be scheduled for
    tomorrow.
    """
    fired = []
    monkeypatch.setattr(watcher, "_fire", lambda a: fired.append(a.id))

    alarm = Alarm(time="10:00:00")
    now = datetime(2026, 8, 1, 10, 0, 5)
    schedule, snoozes, known_ids = {}, {}, set()

    _sync_schedule([alarm], now, schedule, snoozes, known_ids)
    snoozes[alarm.id] = now + timedelta(minutes=10)  # simulate a snooze in progress

    alarm.enabled = False
    _sync_schedule([alarm], now + timedelta(seconds=5), schedule, snoozes, known_ids)
    assert snoozes == {}  # disabling must drop the snooze, not just the schedule entry

    alarm.enabled = True
    _sync_schedule([alarm], now + timedelta(seconds=10), schedule, snoozes, known_ids)

    at_old_snooze_time = now + timedelta(minutes=10, seconds=1)
    _fire_due_alarms([alarm], at_old_snooze_time, schedule, snoozes)
    assert fired == []  # must not fire off the stale snooze time


def test_stale_one_off_from_before_startup_is_disabled_without_firing(monkeypatch):
    # The alarm's time is 5 minutes before the watcher's very first tick -
    # simulates the process having been off when it was due.
    base = datetime(2026, 1, 1, 7, 5, 0)
    ticks = [base + timedelta(seconds=s) for s in range(0, 10, 2)]
    _fake_clock(monkeypatch, ticks)

    storage.add_alarm(Alarm(time="07:00:00"))

    fired = []
    monkeypatch.setattr(watcher, "_fire", lambda a: fired.append(a.id))

    with pytest.raises(StopIteration):
        watcher.run_forever()

    assert fired == []
    assert storage.load()[0].enabled is False
