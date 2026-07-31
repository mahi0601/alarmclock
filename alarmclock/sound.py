"""Best-effort audible alert.

Terminals and CI/headless boxes vary wildly in what audio they have, so this
tries a native platform sound first and falls back to the ASCII bell
character, which every terminal emulator understands even if the bell is
disabled or the machine has no speaker at all.
"""
from __future__ import annotations

import subprocess
import sys
import threading

_MAC_SOUND = "/System/Library/Sounds/Glass.aiff"
_LINUX_SOUND_CANDIDATES = (
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/alsa/Front_Center.wav",
)


def _bell() -> None:
    sys.stdout.write("\a")
    sys.stdout.flush()


def _play_macos_sound() -> bool:
    try:
        subprocess.run(["afplay", _MAC_SOUND], check=True, capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _play_linux_sound() -> bool:
    for candidate in _LINUX_SOUND_CANDIDATES:
        try:
            subprocess.run(["paplay", candidate], check=True, capture_output=True, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def _play_windows_beep() -> bool:
    try:
        import winsound

        winsound.Beep(1000, 400)
        return True
    except (RuntimeError, OSError):
        return False


def ring_once() -> None:
    """Play one alert sound, falling back to the terminal bell on any failure."""
    if sys.platform == "darwin":
        played = _play_macos_sound()
    elif sys.platform.startswith("linux"):
        played = _play_linux_sound()
    elif sys.platform == "win32":
        played = _play_windows_beep()
    else:
        played = False

    if not played:
        _bell()


def ring_until(stop_event: threading.Event, interval_seconds: float = 1.5) -> None:
    """Ring repeatedly until `stop_event` is set. Meant to run in a background thread."""
    while not stop_event.is_set():
        ring_once()
        stop_event.wait(interval_seconds)
