# Design Log — CLI Alarm Clock

This file captures the requirements refinement and design decisions made before writing
code, and the tradeoffs behind them. The brief was intentionally open-ended ("no detailed
spec; decide what to build"), so the first job was to turn that into a bounded scope.

## 1. Reading the brief

Constraints given: Python, CLI only, no web UI, no database. No detail on:
- single alarm vs. multiple
- recurring vs. one-shot
- how "ringing" should behave in a terminal
- whether alarms need to survive restarts

Evaluation criteria stated explicitly: judgment and process matter more than feature count.
That cuts against building the maximal feature set and toward building a small number of
things *correctly*, with the edge cases in those things actually handled, and the ones I
deliberately didn't handle written down rather than silently ignored.

## 2. Scoping decisions

**In scope:**
- Multiple named alarms, not just one — a single global alarm is trivial enough that it
  doesn't exercise any interesting design decisions (storage, IDs, concurrent access).
- One-off alarms *and* recurring alarms (daily / specific weekdays) — the "next occurrence"
  calculation is the one genuinely tricky piece of an alarm clock (DST, week wraparound,
  "time already passed today"), so it's worth doing properly rather than punting.
- Persistence across process restarts (alarms are only useful if `add` and `run` can be
  separate invocations, possibly separate terminals).
- A foreground watcher process (`alarmclock run`) that actually fires alarms.

**Explicitly out of scope, with reasons:**
- A background daemon / OS service (launchd/systemd) that survives logout or machine sleep.
  A real alarm clock needs this, but it's an OS-integration problem, not an "alarm clock
  logic" problem, and every OS does it differently. Documented as a known limitation instead
  of half-implemented.
- Web/GUI notifications, calendar sync, multiple simultaneous timezones — not asked for and
  would dilute the exercise.
- A sound *file* being bundled — I use the terminal bell plus a best-effort platform beep,
  because shipping and licensing an audio asset is orthogonal to what's being evaluated here.

## 3. Data model

```
Alarm:
  id: str            # short, stable, user-facing (e.g. "a1b2")
  time: "HH:MM"       # stored canonical 24h, local wall-clock time
  label: str          # optional, defaults to ""
  repeat: [str]        # weekday codes ("mon".."sun"); [] means one-off
  enabled: bool
  created_at: ISO 8601 timestamp
```

Time is stored as local wall-clock ("7:00"), not as a UTC instant. This matters: a
wall-clock alarm should still fire at 7:00 local time across a DST transition. Storing a
fixed UTC offset would silently shift the alarm by an hour twice a year.

## 4. Key edge cases and how they're handled

| Edge case | Decision |
|---|---|
| One-off alarm time already passed today | Schedule for tomorrow (matches phone alarm behavior), and say so explicitly at `add` time rather than failing silently. |
| DST transition between now and trigger | Compute next occurrence using timezone-aware local time (`zoneinfo`), not a fixed offset, so wall-clock time is preserved. |
| Recurring alarm, all repeat days are "in the past" this week | Wrap to next week; verified with a test where "now" is late Friday and the only repeat day is Monday. |
| Invalid time format | Reject at `add` time with a clear error and non-zero exit code — fail fast, don't store garbage. |
| Two `alarmclock run` processes started by mistake | PID lock file; second process refuses to start rather than double-firing alarms. |
| Alarms file edited/added-to while `run` is already watching | Watcher polls the file's mtime each tick (short sleep chunks, not one long `sleep()`), so a new alarm added in another terminal is picked up without restarting the watcher. |
| Concurrent writes to the alarms file (e.g. `add` run twice quickly) | Write-to-temp-file-then-atomic-rename, so a crash or race never leaves a half-written JSON file. |
| Machine asleep / process not running when a one-off alarm's time passes | On startup, one-off alarms whose time is already more than a minute in the past are marked missed and disabled rather than firing immediately on wake — an alarm going off hours late is worse than not going off. |
| `run` invoked in a non-interactive context (piped/no tty) | Skip the "press enter to dismiss/snooze" prompt (it would hang forever waiting on stdin); ring for a fixed duration and auto-dismiss. |
| Sound playback fails (headless box, no audio device) | Caught narrowly (the specific subprocess/OS errors, not a bare `except`) and falls back to the terminal bell character, so a missing speaker never crashes the alarm. |

## 5. Why a foreground watcher, not a background daemon

`alarmclock run` blocks in the foreground and fires alarms as they come due. This is an
honest reflection of what a "no database, no web UI" CLI can do without OS-specific service
integration: it's not pretending to be more than it is. The README states plainly that the
process must be running (e.g. in a terminal or `tmux`/`screen` session) for alarms to fire —
that's a real limitation, not a bug.

## 6. Implementation plan

1. `timeparse.py` — pure functions: parsing input strings, computing the next occurrence.
   No I/O, so this is exhaustively unit-testable without waiting on real time.
2. `models.py` — the `Alarm` dataclass and (de)serialization.
3. `storage.py` — atomic JSON read/write, id generation, duplicate/lock handling.
4. `sound.py` — small platform-beep abstraction with fallback.
5. `watcher.py` — the `run` loop: load, wait, trigger, snooze/dismiss, missed-alarm handling,
   PID lock.
6. `cli.py` — argparse subcommands wiring the above together.
7. Tests for the pure logic (`timeparse`) and storage, since those are where correctness
   actually matters; the CLI layer is thin and tested at the integration level.

