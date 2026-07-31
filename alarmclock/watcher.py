"""The `alarmclock run` loop: watches the alarms file and fires alarms as they come due.

Design notes (see DESIGN.md for the full reasoning):
- Polls in short slices rather than sleeping until the next alarm, so it
  picks up alarms added from another terminal and responds to Ctrl+C promptly.
- Re-reads "now" on every tick instead of doing interval arithmetic, so DST
  transitions during a long wait are handled for free.
- A PID lock file stops two `run` processes from double-firing alarms.
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
import time as time_mod
from datetime import datetime, timedelta

from . import sound, storage
from .models import Alarm
from .timeparse import parse_clock_time, next_occurrence

POLL_INTERVAL_SECONDS = 2
MISSED_THRESHOLD = timedelta(seconds=60)
DEFAULT_SNOOZE = timedelta(minutes=9)
NON_INTERACTIVE_RING_SECONDS = 15


class AlreadyRunningError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    except OSError:
        return False
    return True


def acquire_run_lock() -> None:
    lock_path = storage.pid_lock_path()
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
        except (ValueError, OSError):
            existing_pid = None
        if existing_pid and existing_pid != os.getpid() and _pid_alive(existing_pid):
            raise AlreadyRunningError(
                f"alarmclock run is already active (pid {existing_pid}). "
                "Stop it before starting another, or delete "
                f"{lock_path} if you're sure it's stale."
            )
    lock_path.write_text(str(os.getpid()))
    atexit.register(lambda: lock_path.unlink(missing_ok=True))


def _disable_missed_one_offs(alarms: list[Alarm], now: datetime) -> bool:
    """A one-off alarm the process wasn't running to catch is disabled, not fired late."""
    changed = False
    for alarm in alarms:
        if not alarm.enabled or alarm.repeat:
            continue
        candidate_today = datetime.combine(now.date(), parse_clock_time(alarm.time), tzinfo=now.tzinfo)
        if now - candidate_today > MISSED_THRESHOLD:
            alarm.enabled = False
            changed = True
            label = f" ({alarm.label})" if alarm.label else ""
            print(
                f"[missed] '{alarm.time}'{label} was due while alarmclock wasn't running; "
                "disabling it rather than firing it late.",
                file=sys.stderr,
            )
    return changed


def _prompt_dismiss_or_snooze(alarm: Alarm) -> timedelta | None:
    """Returns a snooze duration, or None to dismiss. Never blocks forever on a non-tty."""
    if not sys.stdin.isatty():
        time_mod.sleep(NON_INTERACTIVE_RING_SECONDS)
        return None
    try:
        response = input("  [Enter] dismiss, or type a snooze length (e.g. 5m): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if not response:
        return None
    if response in ("s", "snooze"):
        return DEFAULT_SNOOZE
    try:
        from .timeparse import parse_duration

        return parse_duration("+" + response if not response.startswith("+") else response)
    except Exception:
        print("  Didn't understand that; dismissing.", file=sys.stderr)
        return None


def _fire(alarm: Alarm) -> timedelta | None:
    label = f" - {alarm.label}" if alarm.label else ""
    print(f"\n⏰ ALARM {alarm.time}{label}")
    stop_event = threading.Event()
    ringer = threading.Thread(target=sound.ring_until, args=(stop_event,), daemon=True)
    ringer.start()
    try:
        return _prompt_dismiss_or_snooze(alarm)
    finally:
        stop_event.set()
        ringer.join(timeout=2)


def run_forever() -> None:
    acquire_run_lock()
    snoozes: dict[str, datetime] = {}
    last_mtime = None
    print(f"alarmclock watching {storage.alarms_path()} - press Ctrl+C to stop.")

    while True:
        path = storage.alarms_path()
        mtime = path.stat().st_mtime if path.exists() else None
        alarms = storage.load()
        now = datetime.now().astimezone()

        if mtime != last_mtime:
            if _disable_missed_one_offs(alarms, now):
                storage.save(alarms)
            last_mtime = path.stat().st_mtime if path.exists() else mtime
        for alarm in alarms:
            if not alarm.enabled:
                continue
            snoozed_until = snoozes.get(alarm.id)
            if snoozed_until and now < snoozed_until:
                continue
            occurrence = next_occurrence(parse_clock_time(alarm.time), alarm.repeat, now)
            due = snoozed_until or occurrence.trigger_at
            if now >= due:
                snoozes.pop(alarm.id, None)
                snooze_for = _fire(alarm)
                if snooze_for:
                    snoozes[alarm.id] = datetime.now().astimezone() + snooze_for
                    print(f"  Snoozed for {int(snooze_for.total_seconds() // 60)} min.")
                elif not alarm.repeat:
                    alarm.enabled = False
                    storage.save(alarms)
                    last_mtime = path.stat().st_mtime

        time_mod.sleep(POLL_INTERVAL_SECONDS)
