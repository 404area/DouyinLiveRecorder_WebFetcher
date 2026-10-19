# Douyin share-link fixtures

Trimmed, anonymised samples for `tests/test_douyin_link_resolver.py` and `tests/test_url_config_writer.py`.
Captured anonymously on 2026-10-19 (UTC+8) and cut down to the fields the parsers read.

- `reflow_967541884601_live.html`: minimal RSC fragment of a `webcast.amemv.com/douyin/webcast/reflow/<room_id>` page
  (live room). Avatars, user ids, cookies and tracking parameters removed.
- `user_home_miaowa.html`: stub; user-home links are resolved from the redirect URL only.
- `enter_*.json`: `live.douyin.com/webcast/room/web/enter/` responses reduced to status_code, room id/status/title,
  sec_uid and nickname.
- `reflow_info_yuren_offline.json`: `webcast.amemv.com/webcast/room/reflow/info/` response, same reduction.
- `*_URL_config.ini`: small synthetic URL_config.ini samples (UTF-8 BOM, LF). Not production config;
  only the lines the dedup/append tests need are kept.
