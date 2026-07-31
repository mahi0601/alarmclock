import types

import pytest

from alarmclock import storage
from alarmclock.models import Alarm


def _fake_msvcrt(calls):
    return types.SimpleNamespace(
        LK_LOCK="LOCK",
        LK_UNLCK="UNLOCK",
        locking=lambda fd, mode, nbytes: calls.append((mode, nbytes)),
    )


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARMCLOCK_HOME", str(tmp_path))
    yield tmp_path


def test_load_on_missing_file_returns_empty_list():
    assert storage.load() == []


def test_add_then_load_round_trips():
    alarm = Alarm(time="07:00:00", label="wake up", repeat=["mon", "wed"])
    storage.add_alarm(alarm)

    loaded = storage.load()
    assert len(loaded) == 1
    assert loaded[0].id == alarm.id
    assert loaded[0].time == "07:00:00"
    assert loaded[0].label == "wake up"
    assert loaded[0].repeat == ["mon", "wed"]
    assert loaded[0].enabled is True


def test_remove_existing_returns_true(isolated_home):
    alarm = storage.add_alarm(Alarm(time="07:00:00"))
    assert storage.remove_alarm(alarm.id) is True
    assert storage.load() == []


def test_remove_missing_returns_false():
    assert storage.remove_alarm("doesnotexist") is False


def test_set_enabled_toggles_flag():
    alarm = storage.add_alarm(Alarm(time="07:00:00"))
    assert storage.set_enabled(alarm.id, False) is True
    assert storage.load()[0].enabled is False
    assert storage.set_enabled(alarm.id, True) is True
    assert storage.load()[0].enabled is True


def test_set_enabled_missing_returns_false():
    assert storage.set_enabled("nope", False) is False


def test_corrupted_file_raises_clear_error(isolated_home):
    (isolated_home / "alarms.json").write_text("{not valid json")
    with pytest.raises(RuntimeError, match="corrupted"):
        storage.load()


def test_save_is_atomic_no_partial_file_left_on_success(isolated_home):
    storage.add_alarm(Alarm(time="07:00:00"))
    tmp_leftovers = list(isolated_home.glob(".alarms-*.tmp"))
    assert tmp_leftovers == []


def test_multiple_alarms_get_distinct_ids():
    a1 = storage.add_alarm(Alarm(time="07:00:00"))
    a2 = storage.add_alarm(Alarm(time="08:00:00"))
    assert a1.id != a2.id
    assert {a.id for a in storage.load()} == {a1.id, a2.id}


# The following exercise the msvcrt (Windows) locking path by mocking the
# module - there's no real Windows machine in this environment, so this
# verifies the code calls the documented msvcrt API correctly, not that
# Windows actually enforces the lock the way fcntl does on POSIX.


def test_msvcrt_lock_region_writes_placeholder_byte_when_file_empty(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "msvcrt", _fake_msvcrt(calls))

    lock_path = tmp_path / "test.lock"
    with open(lock_path, "a+") as handle:
        storage._msvcrt_lock_region(handle)
        handle.seek(0)
        assert handle.read() == "0"
        storage._msvcrt_unlock_region(handle)

    assert calls == [("LOCK", 1), ("UNLOCK", 1)]


def test_msvcrt_lock_region_leaves_existing_content_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "msvcrt", _fake_msvcrt([]))

    lock_path = tmp_path / "test.lock"
    lock_path.write_text("x")
    with open(lock_path, "r+") as handle:
        storage._msvcrt_lock_region(handle)
        handle.seek(0)
        assert handle.read(1) == "x"


def test_locked_uses_msvcrt_when_fcntl_unavailable(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "fcntl", None)
    monkeypatch.setattr(storage, "msvcrt", _fake_msvcrt(calls))

    with storage._locked(tmp_path / "alarms.json"):
        pass

    assert calls == [("LOCK", 1), ("UNLOCK", 1)]
