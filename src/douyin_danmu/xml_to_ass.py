# -*- encoding: utf-8 -*-
"""Bilibili 风格 XML 弹幕转 ASS（与视频同名，播放器可自动加载）"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from loguru import logger

ASS_HEADER = """[Script Info]
Title: DouyinLiveRecorder Danmaku
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,36,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

SCROLL_DURATION = 4.0
STATIC_DURATION = 3.0
PLAY_RES_X = 1920
PLAY_RES_Y = 1080


def _decimal_to_ass_color(color: int) -> str:
    r = color & 0xFF
    g = (color >> 8) & 0xFF
    b = (color >> 16) & 0xFF
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _seconds_to_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    centisecs = int(round((secs % 1) * 100))
    secs_int = int(secs)
    if centisecs >= 100:
        centisecs = 0
        secs_int += 1
    return f"{hours}:{minutes:02d}:{secs_int:02d}.{centisecs:02d}"


def _parse_p_attr(p_attr: str) -> tuple[float, int, int, int]:
    parts = p_attr.split(',')
    start = float(parts[0]) if parts else 0.0
    msg_type = int(parts[1]) if len(parts) > 1 else 1
    font_size = int(parts[2]) if len(parts) > 2 else 25
    color = int(parts[3]) if len(parts) > 3 else 16777215
    return start, msg_type, font_size, color


def _build_dialogue(start: float, msg_type: int, font_size: int, color: int, text: str) -> str:
    end = start + (SCROLL_DURATION if msg_type == 1 else STATIC_DURATION)
    start_s = _seconds_to_ass_time(start)
    end_s = _seconds_to_ass_time(end)
    ass_color = _decimal_to_ass_color(color)
    safe_text = text.replace('\n', ' ').replace('\r', '')

    y_bottom = PLAY_RES_Y - 80

    if msg_type == 1:
        prefix = (
            f"{{\\an1\\fs{font_size}\\c{ass_color}"
            f"\\move({PLAY_RES_X},{y_bottom},0,{y_bottom})}}"
        )
    elif msg_type == 5:
        prefix = f"{{\\an8\\fs{font_size}\\c{ass_color}}}"
    else:
        prefix = f"{{\\an2\\fs{font_size}\\c{ass_color}}}"

    return f"Dialogue: 0,{start_s},{end_s},Default,,0,0,0,,{prefix}{safe_text}"


def convert_xml_to_ass(xml_path: str | Path, ass_path: str | Path | None = None) -> bool:
    """
    将 B 站 XML 弹幕转为 ASS。默认输出与 xml 同目录、同名 .ass。
    返回是否成功生成（无弹幕时返回 False）。
    """
    xml_path = Path(xml_path)
    if not xml_path.is_file() or xml_path.stat().st_size < 10:
        return False

    ass_path = Path(ass_path) if ass_path else xml_path.with_suffix('.ass')

    try:
        raw = xml_path.read_text(encoding='utf-8')
    except OSError as e:
        logger.warning(f"读取弹幕 XML 失败: {xml_path} ({e})")
        return False

    dialogues: list[str] = []

    try:
        root = ET.fromstring(raw)
        for d in root.iter('d'):
            p = d.get('p', '')
            text = (d.text or '').strip()
            if not text:
                continue
            start, msg_type, font_size, color = _parse_p_attr(p)
            dialogues.append(_build_dialogue(start, msg_type, font_size, color, text))
    except ET.ParseError:
        for match in re.finditer(r'<d\s+p="([^"]+)"[^>]*>([^<]*)</d>', raw):
            p, text = match.group(1), match.group(2).strip()
            if not text:
                continue
            start, msg_type, font_size, color = _parse_p_attr(p)
            dialogues.append(_build_dialogue(start, msg_type, font_size, color, text))

    if not dialogues:
        return False

    try:
        ass_path.write_text(ASS_HEADER + '\n'.join(dialogues) + '\n', encoding='utf-8-sig')
        logger.info(f"弹幕已转为 ASS: {ass_path}")
        return True
    except OSError as e:
        logger.warning(f"写入 ASS 失败: {ass_path} ({e})")
        return False


def convert_xml_template_to_ass_files(path_template: str) -> list[str]:
    """分段模板路径下，将所有已生成的 .xml 转为 .ass。"""
    template = Path(path_template)
    parent = template.parent
    stem = template.stem

    if '%03d' in stem:
        prefix = stem.replace('%03d', '')
        xml_files = sorted(parent.glob(f"{prefix}*.xml"))
    else:
        xml_files = [template] if template.is_file() else []

    converted = []
    for xml_file in xml_files:
        if convert_xml_to_ass(xml_file):
            converted.append(str(xml_file.with_suffix('.ass')))
    return converted
