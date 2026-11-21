"""排版 + 检查的复用入口。API、图节点、填图后自动重排共用。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import ApiError, NotFound
from .lint import lint_article, target_chars_from_brief
from .render.render import compose_article, local_image_data_url, render_article
from .render.themes import ThemeLibrary
from .sourcecheck import check_sources
from .storage import Project, read_json, read_text, set_frontmatter_value, write_json


def resolve_sheet(themes: ThemeLibrary, slug: str) -> tuple[str, dict[str, Any]]:
    """主题 CSS + meta。"""
    theme = themes.resolve(themes.effective_id(slug))
    return theme["css"], theme["meta"]


def render_project(
    project: Project,
    *,
    settings: Settings,
    themes: ThemeLibrary,
    citation_mode: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    css, meta = resolve_sheet(themes, project.slug)
    info = render_article(
        project,
        theme_css=css,
        theme=themes.effective_id(project.slug),
        theme_meta=meta,
        citation_mode=citation_mode or os.environ.get("GZH_CITATION_MODE", "strip"),
        author=settings.author,
    )
    brief = read_text(project.dir / "brief.md", "") or ""
    lint = lint_article(
        project,
        target_chars=target_chars_from_brief(brief) or int(project.meta.get("targetChars") or 0),
        original_mode=True,
    )
    source = check_sources(project, "article.md")
    checks = {"lint": lint, "sourceCheck": source, "renderedAt": info["renderedAt"], "theme": info["theme"]}
    write_json(project.dir / "checks.json", checks)
    return info, checks


def preview_html(
    project: Project,
    *,
    themes: ThemeLibrary,
    settings: Settings,
    text: str | None = None,
) -> str:
    """不落盘的实时预览整页 HTML（图片内联 data URL），供写作页 iframe srcDoc。"""
    if text is None:
        text = read_text(project.dir / "article.md", None)
        if text is None:
            raise NotFound("还没有 article.md", "NO_ARTICLE")
    css, meta = resolve_sheet(themes, project.slug)
    result = compose_article(
        project,
        text,
        theme_css=css,
        theme=themes.effective_id(project.slug),
        theme_meta=meta,
        citation_mode=os.environ.get("GZH_CITATION_MODE", "strip"),
        author=settings.author,
    )
    return result["previewHtml"]


def apply_title(
    project: Project,
    *,
    title: str,
    digest: str,
    settings: Settings,
    themes: ThemeLibrary,
) -> dict[str, Any]:
    """用户选定标题/摘要：写回 frontmatter、渲染、检查，项目标记完成。"""
    title = str(title or "").strip()
    digest = str(digest or "").strip()
    if not title:
        raise ApiError("请选择一个标题", "MISSING_TITLE")
    article_path = project.dir / "article.md"
    article = read_text(article_path, None)
    if article is None:
        raise NotFound("还没有 article.md", "NO_ARTICLE")
    article = set_frontmatter_value(article, "title", title)
    if digest:
        article = set_frontmatter_value(article, "digest", digest)
    if settings.author:
        article = set_frontmatter_value(article, "author", settings.author)
    article_path.write_text(article.rstrip() + "\n", "utf8")
    info, checks = render_project(project, settings=settings, themes=themes)
    return {"info": info, "checks": checks, "article": article}


def deliverable(project: Project) -> dict[str, Any]:
    html_path = project.dir / "dist/article.html"
    if not html_path.exists():
        raise NotFound("还没有排版产物（dist/article.html）；点「重新排版」生成。", "NO_DELIVERABLE")
    raw = html_path.read_text("utf8")
    inlined = raw
    for src in set(re.findall(r'<img src="([^"]+)"', raw)):
        if re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) or src.startswith("/"):
            continue
        data = local_image_data_url(project.dir, src)
        if data:
            inlined = inlined.replace(f'src="{src}"', f'src="{data}"')
    article = project.dir / "article.md"
    rendered_at = html_path.stat().st_mtime * 1000
    article_changed = article.stat().st_mtime * 1000 if article.exists() else None
    checks = read_json(project.dir / "checks.json", None)
    return {
        "html": inlined,
        "copyHtml": raw,
        "renderedAt": rendered_at,
        "articleChangedAt": article_changed,
        "stale": bool(article_changed and article_changed > rendered_at + 1000),
        "pending": raw.count("【配图待补】"),
        "images": len(re.findall(r"<img ", raw)),
        "chars": ((checks or {}).get("lint") or {}).get("summary", {}).get("chars", 0),
        "theme": ((checks or {}).get("theme") or project.meta.get("theme") or "wechat"),
        "checks": checks,
        "articleTitle": (checks or {}).get("lint", {}).get("summary", {}).get("title", {}).get("text")
        or project.meta.get("title")
        or project.slug,
    }
