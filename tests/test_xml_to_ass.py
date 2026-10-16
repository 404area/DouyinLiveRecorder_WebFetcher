"""XML to ASS conversion, colors, and cue timing."""

from pathlib import Path

from src.douyin_danmu.xml_to_ass import (
    _build_dialogue,
    _decimal_to_ass_color,
    _parse_p_attr,
    _seconds_to_ass_time,
    convert_xml_template_to_ass_files,
    convert_xml_to_ass,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_danmu.xml"


def test_color_and_time_helpers():
    assert _decimal_to_ass_color(16777215) == "&H00FFFFFF"
    assert _decimal_to_ass_color(0xFF66CC) == "&H00FF66CC"
    assert _decimal_to_ass_color(255) == "&H000000FF"
    assert _decimal_to_ass_color(0) == "&H00000000"

    assert _seconds_to_ass_time(0) == "0:00:00.00"
    assert _seconds_to_ass_time(-3) == "0:00:00.00"
    assert _seconds_to_ass_time(3661.25) == "1:01:01.25"
    assert _seconds_to_ass_time(1.995) == "0:00:02.00"
    assert _seconds_to_ass_time(59.999) == "0:01:00.00"
    assert _seconds_to_ass_time(119.995) == "0:02:00.00"
    assert _seconds_to_ass_time(3599.999) == "1:00:00.00"


def test_parse_p_attr_defaults_and_dialogue_modes():
    assert _parse_p_attr("1.5") == (1.5, 1, 25, 16777215)
    assert _parse_p_attr("1.5,5,30,255") == (1.5, 5, 30, 255)

    scroll = _build_dialogue(1.25, 1, 25, 16777215, "a\nb")
    assert scroll.startswith("Dialogue: 0,0:00:01.25,0:00:05.25,")
    assert r"\move(1920,1000,0,1000)" in scroll
    assert scroll.endswith("a b")

    top = _build_dialogue(8, 5, 36, 255, "置顶")
    assert "0:00:08.00,0:00:11.00" in top
    assert r"\an8" in top
    assert "&H000000FF" in top

    bottom = _build_dialogue(3, 4, 20, 0, "底")
    assert "0:00:03.00,0:00:06.00" in bottom
    assert r"\an2" in bottom


def test_fixture_xml_converts_to_ass(tmp_path):
    target = tmp_path / "sample.ass"
    assert convert_xml_to_ass(FIXTURE, target) is True
    text = target.read_text(encoding="utf-8-sig")
    assert text.startswith("[Script Info]")
    assert "你好 & <world>" in text
    assert "Dialogue: 0,0:00:01.25,0:00:05.25," in text
    assert "Dialogue: 0,0:00:08.00,0:00:11.00," in text
    assert r"\an8" in text
    assert text.count("Dialogue:") == 2


def test_convert_rejects_missing_tiny_and_empty_files(tmp_path):
    missing = tmp_path / "nope.xml"
    assert convert_xml_to_ass(missing) is False

    tiny = tmp_path / "tiny.xml"
    tiny.write_text("<i/>", encoding="utf-8")
    assert convert_xml_to_ass(tiny) is False
    assert not tiny.with_suffix(".ass").exists()

    empty = tmp_path / "empty.xml"
    empty.write_text('<?xml version="1.0"?><i></i>', encoding="utf-8")
    assert convert_xml_to_ass(empty) is False
    assert not empty.with_suffix(".ass").exists()


def test_malformed_xml_falls_back_to_tag_regex(tmp_path):
    broken = tmp_path / "broken.xml"
    broken.write_text(
        '<i><d p="4.000,1,25,16777215,1,0,0,0">rescued</d><unclosed>',
        encoding="utf-8",
    )
    assert convert_xml_to_ass(broken) is True
    text = broken.with_suffix(".ass").read_text(encoding="utf-8-sig")
    assert "rescued" in text
    assert "0:00:04.00,0:00:08.00" in text


def test_segment_template_converts_matching_xml_only(tmp_path):
    template = tmp_path / "danmu_%03d.xml"
    for index, word in ((0, "one"), (1, "two")):
        path = tmp_path / f"danmu_{index:03d}.xml"
        path.write_text(
            f'<?xml version="1.0"?><i><d p="0.000,1,25,16777215,1,0,0,0">{word}</d></i>',
            encoding="utf-8",
        )
    (tmp_path / "other.xml").write_text(
        '<?xml version="1.0"?><i><d p="0,1,25,16777215,1,0,0,0">skip</d></i>',
        encoding="utf-8",
    )
    converted = convert_xml_template_to_ass_files(str(template))
    assert converted == [
        str(tmp_path / "danmu_000.ass"),
        str(tmp_path / "danmu_001.ass"),
    ]
    assert "one" in (tmp_path / "danmu_000.ass").read_text(encoding="utf-8-sig")
    assert "two" in (tmp_path / "danmu_001.ass").read_text(encoding="utf-8-sig")
    assert not (tmp_path / "other.ass").exists()

    assert convert_xml_template_to_ass_files(str(tmp_path / "missing.xml")) == []
