from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from . import storage
from .models import Alarm
from .timeparse import (
    TimeParseError,
    next_occurrence,
    parse_clock_time,
    parse_duration,
    parse_repeat,
)
from .watcher import AlreadyRunningError, run_forever

RELATIVE_TIME_LIMIT = timedelta(hours=24)


def _resolve_alarm_spec(time_text: str, repeat_text: str, now: datetime) -> tuple[str, list[str]]:
    """Turn raw --time/--repeat input into (canonical "HH:MM:SS" time, repeat days).

    Raises TimeParseError for anything invalid, including combinations that
    parse fine individually but don't make sense together.
    """
    if not time_text.startswith("+"):
        canonical = parse_clock_time(time_text).strftime("%H:%M:%S")
        return canonical, parse_repeat(repeat_text)

    if repeat_text:
        raise TimeParseError("--repeat can't be combined with a relative time (+10m)")
    delta = parse_duration(time_text)
    if delta >= RELATIVE_TIME_LIMIT:
        raise TimeParseError(
            "relative times (+10m) only make sense within a day; "
            "use --time HH:MM for anything further out"
        )
    canonical = (now + delta).strftime("%H:%M:%S")
    return canonical, []


def _cmd_add(args: argparse.Namespace) -> int:
    now = datetime.now().astimezone()
    try:
        canonical, repeat = _resolve_alarm_spec(args.time, args.repeat, now)
    except TimeParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    alarm = Alarm(time=canonical, label=args.label or "", repeat=repeat)
    storage.add_alarm(alarm)

    occurrence = next_occurrence(parse_clock_time(canonical), repeat, now)
    when = occurrence.trigger_at.strftime("%a %Y-%m-%d %H:%M")
    note = " (already passed today, rolled to next occurrence)" if occurrence.rolled_forward else ""
    print(f"Added alarm {alarm.id}: {canonical} -> next fires {when}{note}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    alarms = storage.load()
    if not alarms:
        print("No alarms set.")
        return 0
    now = datetime.now().astimezone()
    for alarm in alarms:
        status = "on " if alarm.enabled else "off"
        repeat = ",".join(alarm.repeat) if alarm.repeat else "once"
        label = f" \"{alarm.label}\"" if alarm.label else ""
        next_str = ""
        if alarm.enabled:
            occurrence = next_occurrence(parse_clock_time(alarm.time), alarm.repeat, now)
            next_str = f" (next: {occurrence.trigger_at.strftime('%a %H:%M')})"
        print(f"[{status}] {alarm.id}  {alarm.time[:5]}  {repeat:8}{label}{next_str}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    if storage.remove_alarm(args.id):
        print(f"Removed {args.id}")
        return 0
    print(f"error: no alarm with id '{args.id}'", file=sys.stderr)
    return 1


def _cmd_toggle(args: argparse.Namespace, enabled: bool) -> int:
    if storage.set_enabled(args.id, enabled):
        print(f"{'Enabled' if enabled else 'Disabled'} {args.id}")
        return 0
    print(f"error: no alarm with id '{args.id}'", file=sys.stderr)
    return 1


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        run_forever()
    except AlreadyRunningError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alarmclock", description="A terminal alarm clock.")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add an alarm")
    add.add_argument("--time", "-t", required=True, help="e.g. 07:00, 7:30am, 19:30, or +10m")
    add.add_argument("--label", "-l", default="", help="Optional note shown when it fires")
    add.add_argument(
        "--repeat", "-r", default="",
        help="daily, weekdays, weekends, or a comma list like mon,wed,fri",
    )
    add.set_defaults(func=_cmd_add)

    listp = sub.add_parser("list", help="List alarms")
    listp.set_defaults(func=_cmd_list)

    remove = sub.add_parser("remove", help="Remove an alarm by id")
    remove.add_argument("id")
    remove.set_defaults(func=_cmd_remove)

    disable = sub.add_parser("disable", help="Disable an alarm without deleting it")
    disable.add_argument("id")
    disable.set_defaults(func=lambda a: _cmd_toggle(a, False))

    enable = sub.add_parser("enable", help="Re-enable a disabled alarm")
    enable.add_argument("id")
    enable.set_defaults(func=lambda a: _cmd_toggle(a, True))

    run = sub.add_parser("run", help="Start watching alarms in the foreground")
    run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
