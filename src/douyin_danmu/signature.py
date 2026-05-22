# -*- encoding: utf-8 -*-
import codecs
import hashlib
import urllib.parse
from pathlib import Path

from py_mini_racer import MiniRacer

_MODULE_DIR = Path(__file__).resolve().parent
_SIGN_JS = _MODULE_DIR / 'js' / 'sign.js'


def generate_signature(wss: str) -> str:
    params = (
        "live_id,aid,version_code,webcast_sdk_version,"
        "room_id,sub_room_id,sub_channel_id,did_rule,"
        "user_unique_id,device_platform,device_type,ac,"
        "identity"
    ).split(',')
    wss_params = urllib.parse.urlparse(wss).query.split('&')
    wss_maps = {i.split('=')[0]: i.split("=")[-1] for i in wss_params}
    tpl_params = [f"{i}={wss_maps.get(i, '')}" for i in params]
    param = ','.join(tpl_params)
    md5 = hashlib.md5()
    md5.update(param.encode())
    md5_param = md5.hexdigest()

    with codecs.open(_SIGN_JS, 'r', encoding='utf8') as f:
        script = f.read()

    ctx = MiniRacer()
    ctx.eval(script)
    return ctx.call("get_sign", md5_param)
