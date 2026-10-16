# -*- encoding: utf-8 -*-
import re
import threading

from loguru import logger

from src.douyin_danmu.fetcher import DouyinDanmuRecorder
from src.douyin_danmu.xml_to_ass import convert_xml_template_to_ass_files

_danmu_recorders: dict[str, DouyinDanmuRecorder] = {}
_danmu_lock = threading.Lock()


def danmu_path_from_video(save_file_path: str) -> str:
    """与视频同级、同主文件名；分段时保留 _%03d 占位符。"""
    if '%03d' in save_file_path:
        base = save_file_path.rsplit('.', 1)[0]
        return f"{base}.xml"
    return f"{save_file_path.rsplit('.', 1)[0]}.xml"


def start_douyin_danmu_recording(
    enabled: bool,
    record_name: str,
    record_url: str,
    port_info: dict,
    save_file_path: str,
    dy_cookie: str,
    split_video_by_time: bool,
    split_time: str,
    include_member: bool,
    include_like: bool,
) -> None:
    if not enabled:
        return
    room_id = str(port_info.get('room_id') or '')
    web_rid = str(port_info.get('web_rid') or '')
    if not room_id and not web_rid:
        logger.warning(f"[{record_name}] 抖音弹幕：无法获取 room_id，已跳过")
        return

    danmu_template = danmu_path_from_video(save_file_path)
    segment_seconds = int(split_time) if split_video_by_time else None
    stop_event = threading.Event()

    recorder = DouyinDanmuRecorder(
        room_id=room_id,
        danmu_path_template=danmu_template,
        cookie=dy_cookie,
        web_rid=web_rid,
        segment_seconds=segment_seconds,
        include_member=include_member,
        include_like=include_like,
        stop_event=stop_event,
    )

    with _danmu_lock:
        old = _danmu_recorders.pop(record_name, None)
        if old:
            old.stop()
        _danmu_recorders[record_name] = recorder

    if not recorder.start():
        with _danmu_lock:
            _danmu_recorders.pop(record_name, None)


def stop_douyin_danmu_recording(record_name: str) -> None:
    with _danmu_lock:
        recorder = _danmu_recorders.pop(record_name, None)
    if recorder:
        danmu_template = recorder.danmu_path_template
        recorder.stop()
        convert_xml_template_to_ass_files(danmu_template)
        logger.debug(f"[{record_name}] 抖音弹幕录制已停止")


def extract_douyin_web_rid(record_url: str) -> str:
    if 'live.douyin.com/' not in record_url:
        return ''
    part = record_url.split('?')[0].split('live.douyin.com/')[-1].strip('/')
    if part and re.match(r'^[\w.-]+$', part):
        return part
    return ''
