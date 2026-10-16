"""Bilibili-style XML danmu output and segment timing."""

import xml.etree.ElementTree as ET

from src.douyin_danmu.xml_writer import XmlDanmuWriter
from tests.conftest import FakeClock


def _patch_clock(monkeypatch, start: float) -> FakeClock:
    clock = FakeClock(start)
    monkeypatch.setattr("src.douyin_danmu.xml_writer.time.time", clock.time)
    return clock


def test_new_file_writes_header_escaped_text_and_footer(tmp_path, monkeypatch):
    clock = _patch_clock(monkeypatch, 1_700_000_000.0)
    path = tmp_path / "nested" / "danmu.xml"
    writer = XmlDanmuWriter(str(path))
    clock.advance(1.25)
    writer.write("a<b>&c", msg_type=1, color=255, font_size=30, user_hash="abc")
    writer.write("")
    writer.write("line\nbreak")
    writer.close()

    text = path.read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<i>\n')
    assert text.endswith("</i>\n")
    assert 'p="1.250,1,30,255,1700000001250,0,abc,0">a&lt;b&gt;&amp;c</d>' in text
    assert "line\nbreak" in text
    root = ET.fromstring(text)
    bodies = [node.text for node in root.iter("d")]
    assert bodies == ["a<b>&c", "line\nbreak"]
    assert path.with_suffix(".ass").is_file()


def test_existing_file_is_appended_without_a_second_header(tmp_path, monkeypatch):
    _patch_clock(monkeypatch, 1_700_000_000.0)
    path = tmp_path / "danmu.xml"
    path.write_text("<i>\n", encoding="utf-8")
    writer = XmlDanmuWriter(str(path))
    writer.write("more")
    writer.close()
    text = path.read_text(encoding="utf-8")
    assert text.count("<?xml") == 0
    assert text.count("<i>") == 1
    assert ">more</d>" in text
    assert text.endswith("</i>\n")


def test_relative_time_and_segment_rotation(tmp_path, monkeypatch):
    clock = _patch_clock(monkeypatch, 1_000.0)
    template = str(tmp_path / "danmu_%03d.xml")
    writer = XmlDanmuWriter(template, segment_seconds=10)
    writer.write("first")
    clock.advance(8)
    writer.write("still-first")
    clock.advance(2)
    writer.write("second")
    clock.advance(3)
    writer.write("later")
    writer.close()

    first = (tmp_path / "danmu_000.xml").read_text(encoding="utf-8")
    second = (tmp_path / "danmu_001.xml").read_text(encoding="utf-8")
    assert 'p="0.000,' in first
    assert 'p="8.000,' in first
    assert "second" not in first
    assert first.rstrip().endswith("</i>")
    assert 'p="0.000,' in second
    assert 'p="3.000,' in second
    assert "first" not in second
    assert (tmp_path / "danmu_000.ass").is_file()
    assert (tmp_path / "danmu_001.ass").is_file()


def test_without_segment_seconds_time_jump_stays_in_one_file(tmp_path, monkeypatch):
    clock = _patch_clock(monkeypatch, 1_000.0)
    path = tmp_path / "danmu.xml"
    writer = XmlDanmuWriter(str(path), segment_seconds=None)
    writer.write("early")
    clock.advance(3600)
    writer.write("late")
    writer.close()
    text = path.read_text(encoding="utf-8")
    assert "early" in text and "late" in text
    assert list(tmp_path.glob("*.xml")) == [path]


def test_empty_recording_has_no_ass(tmp_path, monkeypatch):
    _patch_clock(monkeypatch, 1_000.0)
    path = tmp_path / "empty.xml"
    writer = XmlDanmuWriter(str(path))
    writer.close()
    text = path.read_text(encoding="utf-8")
    assert text == '<?xml version="1.0" encoding="UTF-8"?>\n<i>\n</i>\n'
    assert not path.with_suffix(".ass").exists()
