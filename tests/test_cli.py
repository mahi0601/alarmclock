import pytest

from alarmclock import storage
from alarmclock.cli import main


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARMCLOCK_HOME", str(tmp_path))
    yield tmp_path


def test_add_valid_time(capsys):
    assert main(["add", "--time", "07:00"]) == 0
    out = capsys.readouterr().out
    assert "Added alarm" in out
    assert len(storage.load()) == 1


def test_add_invalid_time_exits_nonzero(capsys):
    assert main(["add", "--time", "not-a-time"]) == 2
    assert "error" in capsys.readouterr().err
    assert storage.load() == []


def test_add_relative_and_repeat_rejected(capsys):
    assert main(["add", "--time", "+10m", "--repeat", "daily"]) == 2
    assert storage.load() == []


def test_add_relative_over_24h_rejected(capsys):
    assert main(["add", "--time", "+25h"]) == 2
    assert storage.load() == []


def test_add_relative_under_24h_accepted():
    assert main(["add", "--time", "+10m"]) == 0
    assert len(storage.load()) == 1


def test_list_when_empty(capsys):
    assert main(["list"]) == 0
    assert "No alarms set." in capsys.readouterr().out


def test_list_shows_added_alarm(capsys):
    main(["add", "--time", "07:00", "--label", "wake up", "--repeat", "weekdays"])
    capsys.readouterr()  # discard the add command's own output
    main(["list"])
    out = capsys.readouterr().out
    assert "07:00" in out
    assert "wake up" in out
    assert "mon,tue,wed,thu,fri" in out


def test_remove(capsys):
    main(["add", "--time", "07:00"])
    alarm_id = storage.load()[0].id
    assert main(["remove", alarm_id]) == 0
    assert storage.load() == []


def test_remove_missing_exits_nonzero(capsys):
    assert main(["remove", "ghost"]) == 1


def test_disable_and_enable(capsys):
    main(["add", "--time", "07:00"])
    alarm_id = storage.load()[0].id
    assert main(["disable", alarm_id]) == 0
    assert storage.load()[0].enabled is False
    assert main(["enable", alarm_id]) == 0
    assert storage.load()[0].enabled is True


def test_no_command_exits_nonzero():
    with pytest.raises(SystemExit):
        main([])
