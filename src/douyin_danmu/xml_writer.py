# -*- encoding: utf-8 -*-
import os
import threading
import time
import xml.sax.saxutils as saxutils
from pathlib import Path


class XmlDanmuWriter:
    """Bilibili-compatible XML for PotPlayer, 弹弹Play, etc."""

    XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<i>\n'
    XML_FOOTER = '</i>\n'

    def __init__(
        self,
        path_template: str,
        segment_seconds: int | None = None,
    ):
        self.path_template = path_template
        self.segment_seconds = segment_seconds
        self._lock = threading.Lock()
        self._segment_index = 0
        self._segment_start = time.time()
        self._file = None
        self._current_path: str | None = None
        self._written_paths: list[str] = []
        self._open_segment(0)

    def _path_for_index(self, index: int) -> str:
        if '%03d' in self.path_template:
            return self.path_template % index
        return self.path_template

    def _close_current_segment(self) -> None:
        if self._file:
            self._file.write(self.XML_FOOTER)
            self._file.flush()
            self._file.close()
            self._file = None
            if self._current_path and self._current_path not in self._written_paths:
                self._written_paths.append(self._current_path)

    def _open_segment(self, index: int) -> None:
        if self._file:
            self._close_current_segment()

        path = self._path_for_index(index)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._file = open(path, 'a', encoding='utf-8')
        if is_new:
            self._file.write(self.XML_HEADER)
        self._current_path = path
        self._segment_index = index
        self._segment_start = time.time()

    def _maybe_rotate(self) -> None:
        if not self.segment_seconds:
            return
        if time.time() - self._segment_start >= self.segment_seconds:
            self._open_segment(self._segment_index + 1)

    def _relative_seconds(self) -> float:
        return max(0.0, time.time() - self._segment_start)

    def write(
        self,
        content: str,
        msg_type: int = 1,
        color: int = 16777215,
        font_size: int = 25,
        user_hash: str = '0',
    ) -> None:
        if not content or self._file is None:
            return
        with self._lock:
            self._maybe_rotate()
            t = self._relative_seconds()
            ts_ms = int(time.time() * 1000)
            esc = saxutils.escape(content)
            p = f"{t:.3f},{msg_type},{font_size},{color},{ts_ms},0,{user_hash},0"
            self._file.write(f'<d p="{p}">{esc}</d>\n')
            self._file.flush()

    def close(self) -> None:
        from src.douyin_danmu.xml_to_ass import convert_xml_to_ass

        with self._lock:
            self._close_current_segment()
            for xml_path in self._written_paths:
                convert_xml_to_ass(xml_path)
