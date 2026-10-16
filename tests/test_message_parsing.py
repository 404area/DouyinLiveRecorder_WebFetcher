"""Protobuf danmu dispatch without a websocket connection."""

import gzip
import threading
from dataclasses import dataclass
from unittest.mock import Mock

import websocket

from src.douyin_danmu.fetcher import DouyinDanmuRecorder
from src.douyin_danmu.protobuf.douyin import (
    ChatMessage,
    ControlMessage,
    EmojiChatMessage,
    FansclubMessage,
    GiftMessage,
    GiftStruct,
    LikeMessage,
    MemberMessage,
    Message,
    PushFrame,
    Response,
    SocialMessage,
    User,
)


@dataclass
class Written:
    content: str
    msg_type: int
    color: int
    font_size: int
    user_hash: str


class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[Written] = []
        self.path_template = "unused.xml"

    def write(
        self,
        content: str,
        msg_type: int = 1,
        color: int = 16777215,
        font_size: int = 25,
        user_hash: str = "0",
    ) -> None:
        self.rows.append(Written(content, msg_type, color, font_size, user_hash))

    def close(self) -> None:
        return None


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send(self, data, opcode=None) -> None:
        self.sent.append((data, opcode))


def make_recorder(*, include_like: bool = False, include_member: bool = False):
    recorder = DouyinDanmuRecorder.__new__(DouyinDanmuRecorder)
    recorder.writer = FakeWriter()
    recorder.include_like = include_like
    recorder.include_member = include_member
    recorder.stop_event = threading.Event()
    recorder._running = True
    recorder.room_id = "1"
    recorder.ws = None
    recorder.stop = Mock()
    return recorder


def frame_bytes(messages: list[Message], *, need_ack: bool = False, log_id: int = 99) -> bytes:
    response = Response(
        messages_list=messages,
        need_ack=need_ack,
        internal_ext="ie",
    )
    frame = PushFrame(log_id=log_id, payload=gzip.compress(response.SerializeToString()))
    return frame.SerializeToString()


def user(uid: int, name: str, gender: int = 0) -> User:
    return User(id=uid, nick_name=name, gender=gender)


def test_user_hash_uses_last_eight_digits():
    recorder = make_recorder()
    assert recorder._user_hash(123456789012) == "56789012"
    assert recorder._user_hash(42) == "42"
    assert recorder._user_hash(0) == "0"
    assert recorder._user_hash(None) == "0"


def test_chat_gift_and_social_messages_are_formatted():
    recorder = make_recorder()
    uid = 123456789012
    chat = ChatMessage(user=user(uid, "甲"), content="你好<&>").SerializeToString()
    gift = GiftMessage(
        user=user(uid, "乙"),
        gift=GiftStruct(name="玫瑰"),
        combo_count=3,
    ).SerializeToString()
    social = SocialMessage(user=user(7, "丙")).SerializeToString()
    payload = frame_bytes(
        [
            Message(method="WebcastChatMessage", payload=chat),
            Message(method="WebcastGiftMessage", payload=gift),
            Message(method="WebcastSocialMessage", payload=social),
            Message(method="WebcastUnknown", payload=b"ignored"),
        ]
    )
    recorder._ws_on_message(FakeSocket(), payload)

    assert [row.content for row in recorder.writer.rows] == [
        f"[{uid}]甲: 你好<&>",
        "乙 送出了 玫瑰x3",
        "[7]丙 关注了主播",
    ]
    assert recorder.writer.rows[0].user_hash == "56789012"
    assert recorder.writer.rows[0].msg_type == 1
    assert recorder.writer.rows[0].color == 16777215
    assert recorder.writer.rows[1].color == 0xFF66CC
    assert recorder.writer.rows[2].msg_type == 4
    assert recorder.writer.rows[2].color == 0x66FF66


def test_like_and_member_respect_include_flags():
    quiet = make_recorder(include_like=False, include_member=False)
    like = LikeMessage(user=user(9, "丁"), count=4).SerializeToString()
    member = MemberMessage(user=user(8, "戊", gender=1)).SerializeToString()
    quiet._ws_on_message(
        FakeSocket(),
        frame_bytes(
            [
                Message(method="WebcastLikeMessage", payload=like),
                Message(method="WebcastMemberMessage", payload=member),
            ]
        ),
    )
    assert quiet.writer.rows == []

    loud = make_recorder(include_like=True, include_member=True)
    loud._parse_like_msg(like)
    loud._parse_member_msg(member)
    loud._parse_member_msg(MemberMessage(user=user(8, "己", gender=0)).SerializeToString())
    loud._parse_member_msg(MemberMessage(user=user(8, "庚", gender=2)).SerializeToString())
    assert [row.content for row in loud.writer.rows] == [
        "丁 点了4个赞",
        "[8][男]戊 进入了直播间",
        "[8][女]己 进入了直播间",
        "[8][未知]庚 进入了直播间",
    ]
    assert loud.writer.rows[0].color == 0x66CCFF
    assert loud.writer.rows[1].msg_type == 4
    assert loud.writer.rows[1].color == 0xAAAAAA


def test_emoji_and_fansclub_text():
    recorder = make_recorder()
    recorder._parse_emoji_chat_msg(
        EmojiChatMessage(user=user(1, "辛"), default_content="哈哈", emoji_id=9).SerializeToString()
    )
    recorder._parse_emoji_chat_msg(EmojiChatMessage(user=user(1, "辛"), emoji_id=9).SerializeToString())
    recorder._parse_fansclub_msg(FansclubMessage(content="升级了").SerializeToString())
    assert [row.content for row in recorder.writer.rows] == [
        "[辛]哈哈",
        "[辛][表情]9",
        "【粉丝团】升级了",
    ]
    assert recorder.writer.rows[2].color == 0xFFAA00
    assert recorder.writer.rows[2].user_hash == "0"


def test_control_status_3_stops_recording():
    recorder = make_recorder()
    recorder._parse_control_msg(ControlMessage(status=1).SerializeToString())
    recorder.stop.assert_not_called()
    recorder._parse_control_msg(ControlMessage(status=3).SerializeToString())
    recorder.stop.assert_called_once()


def test_bad_payload_is_skipped_and_later_messages_remain():
    recorder = make_recorder()
    good = FansclubMessage(content="还在").SerializeToString()
    recorder._ws_on_message(
        FakeSocket(),
        frame_bytes(
            [
                Message(method="WebcastChatMessage", payload=b"\xff\xff"),
                Message(method="WebcastFansclubMessage", payload=good),
            ]
        ),
    )
    assert [row.content for row in recorder.writer.rows] == ["【粉丝团】还在"]


def test_malformed_frame_and_stop_event_write_nothing():
    recorder = make_recorder()
    recorder._ws_on_message(FakeSocket(), b"this is not a frame")
    recorder._ws_on_message(FakeSocket(), gzip.compress(b"still not protobuf"))
    assert recorder.writer.rows == []

    chat = ChatMessage(user=user(1, "甲"), content="晚了").SerializeToString()
    recorder.stop_event.set()
    recorder._ws_on_message(
        FakeSocket(),
        frame_bytes([Message(method="WebcastChatMessage", payload=chat)]),
    )
    assert recorder.writer.rows == []


def test_need_ack_sends_binary_frame():
    recorder = make_recorder()
    socket = FakeSocket()
    chat = ChatMessage(user=user(1, "甲"), content="hi").SerializeToString()
    recorder._ws_on_message(
        socket,
        frame_bytes(
            [Message(method="WebcastChatMessage", payload=chat)],
            need_ack=True,
            log_id=42,
        ),
    )
    assert len(socket.sent) == 1
    data, opcode = socket.sent[0]
    assert opcode == websocket.ABNF.OPCODE_BINARY
    ack = PushFrame().parse(data)
    assert ack.log_id == 42
    assert ack.payload_type == "ack"
    assert ack.payload == b"ie"


def test_parsed_chat_is_written_to_xml(tmp_path, monkeypatch):
    from tests.conftest import FakeClock

    clock = FakeClock(1_700_000_000.0)
    monkeypatch.setattr("src.douyin_danmu.xml_writer.time.time", clock.time)
    path = tmp_path / "live.xml"
    recorder = DouyinDanmuRecorder(
        room_id="1",
        danmu_path_template=str(path),
        cookie="ttwid=local",
    )
    try:
        clock.advance(0.5)
        uid = 123456789012
        chat = ChatMessage(user=user(uid, "甲"), content="a<b>&c").SerializeToString()
        recorder._ws_on_message(
            FakeSocket(),
            frame_bytes([Message(method="WebcastChatMessage", payload=chat)]),
        )
        text = path.read_text(encoding="utf-8")
        assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert 'p="0.500,1,25,16777215,1700000000500,0,56789012,0"' in text
        assert "a&lt;b&gt;&amp;c" in text
    finally:
        recorder.stop()
    assert path.with_suffix(".ass").is_file()
