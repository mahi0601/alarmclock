"""Pure time-parsing and scheduling logic. No I/O, no side effects.

Kept separate from storage/watcher so the tricky part of an alarm clock -
"when does this alarm next go off" - can be unit tested without waiting on
a real clock or touching the filesystem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_ALIASES = {
    "monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
    "friday": "fri", "saturday": "sat", "sunday": "sun",
}

_CLOCK_RE = re.compile(
    r"^\s*(?P<h>\d{1,2})(?::(?P<m>\d{2}))?(?::(?P<s>\d{2}))?\s*(?P<ampm>am|pm)?\s*$",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"^\s*\+\s*(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?\s*$", re.IGNORECASE
)


class TimeParseError(ValueError):
    """Raised for any user-supplied time/repeat string we can't make sense of."""


def parse_duration(text: str) -> timedelta:
    """Parse a relative offset like '+10m', '+1h30m', '+45s'.

    Useful for testing the alarm without waiting for a real clock time to
    come around, and for snooze durations.
    """
    match = _DURATION_RE.match(text)
    if not match or not any(match.groups()):
        raise TimeParseError(
            f"'{text}' is not a valid relative duration (expected e.g. +10m, +1h30m, +45s)"
        )
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    if delta <= timedelta(0):
        raise TimeParseError(f"'{text}' resolves to a non-positive duration")
    return delta


def parse_clock_time(text: str) -> time:
    """Parse an absolute wall-clock time: '7:00', '07:00', '7am', '7:30 PM', '19:30'."""
    match = _CLOCK_RE.match(text)
    if not match:
        raise TimeParseError(
            f"'{text}' is not a recognizable time (expected e.g. 07:00, 7:30am, 19:30)"
        )
    hour = int(match.group("h"))
    minute = int(match.group("m") or 0)
    second = int(match.group("s") or 0)
    ampm = (match.group("ampm") or "").lower()

    if ampm:
        if not 1 <= hour <= 12:
            raise TimeParseError(f"'{text}': hour must be 1-12 when am/pm is given")
        if hour == 12:
            hour = 0
        if ampm == "pm":
            hour += 12
    elif not 0 <= hour <= 23:
        raise TimeParseError(f"'{text}': hour must be 0-23 in 24-hour format")

    if not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise TimeParseError(f"'{text}': minutes/seconds must be 0-59")

    return time(hour=hour, minute=minute, second=second)


def parse_repeat(text: str | None) -> list[str]:
    """Parse a repeat spec into a list of weekday codes (mon..sun). Empty list = one-off."""
    if not text or not text.strip():
        return []
    normalized = text.strip().lower()
    if normalized in ("daily", "everyday", "every day"):
        return list(WEEKDAYS)
    if normalized == "weekdays":
        return WEEKDAYS[:5]
    if normalized == "weekends":
        return WEEKDAYS[5:]

    codes = []
    for part in normalized.split(","):
        part = part.strip()
        code = WEEKDAY_ALIASES.get(part, part[:3] if part[:3] in WEEKDAYS else None)
        if code is None:
            raise TimeParseError(
                f"'{part}' is not a recognized day (use mon/tue/.../sun, 'daily', "
                "'weekdays', or 'weekends')"
            )
        if code not in codes:
            codes.append(code)
    return codes


@dataclass(frozen=True)
class NextOccurrence:
    trigger_at: datetime
    rolled_to_tomorrow: bool  # True if a one-off alarm's time had already passed today


def next_occurrence(alarm_time: time, repeat: list[str], now: datetime) -> NextOccurrence:
    """Compute the next datetime an alarm should fire, given the current moment.

    `now` must be timezone-aware if the caller cares about DST correctness;
    this function only ever compares wall-clock values derived from `now`,
    so it never does time arithmetic across a DST boundary.
    """
    today_candidate = datetime.combine(now.date(), alarm_time, tzinfo=now.tzinfo)

    if not repeat:
        if today_candidate > now:
            return NextOccurrence(today_candidate, rolled_to_tomorrow=False)
        tomorrow = datetime.combine(now.date() + timedelta(days=1), alarm_time, tzinfo=now.tzinfo)
        return NextOccurrence(tomorrow, rolled_to_tomorrow=True)

    # Recurring: scan forward at most 7 days to find the next matching weekday
    # whose time hasn't already passed. Day 7 always matches today's weekday
    # again next week, so this loop is guaranteed to terminate.
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] not in repeat:
            continue
        candidate = datetime.combine(day, alarm_time, tzinfo=now.tzinfo)
        if candidate > now:
            return NextOccurrence(candidate, rolled_to_tomorrow=(offset > 0))

    raise AssertionError("unreachable: a repeating alarm always has a next occurrence")
