"""Resolve Douyin share links to a stable anchor identity and manage URL_config.ini lines.

Pure helpers: every network call goes through an injected ``Fetcher`` and every
request signature through an injected ``Signer``, so the module is testable offline.

Identity model
--------------
* ``sec_uid``  - permanent account id, primary dedup key.
* ``web_rid``  - canonical live room number used in ``https://live.douyin.com/<web_rid>``.
  ``live.douyin.com/<X>`` also accepts the anchor's Douyin id (numeric or alphanumeric),
  so the number in a URL is *not* necessarily the canonical ``web_rid``.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 (KHTML, like Gecko) "
    "SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile Safari/537.36"
)
PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/116.0.5845.97 Safari/537.36 Core/1.116.567.400 QQBrowser/19.7.6764.400"
)
# Anonymous ttwid already published in the upstream source (spider.get_douyin_web_stream_data).
ANON_TTWID = (
    "ttwid=1%7C2iDIYVmjzMcpZ20fcaFde0VghXAA3NaNXE_SLR68IyE%7C1761045455"
    "%7Cab35197d5cfb21df6cbb2fa7ef1c9262206b062c315b9d04da746d0b37dfbc7d"
)
DEREGISTERED_NICKNAME = "账号已注销"
VALID_QUALITIES = ("原画", "蓝光", "超清", "高清", "标清", "流畅")

SHORT_LINK_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_\-]+/?")
LIVE_LINK_RE = re.compile(r"https?://live\.douyin\.com/[A-Za-z0-9_\-.]+")
USER_LINK_RE = re.compile(r"https?://www\.douyin\.com/user/[A-Za-z0-9_\-]+")
SHARE_NAME_RE = re.compile(r"【([^【】]+?)】正在直播")


# --------------------------------------------------------------------------- types
class HttpResponse(NamedTuple):
    url: str
    status: int
    text: str


class Fetcher(Protocol):
    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        """GET ``url`` following redirects; return the final URL, status and body."""
        ...


Signer = Callable[[str, str], str]
"""``signer(query_string, user_agent) -> a_bogus``; upstream: ``src.ab_sign.ab_sign``."""


@dataclass
class AnchorInfo:
    source_url: str
    kind: str  # "short_live" | "short_user" | "live" | "user" | "unknown"
    sec_uid: str | None = None
    web_rid: str | None = None
    nickname: str | None = None
    room_id: str | None = None
    room_status: int | None = None  # 2 = live, 4 = offline
    title: str | None = None
    url_key: str | None = None  # number in the original live.douyin.com URL, lower-cased
    errors: list[str] = field(default_factory=list)

    @property
    def deregistered(self) -> bool:
        return self.nickname == DEREGISTERED_NICKNAME

    @property
    def writable(self) -> bool:
        return bool(self.web_rid and self.sec_uid and not self.deregistered)


@dataclass
class ConfigEntry:
    container: str
    line_no: int
    raw: str
    commented: bool
    quality: str | None
    url: str
    name: str
    url_key: str | None


@dataclass
class Duplicate:
    entry: ConfigEntry
    reason: str  # "sec_uid" | "web_rid" | "url_key"


# --------------------------------------------------------------------------- share text
def extract_links(text: str) -> list[str]:
    """Return Douyin links in order of appearance (short, live and user links)."""
    found: list[tuple[int, str]] = []
    for pattern in (SHORT_LINK_RE, LIVE_LINK_RE, USER_LINK_RE):
        found.extend((m.start(), m.group(0)) for m in pattern.finditer(text))
    seen: set[str] = set()
    out: list[str] = []
    for _, link in sorted(found):
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def extract_share_name(text: str) -> str | None:
    m = SHARE_NAME_RE.search(text)
    return m.group(1).strip() if m else None


def live_url_key(url: str) -> str | None:
    path = urllib.parse.urlsplit(url.strip()).path if "://" in url else url.split("/", 1)[-1]
    if "live.douyin.com" not in url:
        return None
    key = path.strip("/").split("/")[-1]
    return key.lower() or None


def classify_link(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower()
    if host == "v.douyin.com":
        return "short"
    if host == "live.douyin.com":
        return "live"
    if host == "www.douyin.com" and "/user/" in url:
        return "user"
    return "unknown"


# --------------------------------------------------------------------------- parsers
def _first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def parse_reflow_html(html: str) -> dict[str, str | int | None]:
    """Parse the RSC payload of ``webcast.amemv.com/douyin/webcast/reflow/<room_id>``."""
    t = html.replace('\\"', '"')
    owner = re.search(r'"owner":\{"id":\d+,"shortId":\d+,"nickname":"(.*?)"', t)
    web_rids = [x for x in re.findall(r'"webRid":"(.*?)"', t) if x]
    room = re.search(r'"room":\{"id":\d+,"idStr":"(\d+)","status":(\d+)', t)
    title = _first(r'"room":\{"id":\d+,"idStr":"\d+","status":\d+,"ownerUserId":\d+,"title":"(.*?)"', t)
    return {
        "nickname": owner.group(1) if owner else None,
        "web_rid": web_rids[0] if web_rids else None,
        "room_id": room.group(1) if room else None,
        "room_status": int(room.group(2)) if room else None,
        "title": title,
    }


def sec_uid_from_url(url: str) -> str | None:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    if query.get("sec_user_id"):
        return query["sec_user_id"][0]
    m = re.search(r"/(?:share/)?user/([A-Za-z0-9_\-]+)", urllib.parse.urlsplit(url).path)
    return m.group(1) if m else None


def parse_enter_json(payload: Mapping[str, object]) -> dict[str, str | int | None]:
    """Parse ``live.douyin.com/webcast/room/web/enter/``."""
    out: dict[str, str | int | None] = {"status_code": payload.get("status_code")}  # type: ignore[dict-item]
    data = payload.get("data")
    if not isinstance(data, dict):
        return out
    user = data.get("user") or {}
    rooms = data.get("data") or []
    out["sec_uid"] = user.get("sec_uid") or None
    out["nickname"] = user.get("nickname") or None
    if rooms:
        room = rooms[0]
        out["room_id"] = room.get("id_str") or None
        out["room_status"] = room.get("status")
        out["title"] = room.get("title") or None
    return out


def parse_reflow_info_json(payload: Mapping[str, object]) -> dict[str, str | int | None]:
    """Parse ``webcast.amemv.com/webcast/room/reflow/info/``."""
    out: dict[str, str | int | None] = {"status_code": payload.get("status_code")}  # type: ignore[dict-item]
    data = payload.get("data")
    room = data.get("room") if isinstance(data, dict) else None
    if not isinstance(room, dict):
        return out
    owner = room.get("owner") or {}
    out.update(
        web_rid=owner.get("web_rid") or None,
        sec_uid=owner.get("sec_uid") or None,
        nickname=owner.get("nickname") or None,
        room_id=room.get("id_str") or None,
        room_status=room.get("status"),
        title=room.get("title") or None,
    )
    return out


# --------------------------------------------------------------------------- API urls
def enter_api_url(web_rid: str, signer: Signer) -> str:
    params = {
        "aid": "6383", "app_name": "douyin_web", "live_id": "1", "device_platform": "web",
        "language": "zh-CN", "browser_language": "zh-CN", "browser_platform": "Win32",
        "browser_name": "Chrome", "browser_version": "116.0.0.0", "web_rid": web_rid, "msToken": "",
    }
    query = urllib.parse.urlencode(params)
    return f"https://live.douyin.com/webcast/room/web/enter/?{query}&a_bogus={signer(query, PC_UA)}"


def reflow_info_api_url(room_id: str, sec_uid: str, signer: Signer) -> str:
    params = {
        "verifyFp": "verify_hwj52020_7szNlAB7_pxNY_48Vh_ALKF_GA1Uf3yteoOY", "type_id": "0",
        "live_id": "1", "room_id": room_id, "sec_user_id": sec_uid,
        "version_code": "99.99.99", "app_id": "1128",
    }
    query = urllib.parse.urlencode(params)
    return f"https://webcast.amemv.com/webcast/room/reflow/info/?{query}&a_bogus={signer(query, PC_UA)}"


def reflow_page_url(room_id: str, sec_uid: str) -> str:
    return f"https://webcast.amemv.com/douyin/webcast/reflow/{room_id}?sec_user_id={sec_uid}"


PC_HEADERS: Mapping[str, str] = {"User-Agent": PC_UA, "Referer": "https://live.douyin.com/", "Cookie": ANON_TTWID}
MOBILE_HEADERS: Mapping[str, str] = {"User-Agent": MOBILE_UA}


def _json(resp: HttpResponse) -> Mapping[str, object]:
    if not resp.text:
        return {"status_code": -1}
    try:
        value = json.loads(resp.text)
    except ValueError:
        return {"status_code": -1}
    return value if isinstance(value, dict) else {"status_code": -1}


def _merge(info: AnchorInfo, values: Mapping[str, str | int | None]) -> None:
    for key in ("sec_uid", "web_rid", "nickname", "room_id", "room_status", "title"):
        value = values.get(key)
        if value not in (None, "") and getattr(info, key) in (None, ""):
            setattr(info, key, value)


# --------------------------------------------------------------------------- resolution
def _fill_web_rid(info: AnchorInfo, fetch: Fetcher, signer: Signer) -> None:
    if info.web_rid or not (info.room_id and info.sec_uid):
        return
    api = parse_reflow_info_json(_json(fetch(reflow_info_api_url(info.room_id, info.sec_uid, signer), PC_HEADERS)))
    if api.get("web_rid"):
        _merge(info, api)
        return
    if api.get("status_code") not in (0, None):
        info.errors.append(f"reflow/info status_code={api.get('status_code')}")
    page = fetch(reflow_page_url(info.room_id, info.sec_uid), MOBILE_HEADERS)
    _merge(info, parse_reflow_html(page.text))


def resolve_link(url: str, fetch: Fetcher, signer: Signer) -> AnchorInfo:
    """Resolve one Douyin link. Never raises for network/parse problems; see ``errors``."""
    kind = classify_link(url)
    info = AnchorInfo(source_url=url, kind=kind, url_key=live_url_key(url))
    try:
        if kind in ("short", "user"):
            resp = fetch(url, MOBILE_HEADERS)
            final = resp.url
            if "/reflow/" in final:
                info.kind = "short_live"
                info.sec_uid = sec_uid_from_url(final)
                _merge(info, parse_reflow_html(resp.text))
                _fill_web_rid(info, fetch, signer)
            elif "/user/" in final:
                info.kind = "short_user" if kind == "short" else "user"
                info.sec_uid = sec_uid_from_url(final)
            elif "live.douyin.com/" in final:
                info.url_key = live_url_key(final)
                kind = "live"
            else:
                info.errors.append(f"跳转后不是已知的抖音地址: {final.split('?')[0]}")
        if kind == "live" and info.url_key:
            info.kind = "live"
            enter = parse_enter_json(_json(fetch(enter_api_url(info.url_key, signer), PC_HEADERS)))
            if enter.get("status_code") != 0:
                info.errors.append(f"web/enter status_code={enter.get('status_code')}")
            _merge(info, enter)
            _fill_web_rid(info, fetch, signer)
        if kind == "unknown":
            info.errors.append("不支持的链接")
    except Exception as exc:  # network errors from the injected fetcher
        info.errors.append(f"请求失败: {type(exc).__name__}: {exc}")
    return info


def explain_for_joe(info: AnchorInfo) -> str | None:
    """Human message when the anchor must NOT be written; ``None`` when writable."""
    if info.writable:
        return None
    if info.deregistered:
        return "该账号显示已注销，不加入。"
    if info.kind == "unknown" and not info.sec_uid:
        return "这不是可识别的抖音直播/主页链接，请直接转发抖音 App 的「分享直播」文案。"
    if any(e.startswith("请求失败") for e in info.errors):
        return "短链打不开（超时/被风控），请稍后再试，或打开直播间后发 live.douyin.com/房间号。"
    if any("10011" in e for e in info.errors) and not info.sec_uid:
        return "这个房间号/抖音号查不到主播（可能改号或注销），请重新从 App 分享直播链接。"
    if info.sec_uid and not info.web_rid:
        if info.kind in ("short_user", "user"):
            return "这是主页链接，主播现在没开播，拿不到房间号，暂不写入；请在主播开播时从直播间分享链接。"
        return "只拿到了账号 ID，没解析出房间号，暂不写入；请稍后重试或换一条直播间分享链接。"
    return "解析失败，暂不写入：" + "；".join(info.errors or ["未知原因"])


# --------------------------------------------------------------------------- URL_config.ini
def parse_config_line(container: str, line_no: int, raw_line: str) -> ConfigEntry | None:
    line = raw_line.strip().lstrip("\ufeff")
    if len(line) < 18:
        return None
    commented = line.startswith("#")
    body = line.lstrip("#").strip()
    parts = [p.strip() for p in re.split("[,，]", body)]
    quality: str | None = None
    name = ""
    if len(parts) == 1:
        url = parts[0]
    elif len(parts) == 2:
        if "." in parts[0] or "://" in parts[0]:
            url, name = parts
        else:
            quality, url = parts
    else:
        quality, url, name = parts[0], parts[1], ",".join(parts[2:])
    name = re.sub(r"^主播[:：]\s*", "", name).strip()
    return ConfigEntry(container, line_no, raw_line.rstrip("\r\n"), commented, quality, url, name, live_url_key(url))


def parse_config(container: str, text: str) -> list[ConfigEntry]:
    entries = []
    for i, raw in enumerate(text.lstrip("\ufeff").split("\n"), start=1):
        entry = parse_config_line(container, i, raw)
        if entry:
            entries.append(entry)
    return entries


def sanitize_name(name: str, limit: int = 30) -> str:
    cleaned = re.sub(r"[,，#\r\n\t]", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def format_line(web_rid: str, name: str) -> str:
    if not re.fullmatch(r"[0-9A-Za-z_\-.]+", web_rid or ""):
        raise ValueError(f"invalid web_rid: {web_rid!r}")
    clean = sanitize_name(name)
    if not clean:
        raise ValueError("empty anchor name")
    return f"https://live.douyin.com/{web_rid},主播: {clean}"


# --------------------------------------------------------------------------- dedup
@dataclass
class KnownIdentity:
    sec_uid: str | None = None
    web_rid: str | None = None


def find_duplicates(
    candidate: AnchorInfo,
    entries: Iterable[ConfigEntry],
    identities: Mapping[str, KnownIdentity],
) -> list[Duplicate]:
    """Match by sec_uid (primary), web_rid (secondary) or URL key, comments included.

    ``identities`` maps an entry's ``url_key`` (or raw URL for non-live links) to its resolved identity.
    """
    cand_keys = {k for k in (candidate.url_key, (candidate.web_rid or "").lower()) if k}
    out: list[Duplicate] = []
    for entry in entries:
        ident = identities.get(entry.url_key or entry.url) or KnownIdentity()
        if candidate.sec_uid and ident.sec_uid == candidate.sec_uid:
            out.append(Duplicate(entry, "sec_uid"))
        elif candidate.web_rid and ident.web_rid == candidate.web_rid:
            out.append(Duplicate(entry, "web_rid"))
        elif entry.url_key and entry.url_key in cand_keys:
            out.append(Duplicate(entry, "url_key"))
    return out


# --------------------------------------------------------------------------- container
ZHISHI_KEYWORDS = (
    "财经", "股", "A股", "交易", "投资", "基金", "期货", "理财", "经济", "金融", "宏观", "知识", "读书",
    "讲书", "课", "老师", "商业", "创业", "老板", "董事长", "法律", "律师", "医生", "科普", "历史", "教育",
)
YULE_KEYWORDS = (
    "唱", "歌", "舞", "PK", "pk", "连麦", "礼物", "天鹅", "嘉年华", "聊天", "陪伴", "颜值", "才艺",
    "游戏", "户外", "美甲", "情感",
)


def suggest_container(title: str | None, nickname: str | None = None) -> tuple[str | None, str]:
    """Return (``"zhishi"`` | ``"yule"`` | ``None``, reason). ``None`` means ask Joe."""
    text = f"{title or ''} {nickname or ''}"
    zhishi = [k for k in ZHISHI_KEYWORDS if k in text]
    yule = [k for k in YULE_KEYWORDS if k in text]
    if zhishi and not yule:
        return "zhishi", f"标题/昵称命中知识关键词 {zhishi}"
    if yule and not zhishi:
        return "yule", f"标题/昵称命中娱乐关键词 {yule}"
    if zhishi and yule:
        return None, f"同时命中知识 {zhishi} 和娱乐 {yule}，需要 Joe 指定"
    return None, "标题/昵称没有命中任何关键词，需要 Joe 指定"
