# alarmclock

A terminal alarm clock. Set one-off or recurring alarms; a foreground watcher
process rings them at the right time.

There's no web UI, server, or database by design — it's a CLI tool that reads
and writes a local JSON file. Nothing here would meaningfully benefit from
being "hosted": there's no service to deploy, since the whole point is that
it runs on your machine, watching your own terminal. Install instructions
below cover getting it running locally.

```
$ alarmclock add --time 7:00am --label "gym" --repeat weekdays
Added alarm 4f2a: 07:00:00 -> next fires Mon 2026-08-03 07:00

$ alarmclock list
[on ] 4f2a  07:00  mon,tue,wed,thu,fri "gym" (next: Mon 07:00)

$ alarmclock run
alarmclock watching /Users/you/.alarmclock/alarms.json - press Ctrl+C to stop.
```

See [DESIGN.md](DESIGN.md) for the requirements refinement and the reasoning
behind each design decision (why a foreground watcher rather than a daemon,
how DST/missed-alarm/concurrency edge cases are handled, etc).

## Install

Requires Python 3.9+.

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs an `alarmclock` command on your `PATH` (inside the venv).

## Usage

```
alarmclock add --time <time> [--label TEXT] [--repeat SPEC]
alarmclock list
alarmclock enable <id>
alarmclock disable <id>
alarmclock remove <id>
alarmclock run
```

**`--time`** accepts `07:00`, `7:30am`, `19:30`, or a relative offset like
`+10m` / `+1h30m` (useful for trying it out without waiting for a real clock
time — relative offsets are capped under 24h since past that it's not really
an "alarm" anymore).

**`--repeat`** accepts `daily`, `weekdays`, `weekends`, or a comma-separated
list of days (`mon,wed,fri`). Omit it for a one-off alarm. One-off alarms
whose time has already passed today are automatically scheduled for
tomorrow, same as a phone alarm.

**`alarmclock run`** has to actually be running for an alarm to fire — it's a
foreground process, not a background service (see DESIGN.md for why). Leave
it running in a terminal, or in `tmux`/`screen` if you want it to survive
closing the window. When an alarm fires, it rings until you dismiss it
(press Enter) or ask to snooze (type a duration like `5m`).

## Running the tests

```
pip install -e ".[dev]"
pytest
```

The test suite focuses on the scheduling logic in `timeparse.py` — parsing,
rollover to the next day/week, and DST transitions — since that's the part
of an alarm clock that's actually easy to get subtly wrong, plus the storage
layer's atomic-write behavior, a real multi-process concurrency test
(`tests/test_concurrency.py`) that spawns separate OS processes writing to
the same alarms file at once, `sound.py`'s per-platform fallback logic, and
a real multi-process race test for the `run` PID lock (see DESIGN.md §11 —
that one caught an actual startup race between two `run` processes). 84
tests total; also verified to pass on Python 3.9, not just the version it
was developed against.

## Known limitations

- No OS-level integration: if you close the `run` process, put the machine to
  sleep, or log out, alarms won't fire. Building that properly means a
  launchd job on macOS, a systemd unit on Linux, and a Scheduled Task on
  Windows — three different mechanisms, out of scope for a CLI tool with no
  daemon/service layer. This one's a deliberate scope boundary, not
  something left unfinished — see DESIGN.md §2 and §5.
- File locking during concurrent `add`/`remove` uses `fcntl` on POSIX and
  `msvcrt` on Windows. The Windows path was implemented from the standard
  library docs and is covered by tests that mock the `msvcrt` module, but
  this project was built and tested on macOS — it has not been run against
  real Windows file locking, so treat it as best-effort until someone
  verifies it there.
- An alarm set for a wall-clock time that doesn't exist on a given day (the
  one hour skipped during a spring-forward DST transition) will fire at
  whatever the system's timezone library resolves it to rather than being
  explicitly rejected. This is a genuinely rare edge case that didn't seem
  worth the added complexity for this exercise.
