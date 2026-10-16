"""Cookie, path, page, and signature helpers. No network."""

import socket
import string
from unittest.mock import Mock

import pytest
import requests
from src.douyin_danmu.ac_signature import get__ac_signature
from src.douyin_danmu.fetcher import (
    DouyinDanmuRecorder,
    generate_ms_token,
    parse_ttwid_from_cookie,
)
from src.douyin_danmu.manager import (
    danmu_path_from_video,
    extract_douyin_web_rid,
    start_douyin_danmu_recording,
    stop_douyin_danmu_recording,
)
from src.douyin_danmu.signature import generate_signature


def test_network_guard_blocks_direct_and_http_connections():
    with socket.socket() as client:
        with pytest.raises(RuntimeError, match="network is disabled"):
            client.connect(("127.0.0.1", 9))

    with pytest.raises(RuntimeError, match="network is disabled"):
        requests.get("https://example.com", timeout=0.01)


def test_parse_ttwid_from_cookie():
    assert parse_ttwid_from_cookie("") is None
    assert parse_ttwid_from_cookie("session=abc") is None
    assert parse_ttwid_from_cookie("ttwid=abc") == "abc"
    assert parse_ttwid_from_cookie(" a=1; ttwid=xyz ; b=2 ") == "xyz"
    assert parse_ttwid_from_cookie("ttwid=") == ""
    assert parse_ttwid_from_cookie("nottwid=no") is None


def test_generate_ms_token_length_and_alphabet():
    alphabet = set(string.ascii_letters + string.digits + "-_")
    token = generate_ms_token()
    assert len(token) == 107
    assert set(token) <= alphabet
    assert generate_ms_token(0) == ""
    assert len(generate_ms_token(8)) == 8


def test_extract_douyin_web_rid():
    assert extract_douyin_web_rid("https://live.douyin.com/493123872209") == "493123872209"
    assert extract_douyin_web_rid("https://live.douyin.com/abc_def?x=1") == "abc_def"
    assert extract_douyin_web_rid("https://live.douyin.com/name.1/") == "name.1"
    assert extract_douyin_web_rid("https://example.com/493") == ""
    assert extract_douyin_web_rid("https://live.douyin.com/") == ""
    assert extract_douyin_web_rid("https://live.douyin.com/a/b") == ""
    assert extract_douyin_web_rid("https://live.douyin.com/bad id") == ""


def test_danmu_path_from_video():
    assert danmu_path_from_video("/tmp/show.mp4") == "/tmp/show.xml"
    assert danmu_path_from_video("/tmp/show_%03d.flv") == "/tmp/show_%03d.xml"


def test_disabled_recording_does_not_register_or_fetch():
    from src.douyin_danmu import manager

    before = dict(manager._danmu_recorders)
    start_douyin_danmu_recording(
        False,
        "name",
        "https://live.douyin.com/1",
        {"room_id": "1"},
        "/tmp/video.mp4",
        "",
        False,
        "60",
        False,
        False,
    )
    assert manager._danmu_recorders == before
    stop_douyin_danmu_recording("missing-record")
    assert manager._danmu_recorders == before


def test_room_id_is_parsed_from_mocked_page(tmp_path):
    recorder = DouyinDanmuRecorder(
        room_id="",
        danmu_path_template=str(tmp_path / "out.xml"),
        cookie="ttwid=from-cookie",
        web_rid="webroom",
    )
    try:
        response = Mock()
        response.text = r'prefix roomId\":\"556677\" suffix'
        response.raise_for_status = Mock()
        recorder.session.get = Mock(return_value=response)

        assert recorder._resolve_room_id_from_page() == "556677"
        recorder.session.get.assert_called_once()
        called_url = recorder.session.get.call_args.args[0]
        assert called_url.endswith("webroom")

        response.text = "no room here"
        assert recorder._resolve_room_id_from_page() is None

        recorder.session.get.side_effect = OSError("offline")
        assert recorder._resolve_room_id_from_page() is None

        recorder.web_rid = ""
        recorder.session.get.side_effect = None
        recorder.session.get.reset_mock()
        assert recorder._resolve_room_id_from_page() is None
        recorder.session.get.assert_not_called()
    finally:
        recorder.stop()


def test_ac_signature_is_deterministic():
    ua = "Mozilla/5.0"
    nonce = "0123407cc00a9e438deb4"
    first = get__ac_signature("live.douyin.com", nonce, ua, 1700000000)
    again = get__ac_signature("live.douyin.com", nonce, ua, 1700000000)
    assert first == again
    assert first == "_02B4Z6wo00f01g66HWwAAIDChH2vDnyw7vIOmhnAAOcL5d"
    other = get__ac_signature("live.douyin.com", nonce, ua, 1700000001)
    assert other != first


def test_signature_hashes_canonical_params_and_signs_locally(monkeypatch):
    import hashlib

    from src.douyin_danmu import signature as signature_mod

    captured: dict[str, bytes] = {}
    real_md5 = hashlib.md5

    class _RecordingMd5:
        def __init__(self) -> None:
            self._md5 = real_md5()

        def update(self, data: bytes) -> None:
            captured["data"] = data
            self._md5.update(data)

        def hexdigest(self) -> str:
            return self._md5.hexdigest()

    monkeypatch.setattr(signature_mod.hashlib, "md5", _RecordingMd5)
    wss = (
        "wss://example.test/webcast/im/push/v2/"
        "?live_id=1&aid=6383&room_id=42&identity=audience"
    )
    token = generate_signature(wss)
    assert captured["data"] == (
        b"live_id=1,aid=6383,version_code=,webcast_sdk_version=,room_id=42,"
        b"sub_room_id=,sub_channel_id=,did_rule=,user_unique_id=,"
        b"device_platform=,device_type=,ac=,identity=audience"
    )
    # sign.js mixes in the current time, so the token itself is not pinned.
    assert isinstance(token, str) and token
    assert " " not in token
