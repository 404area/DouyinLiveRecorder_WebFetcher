# -*- encoding: utf-8 -*-
import gzip
import random
import re
import string
import threading
import time
from pathlib import Path

import requests
import websocket

from loguru import logger
from src.douyin_danmu.protobuf.douyin import (
    ChatMessage,
    ControlMessage,
    EmojiChatMessage,
    FansclubMessage,
    GiftMessage,
    LikeMessage,
    MemberMessage,
    PushFrame,
    Response,
    RoomMessage,
    RoomRankMessage,
    RoomStatsMessage,
    RoomStreamAdaptationMessage,
    RoomUserSeqMessage,
    SocialMessage,
)
from src.douyin_danmu.signature import generate_signature
from src.douyin_danmu.xml_writer import XmlDanmuWriter

_MODULE_DIR = Path(__file__).resolve().parent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)


def generate_ms_token(length: int = 107) -> str:
    base_str = string.ascii_letters + string.digits + '-_'
    return ''.join(random.choice(base_str) for _ in range(length))


def parse_ttwid_from_cookie(cookie: str) -> str | None:
    if not cookie:
        return None
    for part in cookie.split(';'):
        part = part.strip()
        if part.startswith('ttwid='):
            return part.split('=', 1)[1]
    return None


class DouyinDanmuRecorder:
    def __init__(
        self,
        room_id: str,
        danmu_path_template: str,
        cookie: str = '',
        web_rid: str = '',
        segment_seconds: int | None = None,
        include_member: bool = False,
        include_like: bool = False,
        stop_event: threading.Event | None = None,
    ):
        self.room_id = str(room_id)
        self.web_rid = web_rid or ''
        self.cookie = cookie or ''
        self.stop_event = stop_event or threading.Event()
        self.include_member = include_member
        self.include_like = include_like
        self.session = requests.Session()
        self.live_url = "https://live.douyin.com/"
        self.__ttwid: str | None = None
        self.writer = XmlDanmuWriter(danmu_path_template, segment_seconds=segment_seconds)
        self.ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._running = False

    @property
    def ttwid(self) -> str | None:
        if self.__ttwid:
            return self.__ttwid
        parsed = parse_ttwid_from_cookie(self.cookie)
        if parsed:
            self.__ttwid = parsed
            return self.__ttwid
        try:
            response = self.session.get(self.live_url, headers={"User-Agent": USER_AGENT}, timeout=15)
            response.raise_for_status()
            self.__ttwid = response.cookies.get('ttwid')
        except Exception as err:
            logger.warning(f"获取 ttwid 失败: {err}")
        return self.__ttwid

    def _resolve_room_id_from_page(self) -> str | None:
        if not self.web_rid:
            return None
        url = self.live_url + self.web_rid
        headers = {
            "User-Agent": USER_AGENT,
            "cookie": f"ttwid={self.ttwid};msToken={generate_ms_token()}; __ac_nonce=0123407cc00a9e438deb4",
        }
        if self.cookie:
            headers["cookie"] = self.cookie
        try:
            response = self.session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            match = re.search(r'roomId\\":\\"(\d+)\\"', response.text)
            if match:
                return match.group(1)
        except Exception as err:
            logger.warning(f"从页面解析 room_id 失败: {err}")
        return None

    def start(self) -> bool:
        if not self.room_id:
            self.room_id = self._resolve_room_id_from_page() or ''
        if not self.room_id:
            logger.error("抖音弹幕：缺少 room_id，已跳过")
            return False
        if not self.ttwid:
            logger.error("抖音弹幕：缺少 ttwid，请检查 config.ini 中的抖音cookie")
            return False

        # 紧接直播流 API 请求后立刻连 WS 易被限流，短暂等待可提高连接成功率
        time.sleep(2)
        self._running = True
        self._ws_thread = threading.Thread(target=self._run_websocket, daemon=True)
        self._ws_thread.start()
        logger.info(f"抖音弹幕录制已启动 room_id={self.room_id}")
        return True

    def stop(self) -> None:
        self.stop_event.set()
        self._running = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.writer.close()

    @property
    def danmu_path_template(self) -> str:
        return self.writer.path_template

    def _run_websocket(self) -> None:
        wss = (
            "wss://webcast100-ws-web-lq.douyin.com/webcast/im/push/v2/?app_name=douyin_web"
            "&version_code=180800&webcast_sdk_version=1.0.14-beta.0"
            "&update_version_code=1.0.14-beta.0&compress=gzip&device_platform=web&cookie_enabled=true"
            "&screen_width=1536&screen_height=864&browser_language=zh-CN&browser_platform=Win32"
            "&browser_name=Mozilla"
            "&browser_version=5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,"
            "%20like%20Gecko)%20Chrome/126.0.0.0%20Safari/537.36"
            "&browser_online=true&tz_name=Asia/Shanghai"
            "&cursor=d-1_u-1_fh-7392091211001140287_t-1721106114633_r-1"
            f"&internal_ext=internal_src:dim|wss_push_room_id:{self.room_id}|wss_push_did:7319483754668557238"
            f"|first_req_ms:1721106114541|fetch_time:1721106114633|seq:1|wss_info:0-1721106114633-0-0|"
            f"wrds_v:7392094459690748497"
            f"&host=https://live.douyin.com&aid=6383&live_id=1&did_rule=3&endpoint=live_pc&support_wrds=1"
            f"&user_unique_id=7319483754668557238&im_path=/webcast/im/fetch/&identity=audience"
            f"&need_persist_msg_count=15&insert_task_id=&live_reason=&room_id={self.room_id}&heartbeatDuration=0"
        )
        signature = None
        for attempt in range(3):
            try:
                signature = generate_signature(wss)
                break
            except Exception as e:
                logger.warning(f"抖音弹幕 signature 第{attempt + 1}次失败: {e}")
                time.sleep(1)
        if not signature:
            logger.error("抖音弹幕 signature 计算失败，已停止")
            self.stop()
            return
        wss += f"&signature={signature}"

        headers = {
            "cookie": f"ttwid={self.ttwid}",
            "user-agent": USER_AGENT,
        }
        if self.cookie:
            headers["cookie"] = self.cookie

        self.ws = websocket.WebSocketApp(
            wss,
            header=headers,
            on_open=self._ws_on_open,
            on_message=self._ws_on_message,
            on_error=self._ws_on_error,
            on_close=self._ws_on_close,
        )
        try:
            self.ws.run_forever()
        except Exception as e:
            if self._running:
                logger.debug(f"抖音弹幕 WebSocket 结束: {e}")

    def _ws_on_open(self, _ws) -> None:
        self._heartbeat_thread = threading.Thread(target=self._send_heartbeat, daemon=True)
        self._heartbeat_thread.start()

    def _send_heartbeat(self) -> None:
        while not self.stop_event.is_set() and self._running:
            try:
                if self.ws:
                    heartbeat = PushFrame(payload_type='hb').SerializeToString()
                    self.ws.send(heartbeat, websocket.ABNF.OPCODE_PING)
            except Exception:
                break
            time.sleep(5)

    def _ws_on_message(self, ws, message) -> None:
        if self.stop_event.is_set():
            return
        try:
            package = PushFrame().parse(message)
            response = Response().parse(gzip.decompress(package.payload))
            if response.need_ack:
                ack = PushFrame(
                    log_id=package.log_id,
                    payload_type='ack',
                    payload=response.internal_ext.encode('utf-8'),
                ).SerializeToString()
                ws.send(ack, websocket.ABNF.OPCODE_BINARY)

            for msg in response.messages_list:
                if self.stop_event.is_set():
                    return
                handler = {
                    'WebcastChatMessage': self._parse_chat_msg,
                    'WebcastGiftMessage': self._parse_gift_msg,
                    'WebcastLikeMessage': self._parse_like_msg,
                    'WebcastMemberMessage': self._parse_member_msg,
                    'WebcastSocialMessage': self._parse_social_msg,
                    'WebcastRoomUserSeqMessage': self._parse_room_user_seq_msg,
                    'WebcastFansclubMessage': self._parse_fansclub_msg,
                    'WebcastControlMessage': self._parse_control_msg,
                    'WebcastEmojiChatMessage': self._parse_emoji_chat_msg,
                    'WebcastRoomStatsMessage': self._parse_room_stats_msg,
                    'WebcastRoomMessage': self._parse_room_msg,
                    'WebcastRoomRankMessage': self._parse_rank_msg,
                    'WebcastRoomStreamAdaptationMessage': self._parse_room_stream_adaptation_msg,
                }.get(msg.method)
                if handler:
                    try:
                        handler(msg.payload)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"抖音弹幕消息解析异常: {e}")

    def _ws_on_error(self, _ws, error) -> None:
        if self._running:
            logger.debug(f"抖音弹幕 WebSocket error: {error}")

    def _ws_on_close(self, _ws, *_args) -> None:
        self._running = False

    def _user_hash(self, user_id) -> str:
        return str(user_id)[-8:] if user_id else '0'

    def _parse_chat_msg(self, payload) -> None:
        message = ChatMessage().parse(payload)
        name = message.user.nick_name
        text = f"[{message.user.id}]{name}: {message.content}"
        self.writer.write(text, msg_type=1, color=16777215, user_hash=self._user_hash(message.user.id))

    def _parse_gift_msg(self, payload) -> None:
        message = GiftMessage().parse(payload)
        text = f"{message.user.nick_name} 送出了 {message.gift.name}x{message.combo_count}"
        self.writer.write(text, msg_type=1, color=0xFF66CC, user_hash=self._user_hash(message.user.id))

    def _parse_like_msg(self, payload) -> None:
        if not self.include_like:
            return
        message = LikeMessage().parse(payload)
        text = f"{message.user.nick_name} 点了{message.count}个赞"
        self.writer.write(text, msg_type=1, color=0x66CCFF, user_hash=self._user_hash(message.user.id))

    def _parse_member_msg(self, payload) -> None:
        if not self.include_member:
            return
        message = MemberMessage().parse(payload)
        gender = ["女", "男"][message.user.gender] if message.user.gender in (0, 1) else "未知"
        text = f"[{message.user.id}][{gender}]{message.user.nick_name} 进入了直播间"
        self.writer.write(text, msg_type=4, color=0xAAAAAA, user_hash=self._user_hash(message.user.id))

    def _parse_social_msg(self, payload) -> None:
        message = SocialMessage().parse(payload)
        text = f"[{message.user.id}]{message.user.nick_name} 关注了主播"
        self.writer.write(text, msg_type=4, color=0x66FF66, user_hash=self._user_hash(message.user.id))

    def _parse_room_user_seq_msg(self, payload) -> None:
        pass

    def _parse_fansclub_msg(self, payload) -> None:
        message = FansclubMessage().parse(payload)
        self.writer.write(f"【粉丝团】{message.content}", msg_type=4, color=0xFFAA00)

    def _parse_emoji_chat_msg(self, payload) -> None:
        message = EmojiChatMessage().parse(payload)
        name = message.user.nick_name if message.user else ''
        content = message.default_content or f"[表情]{message.emoji_id}"
        text = f"[{name}]{content}"
        self.writer.write(text, msg_type=1, user_hash=self._user_hash(message.user.id if message.user else 0))

    def _parse_room_stats_msg(self, payload) -> None:
        pass

    def _parse_room_msg(self, payload) -> None:
        pass

    def _parse_rank_msg(self, payload) -> None:
        pass

    def _parse_room_stream_adaptation_msg(self, payload) -> None:
        pass

    def _parse_control_msg(self, payload) -> None:
        message = ControlMessage().parse(payload)
        if message.status == 3:
            logger.info("抖音弹幕：检测到直播结束")
            self.stop()
