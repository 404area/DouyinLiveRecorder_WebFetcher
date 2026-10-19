from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from tools import douyin_link_resolver as dlr

FIX = Path(__file__).parent / "fixtures"
JOE_TEXT = (
    "2- #在抖音，记录美好生活#【甜不辣】正在直播，来和我一起支持Ta吧。复制下方链接，打开【抖音】，"
    "直接观看直播！ https://v.douyin.com/TxM3OMJGxjg/ 3@1.com :9pm"
)
TIAN_SEC = "MS4wLjABAAAAThkTIe-7Lwb93W0jmS6bwuSRv5VQbMbbQxIHKOCVJw4"
YUREN_SEC = "MS4wLjABAAAAfT5DrjqE6N-ykgKjalMds0o-ZTzpWYJIqxzIuJmU4Ms"
MIAOWA_SEC = "MS4wLjABAAAA3mXksDB-2ln9m5j0cShNil-YkwdjikZIciRV9pBS9y8"


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def fake_signer(query: str, ua: str) -> str:
    return "SIGNED"


class FakeFetcher:
    """Route URLs to fixtures by substring; records every call."""

    def __init__(self, routes: list[tuple[str, dlr.HttpResponse]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> dlr.HttpResponse:
        self.calls.append(url)
        for needle, resp in self.routes:
            if needle in url:
                return resp
        raise AssertionError(f"unexpected url {url}")


SHORT_FINAL = f"https://webcast.amemv.com/douyin/webcast/reflow/7698290520545577762?sec_user_id={TIAN_SEC}"
SHORT_ROUTE = ("v.douyin.com/TxM3OMJGxjg", dlr.HttpResponse(SHORT_FINAL, 200, text("reflow_967541884601_live.html")))
YUREN_ROUTES = [
    ("web_rid=yuren1108", dlr.HttpResponse("x", 200, text("enter_yuren1108_offline_alias.json"))),
    ("web_rid=902143432874", dlr.HttpResponse("x", 200, text("enter_yuren1108_offline_alias.json"))),
    ("reflow/info/", dlr.HttpResponse("x", 200, text("reflow_info_yuren_offline.json"))),
]


def config_entries() -> list[dlr.ConfigEntry]:
    out = []
    for name in ("zhishi", "yule"):
        out += dlr.parse_config(name, (FIX / f"{name}_URL_config.ini").read_text(encoding="utf-8-sig"))
    return out


# ------------------------------------------------------------------ share text
def test_extract_short_link_and_name_from_joe_text():
    assert dlr.extract_links(JOE_TEXT) == ["https://v.douyin.com/TxM3OMJGxjg/"]
    assert dlr.extract_share_name(JOE_TEXT) == "甜不辣"


def test_extract_other_link_forms_in_order():
    s = "a https://live.douyin.com/yuren1108 b https://www.douyin.com/user/MS4wLjABAAAAx c https://v.douyin.com/Ab_9/"
    assert dlr.extract_links(s) == [
        "https://live.douyin.com/yuren1108",
        "https://www.douyin.com/user/MS4wLjABAAAAx",
        "https://v.douyin.com/Ab_9/",
    ]
    assert dlr.extract_links("没有链接") == []


def test_live_url_key_strips_query_and_lowercases():
    assert dlr.live_url_key("https://live.douyin.com/JLM_laomo?x=1") == "jlm_laomo"
    assert dlr.live_url_key("https://v.douyin.com/abc/") is None


# ------------------------------------------------------------------ parsers
def test_parse_reflow_html_live_room():
    got = dlr.parse_reflow_html(text("reflow_967541884601_live.html"))
    assert got["web_rid"] == "967541884601"
    assert got["nickname"] == "甜不辣"
    assert got["room_status"] == 2
    assert got["room_id"] == "7698290520545577762"
    assert got["title"] == "收集200个天鹅之梦召唤神龙"


def test_parse_enter_json_variants():
    live = dlr.parse_enter_json(json.loads(text("enter_967541884601_live.json")))
    assert live["status_code"] == 0 and live["sec_uid"] == TIAN_SEC and live["room_status"] == 2
    bad = dlr.parse_enter_json(json.loads(text("enter_hzj3399_invalid_10011.json")))
    assert bad["status_code"] == 10011 and bad.get("sec_uid") is None
    gone = dlr.parse_enter_json(json.loads(text("enter_59588222964_deregistered.json")))
    assert gone["nickname"] == dlr.DEREGISTERED_NICKNAME


def test_parse_reflow_info_json_offline_gives_web_rid():
    got = dlr.parse_reflow_info_json(json.loads(text("reflow_info_yuren_offline.json")))
    assert got["web_rid"] == "902143432874" and got["room_status"] == 4 and got["sec_uid"] == YUREN_SEC


# ------------------------------------------------------------------ resolve
def test_resolve_short_live_link_joe_example():
    fetch = FakeFetcher([SHORT_ROUTE])
    info = dlr.resolve_link("https://v.douyin.com/TxM3OMJGxjg/", fetch, fake_signer)
    assert info.kind == "short_live"
    assert (info.web_rid, info.sec_uid, info.room_status) == ("967541884601", TIAN_SEC, 2)
    assert info.writable and dlr.explain_for_joe(info) is None
    assert len(fetch.calls) == 1  # webRid already in the page, no extra API call


def test_resolve_user_home_short_link_gives_only_sec_uid():
    final = f"https://www.iesdouyin.com/share/user/{MIAOWA_SEC}"
    fetch = FakeFetcher([("v.douyin.com", dlr.HttpResponse(final, 200, text("user_home_miaowa.html")))])
    info = dlr.resolve_link("https://v.douyin.com/AL4aS_r_asg/", fetch, fake_signer)
    assert info.kind == "short_user" and info.sec_uid == MIAOWA_SEC and info.web_rid is None
    assert not info.writable
    assert "主页链接" in (dlr.explain_for_joe(info) or "")


def test_resolve_alias_live_link_to_canonical_web_rid():
    info = dlr.resolve_link("https://live.douyin.com/yuren1108", FakeFetcher(YUREN_ROUTES), fake_signer)
    assert info.url_key == "yuren1108"
    assert info.sec_uid == YUREN_SEC and info.web_rid == "902143432874" and info.room_status == 4


def test_resolve_invalid_and_deregistered_are_not_writable():
    bad = dlr.resolve_link(
        "https://live.douyin.com/_hzj3399",
        FakeFetcher([("web/enter", dlr.HttpResponse("x", 200, text("enter_hzj3399_invalid_10011.json")))]),
        fake_signer,
    )
    assert not bad.writable and "查不到主播" in (dlr.explain_for_joe(bad) or "")
    gone = dlr.resolve_link(
        "https://live.douyin.com/59588222964",
        FakeFetcher([
            ("web/enter", dlr.HttpResponse("x", 200, text("enter_59588222964_deregistered.json"))),
            ("reflow/info", dlr.HttpResponse("x", 200, '{"status_code": 0, "data": {"room": {"owner": {}}}}')),
            ("douyin/webcast/reflow", dlr.HttpResponse("x", 200, "")),
        ]),
        fake_signer,
    )
    assert gone.deregistered and not gone.writable
    assert dlr.explain_for_joe(gone) == "该账号显示已注销，不加入。"


def test_resolve_network_error_is_reported_not_raised():
    def boom(url: str, headers: Mapping[str, str]) -> dlr.HttpResponse:
        raise TimeoutError("timed out")

    info = dlr.resolve_link("https://v.douyin.com/TxM3OMJGxjg/", boom, fake_signer)
    assert not info.writable and "打不开" in (dlr.explain_for_joe(info) or "")


def test_sec_uid_only_without_web_rid_is_not_writable():
    info = dlr.AnchorInfo(source_url="u", kind="live", sec_uid=YUREN_SEC, nickname="x")
    assert not info.writable and "没解析出房间号" in (dlr.explain_for_joe(info) or "")


# ------------------------------------------------------------------ config + dedup
def test_parse_sample_configs():
    entries = config_entries()
    zh = [e for e in entries if e.container == "zhishi"]
    yu = [e for e in entries if e.container == "yule"]
    assert len(zh) == 4 and len(yu) == 6
    assert sum(e.commented for e in yu) == 2 and not any(e.commented for e in zh)
    assert zh[1].url_key == "yuren1108" and zh[1].name == "愚人" and zh[1].quality is None


def test_parse_config_line_with_quality():
    e = dlr.parse_config_line("yule", 1, "超清，https://live.douyin.com/745964462470,主播: 某人")
    assert e and e.quality == "超清" and e.url_key == "745964462470" and e.name == "某人"


def _identities() -> dict[str, dlr.KnownIdentity]:
    return {
        "yuren1108": dlr.KnownIdentity(YUREN_SEC, "902143432874"),
        "59588222964": dlr.KnownIdentity("MS4wLjABAAAAXyKr", None),
    }


def test_alias_and_canonical_are_duplicates_via_sec_uid():
    cand = dlr.AnchorInfo("https://live.douyin.com/902143432874", "live", sec_uid=YUREN_SEC,
                          web_rid="902143432874", url_key="902143432874")
    dups = dlr.find_duplicates(cand, config_entries(), _identities())
    assert [(d.entry.container, d.entry.line_no, d.reason) for d in dups] == [("zhishi", 2, "sec_uid")]


def test_duplicate_found_across_containers_including_commented_lines():
    cand = dlr.AnchorInfo("https://live.douyin.com/59588222964", "live", url_key="59588222964")
    dups = dlr.find_duplicates(cand, config_entries(), {})
    assert len(dups) == 1 and dups[0].entry.container == "yule" and dups[0].entry.commented
    assert dups[0].reason == "url_key"


def test_related_accounts_are_not_duplicates():
    """愚人 / 愚人遇见 are the same person but different accounts: both must stay."""
    cand = dlr.AnchorInfo("u", "live", sec_uid=YUREN_SEC, web_rid="902143432874", url_key="902143432874")
    dups = dlr.find_duplicates(cand, config_entries(), _identities())
    assert all(d.entry.name != "愚人遇见" for d in dups)


def test_new_anchor_has_no_duplicates():
    cand = dlr.AnchorInfo("u", "short_live", sec_uid=TIAN_SEC, web_rid="967541884601")
    assert dlr.find_duplicates(cand, config_entries(), _identities()) == []


# ------------------------------------------------------------------ formatting + container
def test_format_line_canonical_and_sanitized():
    assert dlr.format_line("967541884601", "甜不辣") == "https://live.douyin.com/967541884601,主播: 甜不辣"
    line = dlr.format_line("967541884601", " a,b，c#d\n ")
    assert line == "https://live.douyin.com/967541884601,主播: a b c d"
    assert line.count(",") == 1 and "#" not in line and "webcast" not in line
    assert not line.startswith(dlr.VALID_QUALITIES)


@pytest.mark.parametrize("bad", ["", "webcast.amemv.com/x", "a/b", "1 2"])
def test_format_line_rejects_non_web_rid(bad):
    with pytest.raises(ValueError):
        dlr.format_line(bad, "x")


def test_suggest_container():
    assert dlr.suggest_container("收集200个天鹅之梦召唤神龙", "甜不辣")[0] == "yule"
    assert dlr.suggest_container("科创阶段顶部概率极大", "付鹏的财经世界")[0] == "zhishi"
    # 「交易的游戏」同时命中「交易」和「游戏」，必须交给 Joe 指定
    assert dlr.suggest_container("科创阶段顶部概率极大", "交易的游戏")[0] is None
    assert dlr.suggest_container("愚人正在直播", "愚人")[0] is None
    assert dlr.suggest_container("PK 讲股票", None)[0] is None
