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


def _is_missed_one_off(alarm: Alarm, now: datetime) -> bool:
    """True for a one-off alarm whose time passed more than a minute ago.

    Used only when an alarm is first noticed (startup, or just added/re-enabled)
    - a one-off going off hours late because the watcher wasn't running is worse
    than it simply not going off.
    """
    if alarm.repeat:
        return False
    candidate_today = datetime.combine(now.date(), parse_clock_time(alarm.time), tzinfo=now.tzinfo)
    return now - candidate_today > MISSED_THRESHOLD


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
    """Fire alarms as they come due.

    `schedule` holds each enabled alarm's next trigger time, computed once
    and left fixed until it's reached. This is deliberate: calling
    next_occurrence(..., now) fresh on every tick would always hand back a
    time strictly after that same `now`, so comparing it against `now` right
    away could never be true - the alarm would never fire. Freezing the
    target the first time an alarm is seen, and only recomputing it after it
    actually fires, is what lets real elapsed time catch up to it.
    """
    acquire_run_lock()
    schedule: dict[str, datetime] = {}
    snoozes: dict[str, datetime] = {}
    known_ids: set[str] = set()
    print(f"alarmclock watching {storage.alarms_path()} - press Ctrl+C to stop.")

    while True:
        now = datetime.now().astimezone()
        alarms = storage.load()
        alarms_by_id = {a.id: a for a in alarms}
        dirty = False

        for stale_id in known_ids - alarms_by_id.keys():
            schedule.pop(stale_id, None)
            snoozes.pop(stale_id, None)
        known_ids = set(alarms_by_id)

        for alarm in alarms:
            if not alarm.enabled:
                schedule.pop(alarm.id, None)
                continue
            if alarm.id in schedule:
                continue
            if _is_missed_one_off(alarm, now):
                alarm.enabled = False
                dirty = True
                label = f" ({alarm.label})" if alarm.label else ""
                print(
                    f"[missed] '{alarm.time}'{label} was due while alarmclock wasn't "
                    "running; disabling it rather than firing it late.",
                    file=sys.stderr,
                )
                continue
            schedule[alarm.id] = next_occurrence(parse_clock_time(alarm.time), alarm.repeat, now).trigger_at

        if dirty:
            storage.save(alarms)

        for alarm in alarms:
            if not alarm.enabled:
                continue
            due = snoozes.get(alarm.id) or schedule.get(alarm.id)
            if due is None or now < due:
                continue
            snoozes.pop(alarm.id, None)
            snooze_for = _fire(alarm)
            fired_at = datetime.now().astimezone()
            if snooze_for:
                snoozes[alarm.id] = fired_at + snooze_for
                print(f"  Snoozed for {int(snooze_for.total_seconds() // 60)} min.")
            elif alarm.repeat:
                schedule[alarm.id] = next_occurrence(parse_clock_time(alarm.time), alarm.repeat, fired_at).trigger_at
            else:
                alarm.enabled = False
                storage.save(alarms)
                schedule.pop(alarm.id, None)

        time_mod.sleep(POLL_INTERVAL_SECONDS)
