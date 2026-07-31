from datetime import datetime, timedelta

import pytest

from alarmclock import storage, watcher
from alarmclock.models import Alarm


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
