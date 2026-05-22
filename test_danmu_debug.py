#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import configparser
import sys
import time
from pathlib import Path

script_path = Path(__file__).resolve().parent
sys.path.insert(0, str(script_path))

from src.douyin_danmu.fetcher import DouyinDanmuRecorder
from src.douyin_danmu.signature import generate_signature

ROOM_ID = "7642208316541651747"
WEB_RID = "493123872209"
TEST_SECONDS = 30

cfg = configparser.RawConfigParser()
cfg.read(script_path / "config" / "config.ini", encoding="utf-8-sig")
cookie = cfg.get("Cookie", "抖音cookie", fallback="")

wss_base = (
    "wss://webcast100-ws-web-lq.douyin.com/webcast/im/push/v2/?app_name=douyin_web"
    f"&room_id={ROOM_ID}&live_id=1&aid=6383"
)
try:
    sig = generate_signature(wss_base)
    print(f"signature ok, len={len(sig)}")
except Exception as e:
    print(f"signature FAIL: {e}")
    sys.exit(1)

out = script_path / "downloads" / "抖音直播" / "_danmu_test" / "debug.xml"
recorder = DouyinDanmuRecorder(
    room_id=ROOM_ID,
    danmu_path_template=str(out),
    cookie=cookie,
    web_rid=WEB_RID,
)
print(f"ttwid: {bool(recorder.ttwid)}")
recorder.start()
time.sleep(TEST_SECONDS)
recorder.stop()
if out.exists():
    text = out.read_text(encoding="utf-8")
    n = text.count("<d ")
    print(f"file={out}, size={out.stat().st_size}, danmu={n}")
    print(text[:500])
else:
    print("no file")
