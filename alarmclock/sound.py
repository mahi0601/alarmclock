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


def ring_once() -> None:
    """Play one alert sound, falling back to the terminal bell on any failure."""
    if sys.platform == "darwin":
        try:
            subprocess.run(["afplay", _MAC_SOUND], check=True, capture_output=True, timeout=5)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform.startswith("linux"):
        for candidate in _LINUX_SOUND_CANDIDATES:
            try:
                subprocess.run(["paplay", candidate], check=True, capture_output=True, timeout=5)
                return
            except (OSError, subprocess.SubprocessError):
                continue
    elif sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(1000, 400)
            return
        except (RuntimeError, OSError):
            pass
    _bell()


def ring_until(stop_event: threading.Event, interval_seconds: float = 1.5) -> None:
    """Ring repeatedly until `stop_event` is set. Meant to run in a background thread."""
    while not stop_event.is_set():
        ring_once()
        stop_event.wait(interval_seconds)
