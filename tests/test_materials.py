from __future__ import annotations

from pathlib import Path

from app.materials import (
    format_time,
    looks_timestamped,
    merge_cues,
    parse_content,
    parse_ref,
    parse_subtitles,
    parse_timestamped_text,
    read_refs,
    text_to_paragraphs,
)
from app.storage import ProjectStore


def test_text_to_paragraphs_drops_frontmatter_and_keeps_headings() -> None:
    parsed = text_to_paragraphs("---\ntitle: 素材标题\n---\n\n# 标题\n\n第一段。\n\n## 小节\n第二段。")
    assert parsed["title"] == "素材标题"
    assert parsed["paragraphs"] == ["# 标题", "第一段。", "## 小节", "第二段。"]


def test_subtitles_merge_into_paragraphs_with_timestamps() -> None:
    srt = """1
00:00:01,000 --> 00:00:03,000
这是第一句。

2
00:00:03,200 --> 00:00:06,000
这是第二句。

3
00:00:12,000 --> 00:00:14,000
停顿之后的新段落。
"""
    cues = parse_subtitles(srt)
    assert len(cues) == 3
    paragraphs = merge_cues(cues)
    assert len(paragraphs) == 2
    assert paragraphs[0]["text"].startswith("这是第一句")
    assert format_time(paragraphs[1]["time"]) == "00:12"


def test_timestamped_text_and_looks_timestamped() -> None:
    text = "00:01 第一句\n00:03 第二句\n00:20 第三句"
    assert looks_timestamped(text)
    cues = parse_timestamped_text(text)
    assert [cue["text"] for cue in cues] == ["第一句", "第二句", "第三句"]


def test_parse_refs() -> None:
    assert parse_ref("A01")["id"] == "A01"
    assert parse_ref("A01¶3-5")["para"] == [3, 5]
    assert parse_ref("S01@12:30")["time"] == [750, None]
    assert parse_ref("S01@12:30-15:10")["time"] == [750, 910]
    assert parse_ref("bad") is None


def test_content_anchors_and_read_refs(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects", tmp_path / "styles")
    project = store.create(title="测试", slug="test")
    source_dir = project.dir / "sources/A01-test"
    source_dir.mkdir(parents=True)
    (source_dir / "content.md").write_text(
        "# A01 测试\n\n[¶1] 第一段。\n\n[¶2] 第二段。\n\n[¶3] 第三段。\n", "utf8"
    )
    project.meta["sources"] = [{"id": "A01", "kind": "written", "title": "测试", "dir": "sources/A01-test"}]
    store.save(project)
    paragraphs = parse_content((source_dir / "content.md").read_text("utf8"))
    assert [paragraph["n"] for paragraph in paragraphs] == [1, 2, 3]
    result = read_refs(project, ["A01¶2-3"])
    assert "第二段" in result[0]["text"] and "第三段" in result[0]["text"]
