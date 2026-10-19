"""Safe, append-only writer for URL_config.ini (stdlib only, Python >= 3.10).

Runs on the recorder host. It is also imported by tests with temporary files.
Locking uses ``flock(2)`` on a shared lock file, compatible with the ``flock`` CLI.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager

BOM = b"\xef\xbb\xbf"


class ConcurrentChangeError(RuntimeError):
    """The file changed since it was read for dedup; re-run the dry-run."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def appended_bytes(content: bytes, line: str) -> bytes:
    if "\n" in line or "\r" in line or not line.strip():
        raise ValueError("line must be a single non-empty line")
    body = content if content else BOM
    if body != BOM and not body.endswith(b"\n"):
        body += b"\n"
    return body + line.encode("utf-8") + b"\n"


@contextmanager
def file_lock(lock_path: str, timeout_s: float = 30.0) -> Iterator[None]:
    import time

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"lock busy: {lock_path}") from None
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def atomic_write(path: str, data: bytes, uid: int | None = None, gid: int | None = None, mode: int = 0o644) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if uid is not None and gid is not None:
            os.chown(tmp, uid, gid)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def backup(path: str, backup_dir: str, keep: int = 30, now: _dt.datetime | None = None) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, f"{os.path.basename(path)}.{stamp}")
    if os.path.exists(dest):
        raise FileExistsError(dest)
    shutil.copy2(path, dest)
    if sha256_file(dest) != sha256_file(path):
        raise OSError("backup verification failed")
    prefix = os.path.basename(path) + "."
    olds = sorted(f for f in os.listdir(backup_dir) if f.startswith(prefix))
    for name in olds[:-keep] if keep > 0 else []:
        os.remove(os.path.join(backup_dir, name))
    return dest


def append_line_locked(
    path: str,
    line: str,
    expected_sha: dict[str, str],
    lock_path: str,
    backup_dir: str,
) -> dict[str, str]:
    """Under the lock: verify every file in ``expected_sha`` is unchanged, back up ``path``,
    append ``line`` and atomically replace. Owner/mode of the original file are kept."""
    with file_lock(lock_path):
        for other, sha in expected_sha.items():
            if sha256_file(other) != sha:
                raise ConcurrentChangeError(f"{other} changed since dry-run")
        with open(path, "rb") as f:
            original = f.read()
        st = os.stat(path)
        bak = backup(path, backup_dir)
        new = appended_bytes(original, line)
        atomic_write(path, new, st.st_uid, st.st_gid, st.st_mode & 0o777)
        return {"backup": bak, "old_sha": sha256_bytes(original), "new_sha": sha256_bytes(new)}


def restore(backup_path: str, path: str, lock_path: str) -> str:
    """Roll back ``path`` to ``backup_path`` atomically (note: running recorder threads are not stopped)."""
    with file_lock(lock_path):
        st = os.stat(path)
        with open(backup_path, "rb") as f:
            data = f.read()
        atomic_write(path, data, st.st_uid, st.st_gid, st.st_mode & 0o777)
        return sha256_bytes(data)


if __name__ == "__main__":  # remote entry point: JSON job on stdin, JSON result on stdout
    job = json.load(sys.stdin)
    try:
        result = append_line_locked(job["path"], job["line"], job["expected_sha"], job["lock_path"], job["backup_dir"])
        print(json.dumps({"ok": True, **result}))
    except Exception as exc:  # report everything back to the caller
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
