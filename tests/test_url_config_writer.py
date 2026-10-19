from __future__ import annotations

import datetime as dt
import os
import threading
from pathlib import Path

import pytest
from tools import url_config_writer as ucw

FIX = Path(__file__).parent / "fixtures"
LINE = "https://live.douyin.com/967541884601,主播: 甜不辣"


def test_appended_bytes_keeps_bom_lf_and_prefix():
    original = (FIX / "yule_URL_config.ini").read_bytes()
    new = ucw.appended_bytes(original, LINE)
    assert new.startswith(original) and new[: 3] == ucw.BOM
    assert new[len(original):] == (LINE + "\n").encode("utf-8")
    assert b"\r" not in new


def test_appended_bytes_adds_missing_newline_and_handles_empty():
    assert ucw.appended_bytes(ucw.BOM + b"a", "b") == ucw.BOM + b"a\nb\n"
    assert ucw.appended_bytes(b"", "b") == ucw.BOM + b"b\n"
    with pytest.raises(ValueError):
        ucw.appended_bytes(b"", "a\nb")


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    cfg = tmp_path / "yule" / "config"
    cfg.mkdir(parents=True)
    target = cfg / "URL_config.ini"
    target.write_bytes((FIX / "yule_URL_config.ini").read_bytes())
    os.chmod(target, 0o644)
    other = tmp_path / "zhishi.ini"
    other.write_bytes((FIX / "zhishi_URL_config.ini").read_bytes())
    return target, other, tmp_path / ".lock", tmp_path / "yule" / "bot_backups"


def test_append_locked_backup_and_restore_roundtrip(tmp_path):
    target, other, lock, bdir = _setup(tmp_path)
    original = target.read_bytes()
    expected = {str(target): ucw.sha256_bytes(original), str(other): ucw.sha256_file(str(other))}
    res = ucw.append_line_locked(str(target), LINE, expected, str(lock), str(bdir))
    assert Path(res["backup"]).read_bytes() == original
    assert target.read_bytes() == original + (LINE + "\n").encode()
    assert (target.stat().st_mode & 0o777) == 0o644
    assert not list(target.parent.glob("*.tmp.*"))
    ucw.restore(res["backup"], str(target), str(lock))
    assert target.read_bytes() == original


def test_append_refuses_when_any_file_changed(tmp_path):
    target, other, lock, bdir = _setup(tmp_path)
    expected = {str(target): ucw.sha256_file(str(target)), str(other): "0" * 64}
    before = target.read_bytes()
    with pytest.raises(ucw.ConcurrentChangeError):
        ucw.append_line_locked(str(target), LINE, expected, str(lock), str(bdir))
    assert target.read_bytes() == before and not bdir.exists()


def test_lock_is_exclusive(tmp_path):
    lock = str(tmp_path / ".lock")
    with ucw.file_lock(lock):
        err: list[BaseException] = []

        def other() -> None:
            try:
                with ucw.file_lock(lock, timeout_s=0.5):
                    pass
            except BaseException as exc:  # noqa: BLE001
                err.append(exc)

        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert err and isinstance(err[0], TimeoutError)


def test_backup_rotation_keeps_latest(tmp_path):
    f = tmp_path / "URL_config.ini"
    f.write_bytes(b"x")
    bdir = tmp_path / "b"
    for i in range(5):
        ucw.backup(str(f), str(bdir), keep=3, now=dt.datetime(2026, 10, 19, 18, 0, i))
    assert sorted(os.listdir(bdir)) == [f"URL_config.ini.20261019-18000{i}" for i in (2, 3, 4)]
