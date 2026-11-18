from __future__ import annotations

from pathlib import Path

from app.lint import lint_article, target_chars_from_brief
from app.materials import write_index
from app.sourcecheck import check_sources, match_quote
from app.storage import ProjectStore


def make_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects", tmp_path / "styles")
    project = store.create(title="测试", slug="lint-test")
    source_dir = project.dir / "sources/A01-test"
    source_dir.mkdir(parents=True)
    (source_dir / "content.md").write_text(
        "# A01 测试\n\n[¶1] 这是一段很长的素材原文，用来测试逐字复制检查是否能够识别出来。\n\n"
        "[¶2] 2024 年市场平均利率约为 1.68%。\n\n[¶3] 他说：“这是一句可以核对的原话。”\n",
        "utf8",
    )
    project.meta["sources"] = [
        {"id": "A01", "kind": "written", "title": "测试", "dir": "sources/A01-test", "paragraphs": 3, "chars": 100}
    ]
    store.save(project)
    write_index(project)
    return store, project


def test_lint_original_mode_and_pending(tmp_path: Path) -> None:
    _, project = make_project(tmp_path)
    (project.dir / "article.md").write_text(
        """---
title: 标题
digest: 摘要
---

素材显示这是一段很长的素材原文，用来测试逐字复制检查是否能够识别出来。

![图注](ww-img:pending "google: x | 中文")
""",
        "utf8",
    )
    report = lint_article(project, original_mode=True)
    rules = {issue["rule"] for issue in report["issues"]}
    assert "source-mention" in rules
    assert "verbatim-copy" in rules
    assert "image-pending" in rules
    assert report["summary"]["errors"] >= 1


def test_lint_title_and_digest_limits(tmp_path: Path) -> None:
    _, project = make_project(tmp_path)
    (project.dir / "article.md").write_text(
        "---\ntitle: " + "长" * 65 + "\ndigest: " + "摘" * 121 + "\n---\n\n正文。\n",
        "utf8",
    )
    report = lint_article(project, original_mode=False)
    rules = {issue["rule"] for issue in report["issues"]}
    assert "title-length" in rules
    assert "digest-length" in rules


def test_sourcecheck_quotes_and_figures(tmp_path: Path) -> None:
    _, project = make_project(tmp_path)
    (project.dir / "article.md").write_text(
        """---
title: 标题
digest: 摘要
---

市场平均利率约为 1.68%。[A01¶2]

他说：“这是一句可以核对的原话。”[A01¶3]

这是一个错误的数据 9.99%。[A01¶2]

无来源的段落，长度超过四十个字符，应该被列为待确认内容，提醒作者补出处或者标为观点。
""",
        "utf8",
    )
    report = check_sources(project)
    types = {issue["type"] for issue in report["issues"]}
    assert "figure-mismatch" in types
    assert "unsourced-claim" in types
    assert report["summary"]["quotes"] >= 1


def test_match_quote_fuzzy() -> None:
    assert match_quote("这是一句可以核对的原话", "他说：“这是一句可以核对的原话。”")["kind"] == "exact"
    assert match_quote("完全不存在的内容", "另一段文字")["kind"] == "none"


def test_target_chars_from_brief() -> None:
    assert target_chars_from_brief("**篇幅**：约 2800 字") == 2800
    assert target_chars_from_brief("目标 1200 字") == 1200
