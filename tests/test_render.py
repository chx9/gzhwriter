from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.render.css import inline_css
from app.render.markdown import markdown_to_html
from app.render.render import render_article
from app.render.themes import BUILTIN_IDS, ThemeLibrary
from app.storage import ProjectStore

SAMPLE = """# 标题

开头 **粗体** ++下划线++ ==高亮== *斜体*。

## 01 / 小节

> 引用

> [!TIP] 提示
> 内容

```cards
标签 | 数值 | 说明
A | 1 | x
B | 2 | y
```

```chart
title: 图
A | 1
B | 2
```

| a | b |
|---|---|
| 1 | 2 |

![图注](ww-img:pending "google: test keywords | 中文")
"""


@pytest.mark.parametrize("theme", BUILTIN_IDS)
def test_render_matches_js_golden(theme: str, tmp_path: Path) -> None:
    library = ThemeLibrary(tmp_path / "styles")
    resolved = library.resolve(theme)
    ctx = {
        "links": [],
        "images": [],
        "pending": [],
        "moxiaChrome": bool(re.search(r"#moxia\b", resolved["css"])),
    }
    html, _ = inline_css(
        f'<section class="wx-article" id="moxia">{markdown_to_html(SAMPLE, ctx)}</section>',
        resolved["css"],
    )
    expected = (Path(__file__).parent / "golden" / f"theme-{theme}.html").read_text("utf8")
    assert html == expected


def test_pending_slot_never_emits_img() -> None:
    html = markdown_to_html('![图注](ww-img:pending "google: keyword | 中文")', {"links": [], "images": [], "pending": []})
    assert "【配图待补】" in html
    assert "<img" not in html


def test_inline_css_removes_class_and_keeps_style(tmp_path: Path) -> None:
    library = ThemeLibrary(tmp_path / "styles")
    resolved = library.resolve("wechat")
    html, _ = inline_css('<section class="wx-article"><p class="wx-p">文字</p></section>', resolved["css"])
    assert "class=" not in html
    assert "style=" in html
    assert "<style" not in html


def test_article_render_strips_markers_and_deduplicates_h1(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects", tmp_path / "styles")
    project = store.create(title="测试文章", slug="render-test")
    source_dir = project.dir / "sources/A01-test"
    source_dir.mkdir(parents=True)
    (source_dir / "content.md").write_text("# A01 测试\n\n[¶1] 素材内容。\n", "utf8")
    project.meta["sources"] = [{"id": "A01", "kind": "written", "title": "测试", "dir": "sources/A01-test"}]
    store.save(project)
    (project.dir / "images").mkdir(exist_ok=True)
    (project.dir / "images/fig-01.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    )
    (project.dir / "article.md").write_text(
        """---
title: 测试文章
digest: 摘要
author: 作者
---

# 测试文章

第一段。[A01¶1][观点]

![图注](ww-img:pending "google: keyword | 中文")

![已补图](images/fig-01.png)

## 01 / 小节

正文。
""",
        "utf8",
    )
    library = ThemeLibrary(tmp_path / "styles")
    resolved = library.resolve("wechat")
    info = render_article(project, theme_css=resolved["css"], theme="wechat")
    article_html = (project.dir / "dist/article.html").read_text("utf8")
    preview_html = info["previewHtml"]
    assert "class=" not in article_html
    assert "style=" in article_html
    assert "<style" not in article_html
    assert "<script" not in article_html
    assert "[A01" not in article_html
    assert "[观点]" not in article_html
    assert "<h1" not in article_html
    assert "【配图待补】" in article_html
    assert "ww-img:pending" not in article_html
    assert "images/fig-01.png" in article_html
    assert "data:image/png;base64" in preview_html
    assert info["pendingImages"]


def test_loose_markers_are_stripped_but_link_text_kept() -> None:
    from app.materials import strip_markers
    from app.render.render import apply_citations

    body = (
        "一个时代结束了[A02标题]。这是判断[A01¶1；观点]。"
        "看图 ![A02 服务器图](images/a.png)，再读 [A03 财报](https://example.com)。"
    )
    out, _ = apply_citations(body, "strip")
    assert "[A02标题]" not in out
    assert "[A01¶1；观点]" not in out
    assert "![A02 服务器图](images/a.png)" in out
    assert "[A03 财报](https://example.com)" in out
    assert strip_markers("[A02标题]") == ""
