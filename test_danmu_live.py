#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时测试：抖音弹幕连接与写入"""
import asyncio
import configparser
import os
import sys
import time
from pathlib import Path

script_path = Path(__file__).resolve().parent
sys.path.insert(0, str(script_path))

from src import spider, stream
from src.douyin_danmu.fetcher import DouyinDanmuRecorder

TEST_URL = "https://live.douyin.com/493123872209"
TEST_SECONDS = 45
OUT_DIR = script_path / "downloads" / "抖音直播" / "_danmu_test"


def read_dy_cookie() -> str:
    cfg = configparser.RawConfigParser()
    cfg.read(script_path / "config" / "config.ini", encoding="utf-8-sig")
    return cfg.get("Cookie", "抖音cookie", fallback="")


async def fetch_room_info(cookie: str) -> dict:
    json_data = await spider.get_douyin_web_stream_data(url=TEST_URL, cookies=cookie)
    port_info = await stream.get_douyin_stream_url(json_data, "原画", None, TEST_URL)
    return json_data, port_info


def main() -> int:
    cookie = read_dy_cookie()
    print(f"测试直播间: {TEST_URL}")
    print("正在获取直播间信息...")

    try:
        json_data, port_info = asyncio.run(fetch_room_info(cookie))
    except Exception as e:
        print(f"[失败] 获取直播信息: {e}")
        return 1

    anchor = port_info.get("anchor_name") or json_data.get("anchor_name") or "unknown"
    is_live = port_info.get("is_live", False)
    room_id = port_info.get("room_id") or json_data.get("id_str") or json_data.get("id")
    web_rid = port_info.get("web_rid") or "911629098736"
    title = port_info.get("title") or json_data.get("title", "")

    print(f"主播: {anchor}")
    print(f"标题: {title}")
    print(f"开播: {'是' if is_live else '否'}")
    print(f"room_id: {room_id}")
    print(f"web_rid: {web_rid}")

    if not is_live:
        print("[提示] 当前未开播，弹幕 WebSocket 可能无法收到消息，仍将尝试连接 45 秒...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in anchor)[:40]
    xml_path = OUT_DIR / f"{safe_name}_{ts}.xml"

    recorder = DouyinDanmuRecorder(
        room_id=str(room_id),
        danmu_path_template=str(xml_path),
        cookie=cookie,
        web_rid=str(web_rid),
        segment_seconds=None,
        include_member=False,
        include_like=False,
    )

    print(f"弹幕输出: {xml_path}")
    time.sleep(2)
    if not recorder.start():
        print("[失败] 弹幕录制启动失败")
        return 1

    print(f"已连接，采集 {TEST_SECONDS} 秒...")
    try:
        for i in range(TEST_SECONDS):
            time.sleep(1)
            if i % 10 == 9 and xml_path.exists():
                size = xml_path.stat().st_size
                print(f"  ...{i + 1}s, 文件大小 {size} bytes")
    finally:
        recorder.stop()

    if xml_path.exists():
        size = xml_path.stat().st_size
        with open(xml_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        danmu_count = sum(1 for ln in lines if ln.strip().startswith("<d "))
        print(f"\n[完成] XML 已生成: {xml_path}")
        print(f"文件大小: {size} bytes, 弹幕条数(约): {danmu_count}")
        if danmu_count > 0:
            sample = [ln for ln in lines if ln.strip().startswith("<d ")][:3]
            print("样例:")
            for s in sample:
                print(" ", s.strip()[:120])
            return 0
        print("[警告] 文件已创建但未收到弹幕（可能未开播或连接被限制）")
        return 2
    print("[失败] 未生成弹幕文件")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
