"""Alarm persistence: a JSON file with atomic writes and advisory locking.

Atomic writes matter because `add`/`remove` do read-modify-write on a shared
file, and two invocations can race (e.g. two terminals). Writing to a temp
file and renaming over the target means a crash mid-write never leaves a
half-written, unparseable JSON file behind - `os.replace` is atomic on both
POSIX and Windows.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from .models import Alarm, generate_alarm_id

try:
    import fcntl  # POSIX only
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None

try:
    import msvcrt  # Windows only
except ImportError:  # pragma: no cover - exercised only on POSIX
    msvcrt = None


def data_dir() -> Path:
    override = os.environ.get("ALARMCLOCK_HOME")
    path = Path(override) if override else Path.home() / ".alarmclock"
    path.mkdir(parents=True, exist_ok=True)
    return path


def alarms_path() -> Path:
    return data_dir() / "alarms.json"


def pid_lock_path() -> Path:
    return data_dir() / "run.lock"


def _msvcrt_lock_region(handle) -> None:
    """Lock byte 0 of `handle`, writing a placeholder byte if the file is empty.

    msvcrt.locking() locks a byte range starting at the current file
    position rather than the whole file (unlike flock), so there needs to be
    at least one byte present to lock. LK_LOCK retries roughly once a second
    for ~10 seconds before raising OSError, rather than blocking
    indefinitely like flock's LOCK_EX - acceptable here since this lock is
    only ever held for a brief read-modify-write of a small JSON file.

    NOTE: implemented from the msvcrt documentation; this project was built
    and tested on macOS, so this path has not been run on real Windows.
    """
    handle.seek(0)
    if not handle.read(1):
        handle.seek(0)
        handle.write("0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _msvcrt_unlock_region(handle) -> None:
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def _locked(path: Path):
    """Advisory exclusive lock so concurrent CLI invocations don't interleave writes.

    Uses fcntl on POSIX and msvcrt on Windows. On any other platform this
    falls back to no locking; the atomic rename in save() still prevents
    file corruption there, it just doesn't serialize concurrent
    read-modify-write cycles.
    """
    lock_file = path.with_suffix(path.suffix + ".op-lock")
    handle = open(lock_file, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            _msvcrt_lock_region(handle)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            _msvcrt_unlock_region(handle)
        handle.close()


def load() -> list[Alarm]:
    path = alarms_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} is corrupted and can't be parsed as JSON: {exc}. "
            "Fix or remove it manually before continuing."
        ) from exc
    return [Alarm.from_dict(item) for item in raw]


def save(alarms: list[Alarm]) -> None:
    path = alarms_path()
    payload = json.dumps([a.to_dict() for a in alarms], indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".alarms-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp_name)
        raise


def add_alarm(alarm: Alarm) -> Alarm:
    with _locked(alarms_path()):
        alarms = load()
        existing_ids = {a.id for a in alarms}
        while alarm.id in existing_ids:  # extremely unlikely, but don't silently collide
            alarm.id = generate_alarm_id()
        alarms.append(alarm)
        save(alarms)
    return alarm


def remove_alarm(alarm_id: str) -> bool:
    with _locked(alarms_path()):
        alarms = load()
        remaining = [a for a in alarms if a.id != alarm_id]
        if len(remaining) == len(alarms):
            return False
        save(remaining)
    return True


def set_enabled(alarm_id: str, enabled: bool) -> bool:
    with _locked(alarms_path()):
        alarms = load()
        for a in alarms:
            if a.id == alarm_id:
                a.enabled = enabled
                save(alarms)
                return True
    return False
