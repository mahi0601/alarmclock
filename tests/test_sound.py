import sys
import types

from alarmclock import sound


class _FakeEvent:
    """Enough of threading.Event's interface for ring_until, without a real clock."""

    def __init__(self, stop_after_calls):
        self._calls = 0
        self._stop_after_calls = stop_after_calls

    def is_set(self):
        return self._calls >= self._stop_after_calls

    def wait(self, timeout):
        self._calls += 1


def test_ring_until_rings_until_event_is_set(monkeypatch):
    rings = []
    monkeypatch.setattr(sound, "ring_once", lambda: rings.append(1))
    sound.ring_until(_FakeEvent(stop_after_calls=3), interval_seconds=0)
    assert len(rings) == 3


def test_play_macos_sound_true_on_success(monkeypatch):
    monkeypatch.setattr(sound.subprocess, "run", lambda *a, **k: None)
    assert sound._play_macos_sound() is True


def test_play_macos_sound_false_when_afplay_missing(monkeypatch):
    def boom(*a, **k):
        raise OSError("no afplay")

    monkeypatch.setattr(sound.subprocess, "run", boom)
    assert sound._play_macos_sound() is False


def test_play_linux_sound_falls_through_to_next_candidate(monkeypatch):
    tried = []

    def fake_run(cmd, **kwargs):
        tried.append(cmd[1])
        if cmd[1] == sound._LINUX_SOUND_CANDIDATES[0]:
            raise OSError("missing sound file")

    monkeypatch.setattr(sound.subprocess, "run", fake_run)
    assert sound._play_linux_sound() is True
    assert tried == list(sound._LINUX_SOUND_CANDIDATES[:2])


def test_play_linux_sound_false_when_no_candidate_works(monkeypatch):
    def boom(*a, **k):
        raise OSError("missing sound file")

    monkeypatch.setattr(sound.subprocess, "run", boom)
    assert sound._play_linux_sound() is False


def test_play_windows_beep_true_on_success(monkeypatch):
    fake_winsound = types.SimpleNamespace(Beep=lambda freq, duration: None)
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)
    assert sound._play_windows_beep() is True


def test_play_windows_beep_false_when_no_driver(monkeypatch):
    def boom(freq, duration):
        raise RuntimeError("no sound driver")

    fake_winsound = types.SimpleNamespace(Beep=boom)
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)
    assert sound._play_windows_beep() is False


def test_ring_once_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(sound.sys, "platform", "darwin")
    monkeypatch.setattr(sound, "_play_macos_sound", lambda: True)
    bell_calls = []
    monkeypatch.setattr(sound, "_bell", lambda: bell_calls.append(1))

    sound.ring_once()

    assert bell_calls == []  # played successfully, no need for the fallback


def test_ring_once_falls_back_to_bell_when_player_fails(monkeypatch):
    monkeypatch.setattr(sound.sys, "platform", "linux")
    monkeypatch.setattr(sound, "_play_linux_sound", lambda: False)
    bell_calls = []
    monkeypatch.setattr(sound, "_bell", lambda: bell_calls.append(1))

    sound.ring_once()

    assert bell_calls == [1]


def test_ring_once_falls_back_to_bell_on_unrecognized_platform(monkeypatch):
    monkeypatch.setattr(sound.sys, "platform", "freebsd13")
    bell_calls = []
    monkeypatch.setattr(sound, "_bell", lambda: bell_calls.append(1))

    sound.ring_once()

    assert bell_calls == [1]
