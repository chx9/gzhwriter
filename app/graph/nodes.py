"""流水线节点（v3）。

后台先跑 research（简报 + 消化）和 outline，只 interrupt 一次：大纲完成。
用户确认后直接 draft + titles；正文在编辑器里改，不再走确认点。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from langgraph.types import interrupt

from ..config import Settings
from ..errors import ApiError
from ..events import EventBus
from ..lint import lint_article, target_chars_from_brief
from ..llm import WriterEngine
from ..materials import write_index
from ..sourcecheck import check_sources
from ..storage import (
    Project,
    ProjectStore,
    now_iso,
    parse_frontmatter,
    read_text,
    set_frontmatter_value,
    stringify_frontmatter,
    write_text,
)
from ..styles import StyleStore
from ..render.themes import ThemeLibrary
from .state import WriterState


def _strip_fence(text: str) -> str:
    value = str(text or "").strip()
    match = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$", value, re.I)
    return match.group(1).strip() if match else value



def _extract_article(text: str) -> str:
    """从模型回复里提取真正的 article.md。

    模型常见的两种多余包装：
    1. 前面写一段说明，再用 ```markdown ... ``` 包住全文；
    2. 整段就是围栏，但围栏前后还有客套话。
    处理：取包含 frontmatter 的最长围栏块；去掉 frontmatter 前的说明；
    如果正文里又嵌了一个 frontmatter，合并取标题/摘要。
    """
    value = str(text or "").strip()
    fences = re.findall(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```", value)
    if fences:
        with_fm = [block for block in fences if block.lstrip().startswith("---")]
        value = max(with_fm or fences, key=len).strip()
    # 去掉 frontmatter 之前的说明文字
    if not value.startswith("---"):
        marker = re.search(r"(?m)^---\s*$", value)
        if marker and "title:" in value[marker.start():marker.start() + 400]:
            value = value[marker.start():].strip()
    # 去掉结尾 ``` 残留
    value = re.sub(r"\n```\s*$", "", value).strip()
    # 嵌套 frontmatter：取第一个块，若其正文里还有 title:，合并
    if value.startswith("---"):
        end = value.find("\n---", 4)
        if end >= 0:
            body = value[end + 4:]
            inner = re.search(r"(?s)^\s*---\s*\n(title:[\s\S]*?\n)---\s*\n", body)
            if inner:
                head = value[4:end]
                value = "---\n" + head.rstrip() + "\n" + inner.group(1) + "---\n" + body[inner.end():]
    return value


class PipelineNodes:
    def __init__(
        self,
        store: ProjectStore,
        styles: StyleStore,
        themes: ThemeLibrary,
        llm: WriterEngine,
        events: EventBus,
        settings: Settings,
    ) -> None:
        self.store = store
        self.styles = styles
        self.themes = themes
        self.llm = llm
        self.events = events
        self.settings = settings

    def _project(self, state: WriterState) -> Project:
        slug = state.get("slug")
        if not slug:
            raise ApiError("state 缺少 slug", "BAD_STATE")
        return self.store.load(slug)

    def _stage(self, project: Project, stage: str, status: str | None = None, **extra: Any) -> None:
        self.store.set_stage(project.slug, stage, status or "running", **extra)

    def _normalize_article(self, project: Project, text: str, state: WriterState) -> str:
        value = _extract_article(text)
        title = (
            state.get("selected_title")
            or project.meta.get("title")
            or state.get("topic")
            or project.slug
        )
        digest = state.get("selected_digest") or ""
        author = self.settings.author or ""
        if not value.startswith("---"):
            value = stringify_frontmatter({"title": title, "digest": digest, "author": author}, value)
        else:
            meta, _ = parse_frontmatter(value)
            if not meta.get("title"):
                value = set_frontmatter_value(value, "title", title)
            if "digest" not in meta:
                value = set_frontmatter_value(value, "digest", digest)
            if "author" not in meta and author:
                value = set_frontmatter_value(value, "author", author)
        return value

    # ── nodes ───────────────────────────────────────────────────────

    def prepare(self, state: WriterState) -> dict[str, Any]:
        project = self._project(state)
        if not project.meta.get("sources"):
            raise ApiError("还没有素材：先在「素材」页上传文件、粘贴文字或贴链接。", "NO_MATERIALS")
        self.styles.ensure_session(project.slug)
        write_index(project)
        theme = self.themes.effective_id(project.slug)
        topic = project.meta.get("topic") or project.meta.get("title") or project.slug
        self._stage(project, "research", "running")
        return {
            "slug": project.slug,
            "topic": topic,
            "goal": project.meta.get("goal", ""),
            "audience": project.meta.get("audience", ""),
            "target_chars": int(project.meta.get("targetChars") or self.settings.target_chars),
            "theme": theme,
            "stage": "research",
            "updated_at": now_iso(),
        }

    def research(self, state: WriterState) -> dict[str, Any]:
        """简报 + 消化，一次跑完。产物落盘，但对用户不可见。"""
        project = self._project(state)
        self._stage(project, "research", "running")

        brief = self.llm.brief(
            project,
            topic=state.get("topic", ""),
            goal=state.get("goal", ""),
            audience=state.get("audience", ""),
            target_chars=int(state.get("target_chars") or self.settings.target_chars),
        )
        brief = _strip_fence(brief)
        write_text(project.dir / "brief.md", brief.rstrip() + "\n")

        for source in project.meta.get("sources", []):
            if source.get("excluded"):
                continue
            if source.get("kind") == "spoken" and not (project.dir / str(source["dir"]) / "clean.md").exists():
                content = read_text(project.dir / str(source["dir"]) / "content.md", "") or ""
                cleaned = self.llm.clean(project, source, content)
                write_text(project.dir / str(source["dir"]) / "clean.md", _strip_fence(cleaned).rstrip() + "\n")
            digest = self.llm.digest(project, source)
            write_text(project.dir / str(source["dir"]) / "digest.md", _strip_fence(digest).rstrip() + "\n")

        write_index(project)
        return {
            "brief": brief,
            "stage": "outline",
            "notice": "",
            "updated_at": now_iso(),
        }

    def outline(self, state: WriterState) -> dict[str, Any]:
        project = self._project(state)
        feedback = state.get("outline_feedback", "")
        self._stage(project, "outline", "running")
        outline = self.llm.outline(project, brief=state.get("brief", ""), feedback=feedback)
        outline = _strip_fence(outline)
        write_text(project.dir / "outline.md", outline.rstrip() + "\n")
        return {
            "outline": outline,
            "outline_feedback": "",
            "stage": "outline",
            "notice": "",
            "updated_at": now_iso(),
        }

    def outline_ready(self, state: WriterState) -> dict[str, Any]:
        payload = {
            "kind": "outline",
            "title": "大纲已生成",
            "content": state.get("outline", ""),
            "path": "outline.md",
            "options": {},
            "round": 0,
            "maxRounds": 0,
            "hint": "可以直接改 outline.md，或让 AI 改；满意后点「生成文章」。",
            "notice": "",
            "actions": [
                {"id": "approve", "label": "生成文章"},
                {"id": "revise", "label": "按意见重改大纲"},
            ],
        }
        resumed = interrupt(payload) or {}
        action = resumed.get("action") or "approve"
        feedback = str(resumed.get("feedback") or "").strip()
        if action == "revise" and not feedback:
            action = "approve"
        return {
            "decision": action,
            "outline_feedback": feedback if action == "revise" else "",
            "stage": "outline",
            "updated_at": now_iso(),
        }

    def draft(self, state: WriterState) -> dict[str, Any]:
        project = self._project(state)
        self._stage(project, "draft", "running")
        # 以磁盘上的 outline.md 为准：用户可能在大纲停点直接改过，或让 AI 改过
        outline = read_text(project.dir / "outline.md", "") or state.get("outline", "")
        text = self.llm.draft(
            project,
            brief=state.get("brief", ""),
            outline=outline,
        )
        normalized = self._normalize_article(project, text, state)
        write_text(project.dir / "article.md", normalized.rstrip() + "\n")
        return {
            "draft": normalized,
            "decision": "",
            "stage": "draft",
            "notice": "",
            "updated_at": now_iso(),
        }

    def titles(self, state: WriterState) -> dict[str, Any]:
        project = self._project(state)
        article = read_text(project.dir / "article.md", "") or ""
        self._stage(project, "titles", "running")
        options = self.llm.title(project, article=article, brief=state.get("brief", ""))
        titles = [item.get("title", "") for item in options.get("titles", [])]
        recommended = max(1, min(5, int(options.get("recommended") or 1)))
        self._stage(project, "awaiting_title", "awaiting_title")
        return {
            "titles": titles,
            "title_options": options,
            "selected_title": titles[recommended - 1] if titles else "",
            "selected_digest": options.get("digest", ""),
            "stage": "awaiting_title",
            "notice": "",
            "updated_at": now_iso(),
        }
