# -*- coding: utf-8 -*-
"""录制完成后的 ffmpeg 转封装：低优先级与限并发。"""

import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from src.utils import logger

_priority_prefix_cache: list[str] | None = None


def detect_low_priority_prefix(
    os_name: str = os.name,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> list[str]:
    """无缓存地探测可用的低优先级命令前缀。"""
    if os_name == "nt":
        return []
    prefix: list[str] = []
    if which("ionice"):
        try:
            result = run(
                ["ionice", "-c3", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return_code = getattr(result, "returncode", result)
            ionice_ok = return_code == 0
        except Exception:
            ionice_ok = False
        if ionice_ok:
            prefix.extend(["ionice", "-c3"])
    if which("nice"):
        prefix.extend(["nice", "-n", "19"])
    return prefix


def low_priority_prefix(
    os_name: str = os.name,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> list[str]:
    """返回缓存后的低优先级命令前缀；首次探测时记录一行日志。"""
    global _priority_prefix_cache
    if _priority_prefix_cache is None:
        _priority_prefix_cache = detect_low_priority_prefix(os_name, which, run)
        if "ionice" in _priority_prefix_cache and "nice" in _priority_prefix_cache:
            logger.info("转封装使用低优先级：ionice -c3 + nice -n 19")
        elif "ionice" in _priority_prefix_cache:
            logger.info("转封装使用低优先级：ionice -c3")
        elif "nice" in _priority_prefix_cache:
            logger.info("转封装使用低优先级：nice -n 19")
        else:
            logger.info("转封装未启用低优先级（未找到 ionice/nice）")
    return list(_priority_prefix_cache)


def reset_cache() -> None:
    """清空低优先级前缀缓存，仅供测试使用。"""
    global _priority_prefix_cache
    _priority_prefix_cache = None


def popen_kwargs(os_name: str = os.name) -> dict[str, Any]:
    """Windows 下让 ffmpeg 进程以低于普通优先级运行。"""
    if os_name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)}
    return {}


def run_ffmpeg_then_delete(
    cmd: list[str],
    src_path: str,
    delete_origin: bool,
    *,
    check_output: Callable[..., Any] = subprocess.check_output,
    remove: Callable[[str], Any] = os.remove,
    sleep: Callable[[float], Any] = time.sleep,
    **kwargs: Any,
) -> Any:
    """执行 ffmpeg；只有成功返回后才删除原文件，失败则原样保留。"""
    output = check_output(cmd, stderr=subprocess.STDOUT, **kwargs)
    if delete_origin and os.path.exists(src_path):
        sleep(1)
        remove(src_path)
    return output


class RemuxQueue:
    """固定 worker 数的转封装队列；max_concurrency <= 0 时退化为不限流。"""

    def __init__(self, max_concurrency: int, runner: Callable[[str, bool], None]) -> None:
        self.max_concurrency = max_concurrency
        self.runner = runner
        self._queue: queue.Queue[tuple[str, bool]] = queue.Queue()
        self._lock = threading.Lock()
        self._done_condition = threading.Condition(self._lock)
        self._start_lock = threading.Lock()
        self._pending: set[str] = set()
        self._running = 0
        self._started = False
        self._workers: list[threading.Thread] = []

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._started = True
            for index in range(self.max_concurrency):
                worker = threading.Thread(
                    target=self._worker,
                    name=f"remux-worker-{index}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def _worker(self) -> None:
        while True:
            path, delete_origin = self._queue.get()
            try:
                self._run_task(path, delete_origin)
            finally:
                self._queue.task_done()

    def _run_task(self, path: str, delete_origin: bool) -> None:
        started_at = time.monotonic()
        with self._lock:
            self._running += 1
        try:
            self.runner(path, delete_origin)
            elapsed = time.monotonic() - started_at
            name = os.path.basename(path)
            size_text = ""
            try:
                mp4_path = path.rsplit(".", maxsplit=1)[0] + ".mp4"
                size_gb = os.path.getsize(mp4_path) / (1024 ** 3)
                size_text = f" {size_gb:.2f}GB"
            except OSError:
                pass
            logger.info(f"转封装完成 {name}{size_text} 用时 {elapsed:.1f}s")
        except Exception as e:
            logger.error(f"转封装失败 {os.path.basename(path)}: {e}")
        finally:
            with self._lock:
                self._running -= 1
                self._pending.discard(path)
                self._done_condition.notify_all()

    def submit(self, path: str, delete_origin: bool) -> bool:
        with self._lock:
            if path in self._pending:
                return False
            self._pending.add(path)

        if self.max_concurrency <= 0:
            threading.Thread(
                target=self._run_task,
                args=(path, delete_origin),
                name="remux-unbounded",
                daemon=True,
            ).start()
        else:
            self._ensure_started()
            self._queue.put((path, delete_origin))

        with self._lock:
            queued = max(0, len(self._pending) - self._running)
            running = self._running
        logger.info(
            f"已加入转封装队列：{os.path.basename(path)}"
            f"（排队 {queued}，进行中 {running}/{max(0, self.max_concurrency)}）"
        )
        return True

    def pending(self) -> list[str]:
        with self._lock:
            return list(self._pending)

    def join(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._done_condition:
            while self._pending:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._done_condition.wait(remaining)
            return True
