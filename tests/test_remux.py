# -*- coding: utf-8 -*-
"""Tests for src.remux: low-priority prefix, ffmpeg deletion, and queue limits."""

import os
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from src import remux
from src.remux import (
    RemuxQueue,
    detect_low_priority_prefix,
    low_priority_prefix,
    popen_kwargs,
    run_ffmpeg_then_delete,
)

TIMEOUT = 5.0


class _RecordingLogger:
    """Minimal loguru-compatible logger used to assert log messages in tests."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def _record(self, level: str, message, *_args, **_kwargs) -> None:
        self.messages.append((level, str(message)))

    def debug(self, message, *args, **kwargs) -> None:
        self._record("debug", message, *args, **kwargs)

    def info(self, message, *args, **kwargs) -> None:
        self._record("info", message, *args, **kwargs)

    def warning(self, message, *args, **kwargs) -> None:
        self._record("warning", message, *args, **kwargs)

    def error(self, message, *args, **kwargs) -> None:
        self._record("error", message, *args, **kwargs)

    def exception(self, message, *args, **kwargs) -> None:
        self._record("exception", message, *args, **kwargs)

    def text(self) -> str:
        return "\n".join(text for _level, text in self.messages)


def _wait_for(predicate, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class _FakeProcess:
    """Minimal subprocess.Popen stand-in driven entirely by test data."""

    def __init__(self, output=b"", returncode=0):
        self.output = output
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def communicate(self, timeout=None):
        if isinstance(self.output, BaseException):
            raise self.output
        return self.output, None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.returncode


def _popen_returning(process):
    def popen(_cmd, **kwargs):
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT
        return process

    return popen


class _WaitTimesOutProcess(_FakeProcess):
    """communicate 与 terminate 后的 wait 都会超时，直到 kill 才退出。"""

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired("ffmpeg", timeout)

    def wait(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        self.returncode = -9
        return self.returncode


def test_concurrency_limit():
    lock = threading.Lock()
    state = {"current": 0, "max": 0, "done": 0}

    def runner(_path, _delete_origin):
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
            state["done"] += 1

    remux_queue = RemuxQueue(2, runner)
    for index in range(20):
        assert remux_queue.submit(f"/tmp/remux-{index}.ts", False) is True

    assert remux_queue.join(TIMEOUT) is True
    assert state["max"] == 2
    assert state["done"] == 20


def test_fifo_order():
    executed = []
    paths = [f"/tmp/fifo-{index}.ts" for index in range(10)]
    remux_queue = RemuxQueue(1, lambda path, _delete: executed.append(path))
    for path in paths:
        assert remux_queue.submit(path, False) is True

    assert remux_queue.join(TIMEOUT) is True
    assert executed == paths


def test_unbounded_concurrency_when_max_is_zero():
    gate = threading.Event()
    lock = threading.Lock()
    started = []
    gate_results = []

    def runner(path, _delete_origin):
        with lock:
            started.append(path)
        gate_results.append(gate.wait(TIMEOUT))

    remux_queue = RemuxQueue(0, runner)
    for index in range(10):
        assert remux_queue.submit(f"/tmp/unbounded-{index}.ts", False) is True

    assert _wait_for(lambda: len(started) == 10) is True
    gate.set()
    assert remux_queue.join(TIMEOUT) is True
    assert len(started) == 10
    assert len(gate_results) == 10
    assert all(gate_results)


def test_duplicate_path_only_runs_once():
    gate = threading.Event()
    calls = []
    lock = threading.Lock()

    def runner(path, _delete_origin):
        with lock:
            calls.append(path)
        gate.wait(TIMEOUT)

    remux_queue = RemuxQueue(1, runner)
    assert remux_queue.submit("/tmp/duplicate.ts", False) is True
    assert _wait_for(lambda: bool(calls)) is True
    assert remux_queue.submit("/tmp/duplicate.ts", False) is False

    gate.set()
    assert remux_queue.join(TIMEOUT) is True
    assert calls == ["/tmp/duplicate.ts"]


def test_duplicate_submit_logs_skip(monkeypatch):
    fake_logger = _RecordingLogger()
    monkeypatch.setattr(remux, "logger", fake_logger)
    gate = threading.Event()

    def runner(_path, _delete_origin):
        gate.wait(TIMEOUT)

    remux_queue = RemuxQueue(1, runner)
    assert remux_queue.submit("/tmp/dup-logged.ts", False) is True
    assert remux_queue.submit("/tmp/dup-logged.ts", False) is False

    gate.set()
    assert remux_queue.join(TIMEOUT) is True
    assert "转封装任务已在队列中，跳过重复提交" in fake_logger.text()
    assert "dup-logged.ts" in fake_logger.text()


def test_runner_returning_false_logs_failure_without_completion(monkeypatch):
    fake_logger = _RecordingLogger()
    monkeypatch.setattr(remux, "logger", fake_logger)

    remux_queue = RemuxQueue(1, lambda _path, _delete_origin: False)
    assert remux_queue.submit("/tmp/failed-remux.ts", False) is True
    assert remux_queue.join(TIMEOUT) is True

    logged = fake_logger.text()
    assert "转封装失败" in logged
    assert "failed-remux.ts" in logged
    assert "转封装完成" not in logged


def test_exception_does_not_kill_worker():
    executed = []
    lock = threading.Lock()

    def runner(path, _delete_origin):
        with lock:
            executed.append(path)
        if path.endswith("bad.ts"):
            raise RuntimeError("boom")

    paths = ["/tmp/bad.ts", "/tmp/good-1.ts", "/tmp/good-2.ts"]
    remux_queue = RemuxQueue(1, runner)
    for path in paths:
        assert remux_queue.submit(path, False) is True

    assert remux_queue.join(TIMEOUT) is True
    assert executed == paths
    assert remux_queue.pending() == []


def _which_from(mapping):
    def which(name):
        return mapping.get(name)

    return which


def _run_returning(return_code):
    def run(*_args, **_kwargs):
        return SimpleNamespace(returncode=return_code)

    return run


def _run_raising():
    def run(*_args, **_kwargs):
        raise OSError("ionice failed")

    return run


@pytest.mark.parametrize(
    ("which_map", "run_factory", "expected"),
    [
        (
            {"ionice": "/usr/bin/ionice", "nice": "/usr/bin/nice"},
            lambda: _run_returning(0),
            ["ionice", "-c3", "nice", "-n", "19"],
        ),
        (
            {"ionice": "/usr/bin/ionice", "nice": "/usr/bin/nice"},
            lambda: _run_returning(1),
            ["nice", "-n", "19"],
        ),
        (
            {"ionice": "/usr/bin/ionice", "nice": "/usr/bin/nice"},
            _run_raising,
            ["nice", "-n", "19"],
        ),
        ({}, lambda: _run_returning(0), []),
    ],
)
def test_detect_low_priority_prefix(which_map, run_factory, expected):
    prefix = detect_low_priority_prefix(
        os_name="posix",
        which=_which_from(which_map),
        run=run_factory(),
    )
    assert prefix == expected


def test_detect_prefix_on_windows_and_popen_kwargs():
    def which(_name):
        return "/usr/bin/ionice"

    def run(*_args, **_kwargs):
        raise AssertionError("windows must not probe ionice")

    assert detect_low_priority_prefix(os_name="nt", which=which, run=run) == []
    expected_flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)
    assert popen_kwargs("nt") == {"creationflags": expected_flags}
    assert popen_kwargs("posix") == {}


def test_popen_kwargs_respects_enabled_switch():
    expected_flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)
    assert popen_kwargs("nt", enabled=False) == {}
    assert popen_kwargs("nt", enabled=True) == {"creationflags": expected_flags}
    assert popen_kwargs("posix", enabled=True) == {}
    assert popen_kwargs("posix", enabled=False) == {}


def test_low_priority_prefix_caches_detection(monkeypatch):
    remux.reset_cache()
    calls = []

    def fake_detect(*_args, **_kwargs):
        calls.append(1)
        return ["nice", "-n", "19"]

    monkeypatch.setattr(remux, "detect_low_priority_prefix", fake_detect)
    try:
        assert low_priority_prefix() == ["nice", "-n", "19"]
        assert low_priority_prefix() == ["nice", "-n", "19"]
        assert len(calls) == 1
    finally:
        remux.reset_cache()


def test_failure_keeps_source_ts(tmp_path):
    src = tmp_path / "a.ts"
    src.write_bytes(b"fake-ts")
    removed = []
    slept = []
    process = _FakeProcess(output=b"", returncode=1)

    with pytest.raises(subprocess.CalledProcessError):
        run_ffmpeg_then_delete(
            ["ffmpeg", "-i", str(src)],
            str(src),
            True,
            popen=_popen_returning(process),
            remove=removed.append,
            sleep=slept.append,
        )

    assert src.exists()
    assert removed == []
    assert slept == []


def test_ffmpeg_failure_never_removes_source(tmp_path):
    src = tmp_path / "keep.ts"
    src.write_bytes(b"fake-ts")
    removed = []
    slept = []
    process = _FakeProcess(output=b"", returncode=1)

    with pytest.raises(subprocess.CalledProcessError):
        run_ffmpeg_then_delete(
            ["ffmpeg", "-i", str(src)],
            str(src),
            True,
            popen=_popen_returning(process),
            remove=removed.append,
            sleep=slept.append,
        )

    assert src.exists()
    assert removed == []
    assert slept == []


def test_success_deletes_source_when_requested(tmp_path):
    src = tmp_path / "b.ts"
    src.write_bytes(b"fake-ts")
    removed = []
    slept = []
    process = _FakeProcess(output=b"ok", returncode=0)

    def fake_remove(path):
        removed.append(path)
        os.remove(path)

    result = run_ffmpeg_then_delete(
        ["ffmpeg", "-i", str(src)],
        str(src),
        True,
        popen=_popen_returning(process),
        remove=fake_remove,
        sleep=slept.append,
    )

    assert result == b"ok"
    assert not src.exists()
    assert removed == [str(src)]
    assert slept == [1]


def test_success_keeps_source_when_delete_disabled(tmp_path):
    src = tmp_path / "c.ts"
    src.write_bytes(b"fake-ts")
    removed = []
    process = _FakeProcess(output=b"ok", returncode=0)

    run_ffmpeg_then_delete(
        ["ffmpeg", "-i", str(src)],
        str(src),
        False,
        popen=_popen_returning(process),
        remove=removed.append,
        sleep=lambda _seconds: None,
    )

    assert src.exists()
    assert removed == []


def test_timeout_terminates_process_and_keeps_source(tmp_path, monkeypatch):
    fake_logger = _RecordingLogger()
    monkeypatch.setattr(remux, "logger", fake_logger)
    src = tmp_path / "hang.ts"
    src.write_bytes(b"fake-ts")
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        run_ffmpeg_then_delete(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            str(src),
            True,
            timeout=0.5,
            sleep=lambda _seconds: None,
        )

    assert time.monotonic() - started < 10
    assert src.exists()
    assert "转封装超时" in fake_logger.text()
    assert "原文件保留" in fake_logger.text()
    assert "hang.ts" in fake_logger.text()


def test_timeout_kills_process_when_terminate_wait_times_out(tmp_path, monkeypatch):
    fake_logger = _RecordingLogger()
    monkeypatch.setattr(remux, "logger", fake_logger)
    src = tmp_path / "stuck.ts"
    src.write_bytes(b"fake-ts")
    process = _WaitTimesOutProcess()

    with pytest.raises(subprocess.TimeoutExpired):
        run_ffmpeg_then_delete(
            ["ffmpeg", "-i", str(src)],
            str(src),
            False,
            timeout=1,
            popen=_popen_returning(process),
        )

    assert process.terminated is True
    assert process.killed is True
    assert src.exists()


def test_timeout_does_not_delete_existing_partial_output(tmp_path, monkeypatch):
    fake_logger = _RecordingLogger()
    monkeypatch.setattr(remux, "logger", fake_logger)
    src = tmp_path / "partial.ts"
    src.write_bytes(b"fake-ts")
    partial = tmp_path / "partial.mp4"
    partial.write_bytes(b"half-done")

    with pytest.raises(subprocess.TimeoutExpired):
        run_ffmpeg_then_delete(
            [sys.executable, "-c", "import time; time.sleep(60)", str(partial)],
            str(src),
            True,
            timeout=0.5,
            sleep=lambda _seconds: None,
        )

    assert partial.exists()
    assert src.exists()
    assert "可能是半成品，未删除" in fake_logger.text()
    assert str(partial) in fake_logger.text()


def test_timeout_failure_does_not_block_queue():
    executed = []

    def runner(path, _delete_origin):
        executed.append(path)
        if path == "first":
            raise subprocess.TimeoutExpired("ffmpeg", 1)
        return True

    remux_queue = RemuxQueue(1, runner)
    assert remux_queue.submit("first", False) is True
    assert remux_queue.submit("second", False) is True
    assert remux_queue.join(TIMEOUT) is True

    assert executed == ["first", "second"]
    assert remux_queue.pending() == []


def test_real_ffmpeg_remux_with_concurrency_limit(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not available")

    source = tmp_path / "source.ts"
    generate_command = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "testsrc=d=1",
        "-f", "lavfi", "-i", "sine=d=1",
        "-c:v", "mpeg2video", "-c:a", "mp2",
        "-f", "mpegts", str(source),
    ]
    try:
        subprocess.run(
            generate_command,
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("failed to generate a test ts with ffmpeg")

    ts_paths = []
    for index in range(4):
        ts_path = tmp_path / f"segment-{index}.ts"
        shutil.copyfile(source, ts_path)
        ts_paths.append(ts_path)

    lock = threading.Lock()
    state = {"current": 0, "max": 0}

    def runner(ts_path, delete_origin):
        mp4_path = ts_path.rsplit(".", maxsplit=1)[0] + ".mp4"
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        try:
            run_ffmpeg_then_delete(
                [ffmpeg, "-y", "-i", ts_path, "-c", "copy", "-f", "mp4", mp4_path],
                ts_path,
                delete_origin,
                sleep=lambda _seconds: None,
                timeout=30,
            )
        finally:
            with lock:
                state["current"] -= 1

    remux_queue = RemuxQueue(2, runner)
    for ts_path in ts_paths:
        assert remux_queue.submit(str(ts_path), True) is True

    assert remux_queue.join(30) is True
    for ts_path in ts_paths:
        assert (tmp_path / (ts_path.stem + ".mp4")).exists()
        assert not ts_path.exists()
    assert state["max"] <= 2


def test_concurrent_submit_of_24_tasks_respects_limit():
    lock = threading.Lock()
    state = {"current": 0, "max": 0, "done": 0}

    def runner(_path, _delete_origin):
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        time.sleep(0.02)
        with lock:
            state["current"] -= 1
            state["done"] += 1

    remux_queue = RemuxQueue(2, runner)
    barrier = threading.Barrier(24)
    results = []

    def submit_one(index):
        barrier.wait(timeout=TIMEOUT)
        result = remux_queue.submit(f"/tmp/concurrent-{index}.ts", False)
        with lock:
            results.append(result)

    threads = [
        threading.Thread(target=submit_one, args=(index,), daemon=True)
        for index in range(24)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)
        assert not thread.is_alive()

    assert remux_queue.join(TIMEOUT) is True
    assert len(results) == 24
    assert all(results)
    assert state["done"] == 24
    assert state["max"] == 2
