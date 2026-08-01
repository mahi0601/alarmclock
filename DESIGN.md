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
  id: str        # short, stable, user-facing (e.g. "a1b2")
  time: str      # canonical "HH:MM:SS", 24h, local wall-clock
  label: str     # optional, defaults to ""
  repeat: [str]  # weekday codes ("mon".."sun"); [] means one-off
  enabled: bool
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
| Two `alarmclock run` processes started at nearly the same instant | An atomic exclusive file create (`O_CREAT \| O_EXCL`), not a check-then-write, decides which one gets the PID lock — a real concurrent reproduction with 8-16 racing processes previously showed up to 7 of 8 wrongly believing they'd acquired it; that reproduction is now a passing regression test. |
| Alarms file edited/added-to while `run` is already watching | Watcher reloads the alarms file every poll tick (short sleep chunks, not one long `sleep()`) and diffs the current alarm IDs against what it saw last tick, so a new alarm added in another terminal is picked up without restarting the watcher. |
| Concurrent writes to the alarms file from separate processes (e.g. `add` run from two terminals at once) | Write-to-temp-file-then-atomic-rename for corruption-proofing, plus an advisory exclusive lock (`fcntl` on POSIX, `msvcrt` on Windows) around each read-modify-write so one write can't be lost under the other. Verified with a test that spawns real separate processes writing concurrently — and confirmed the test actually catches the bug by deliberately breaking the lock and watching it fail. |
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

## 7. A real bug caught during testing

The first version of the `run` loop recomputed each alarm's next occurrence on every poll
tick by calling `next_occurrence(alarm_time, repeat, now)` with the *current* `now`, then
compared that result back against the same `now`. `next_occurrence` is specified to always
return a time strictly after whatever `now` it's given (that's what makes it correct for
`add`/`list`, which want "when will this next go off"). Applied inside the firing loop, that
same guarantee means the comparison could never be true — the computed time is by
construction always slightly ahead of the `now` used to compute it, on every single tick.
The watcher would sit there forever and never fire anything.

This didn't show up in the `timeparse` unit tests (those test `next_occurrence` in
isolation, correctly, against fixed `now` values) or in manual CLI smoke testing (`add`,
`list`, `remove` all looked right). It only surfaced when I ran `alarmclock run` against a
real near-future alarm and watched it silently do nothing.

The fix: the watcher now computes each alarm's next trigger time once, when it first sees
that alarm, and holds that value fixed across ticks. Each tick just asks "has real time
reached the value I already computed", instead of asking "what's the next value, relative to
right now" — which is a different question with a different (and previously wrong) answer.
`schedule[alarm.id]` is only recomputed after the alarm actually fires (or when it's newly
added/re-enabled). Covered by `tests/test_watcher.py`, which drives a fake clock through a
fixed sequence of instants and asserts a fire actually happens — a test that would have
caught this before it ever reached a real terminal.

## 8. A cleanup pass, and why it stopped short of "Clean Architecture"

After the implementation was working, I went back through it for straightforward clean-code
issues: an unused import, a stale comment (`Alarm.time` documented as "HH:MM" when it's
actually stored as "HH:MM:SS"), a misleadingly-named field (`rolled_to_tomorrow` used for
alarms that could roll a full week forward, not just a day), a reflection hack
(`type(alarm).__dataclass_fields__["id"].default_factory()`) that a plain function call does
more plainly, and one function — `run_forever`'s main loop — that had grown to do three
unrelated things at once (reconcile the schedule against the current alarm list, detect
missed alarms, fire due alarms). That last one I split into `_sync_schedule` and
`_fire_due_alarms`, each independently readable and already covered by the existing watcher
tests since the split didn't change behavior.

What I didn't do: restructure this into formal Clean Architecture (entities, use-case
interactors, repository interfaces, dependency injection). That pattern earns its keep when
infrastructure is genuinely swappable — e.g. swapping a JSON file for a database without
touching business logic. This tool has one storage backend, one entry point, and no
plausible near-term need for another; adding interfaces and DI around a single JSON file
would be indirection with nothing on the other end of it. Section 2 already lays out the
same principle for features — it applies to internal structure just as much.

## 9. Closing the gaps a second look turned up

Running `pyflakes` and testing against Python 3.9 directly (rather than only the 3.12 dev
venv) turned up an unused import, an unused test variable, and confirmed the `>=3.9` claim
in `pyproject.toml` was actually true rather than just asserted. Also found: `sound.py` had
real per-platform branching with zero automated coverage — only "I heard a beep" during
manual runs — so I added unit tests for each platform path and the bell fallback, injecting
a fake `winsound` module via `sys.modules` so the Windows path is testable from any OS.

Two more substantive gaps:

- **`list`'s column alignment broke for long custom `--repeat` values** (`mon,tue,wed,thu,fri`
  overflowed a hardcoded 8-character field). Fixed by computing the column width from what's
  actually being displayed instead of a fixed guess.
- **The concurrent-write locking was only tested for atomicity of a single write, never for
  actually serializing concurrent writers.** Added `tests/test_concurrency.py`, which spawns
  real separate OS processes (via `multiprocessing`, `spawn` start method — genuine
  independent Python interpreters, not threads) hammering `add_alarm` on the same file at
  once. To make sure this test had teeth rather than passing by coincidence, I temporarily
  neutered the lock and reran it: it failed reliably (13-22 of 32 alarms survived instead of
  32, i.e. writers clobbering each other), then passed again once the lock was restored. A
  concurrency test that can't be shown to fail when the thing it protects is broken isn't
  proving anything.

One gap I closed partially, with the limitation stated rather than hidden: Windows file
locking was fcntl-only (a no-op on Windows). Implemented a `msvcrt`-based path following the
standard library docs, with unit tests that mock the `msvcrt` module to verify the code calls
the documented API correctly. What that *doesn't* prove is that real Windows enforces the
lock the way the tests assume — this project was built and tested on macOS, and I have no
Windows machine to verify against. Said so plainly in the README rather than upgrading the
limitations list to "fixed" on the strength of a mocked test alone. A gap that's real but
untestable in the current environment should be labeled that way, not quietly closed.

One item from the original limitations list I deliberately left alone: no launchd/systemd/
Task Scheduler integration. That's not an oversight surfaced by more testing — it's the same
scope boundary from §2 and §5, and building three untested OS-service integrations to
"complete the list" would be scope creep dressed up as thoroughness.

## 10. A pass for things that only look correct

A later re-read specifically for code that works but shouldn't have been written that way
turned up:

- **`Alarm.created_at`** — a timestamp field, set on every alarm, serialized to disk,
  and never read anywhere: not displayed by `list`, not used by any sorting or expiry logic.
  The kind of field that gets added because a data model "should probably" track creation
  time, without an actual use case behind it. Removed, along with the now-unused
  `datetime`/`timezone` import that only existed to compute it.
- **This file's own data model block (§3) still said `time: "HH:MM"`** — the exact stale
  claim §8 describes fixing in `models.py`'s comment, left uncorrected in the mirror copy
  three sections up in the same document. Docs drift the same way code does; fixing an issue
  in one place doesn't mean it's gone everywhere it was written down.
- **A dead line and a no-op line in `tests/test_cli.py`**: `out = capsys.readouterr().out`
  whose value was immediately overwritten and never read, followed by a bare
  `capsys.readouterr()` call that discarded nothing (the previous line had already drained
  the buffer) — leftover from an earlier version of the test that never got swept up.
- **`lambda a: fired.append(a.id) or None`, three times in `tests/test_watcher.py`** —
  `list.append()` already returns `None`, so `or None` was inert. A defensive habit copied
  from patterns where the left side *can* return something truthy (`dict.update`, boolean
  short-circuiting for a default), applied somewhere it does nothing.

None of these were bugs — the tests still passed and the field didn't corrupt anything sitting
unused in the JSON. That's exactly why they're worth naming separately from the bug in §7:
working and correct aren't the same thing, and "the tests are green" doesn't catch a field
nobody reads or a doc comment nobody updated.

## 11. Two more real bugs, found by trying to break it rather than re-read it

A final pass, done by actively trying to construct failure scenarios instead of rereading code
and trusting it, turned up two genuine correctness bugs — both confirmed by reproducing them for
real before fixing, and both now covered by regression tests that were verified to fail against
the old code and pass against the fix.

**Disabling a snoozed alarm didn't clear its snooze.** `_sync_schedule`'s disabled-alarm branch
popped the alarm out of `schedule` but not out of `snoozes`. Scenario: a one-off alarm fires, gets
snoozed for 10 minutes; before those 10 minutes pass, it's disabled (`alarmclock disable`) and
then re-enabled. Re-enabling recomputes a fresh, correct schedule entry (for tomorrow, say) — but
the stale snooze entry from before the disable was never cleared, and `_fire_due_alarms` checks
snooze before schedule. The alarm would go off at the old snooze time, ignoring the freshly
computed schedule entirely. Reproduced directly against the internal functions, fixed with one
added line (`snoozes.pop(alarm.id, None)` alongside the existing `schedule.pop`), and covered by
`test_disable_then_reenable_during_a_snooze_drops_the_stale_snooze`.

**The PID lock had a real startup race.** `acquire_run_lock` used to check `lock_path.exists()`
and, if not, write its own PID — a classic check-then-write gap. Reproduced with 8-16 real
separate processes (via `multiprocessing`, not threads) all calling `acquire_run_lock()` at
nearly the same instant: in repeated trials, anywhere from 2 to all 8 of them wrongly believed
they'd acquired the exclusive lock. Fixed by switching to an atomic `os.open(path, O_CREAT |
O_EXCL)`, which the OS guarantees only one concurrent caller can succeed at. That surfaced a
second, subtler issue on the way to fixing it: a process that loses the race and reads the lock
file in the narrow window before the winner has actually written its PID into it sees an empty
file, and the original code treated "can't parse a PID" as "nobody valid owns this, safe to take
over" — which is exactly the bug, just moved one step later. Fixed by writing the PID
immediately on the raw file descriptor (shrinking the window) and retrying the read briefly
before concluding a lock is genuinely stale, rather than concluding that from a single read.
Covered by `test_acquire_run_lock_serializes_across_real_concurrent_processes`, which — like the
concurrency test in §9 — was confirmed to fail against the old code before it was confirmed to
pass against the fix.

Both bugs existed through every earlier pass in this log, including the "vibe-coded smells" pass
in §10. Finding them took actually trying to break the code — constructing the specific sequence
of operations (disable mid-snooze; two processes racing to start) that would expose them — rather
than reading it in isolation and asking whether it looked right. Looking right and being right
are different properties, and only one of them is checked by staring at code you already believe
is correct.
