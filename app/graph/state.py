"""LangGraph 状态字段（v2：文档为中心，只停一次）。"""

from __future__ import annotations

from typing import Any, TypedDict


class WriterState(TypedDict, total=False):
    slug: str
    topic: str
    goal: str
    audience: str
    target_chars: int
    theme: str

    brief: str
    digest: str
    outline: str
    draft: str

    draft_feedback: str

    decision: str  # "approve" | "revise"
    titles: list[str]
    title_options: dict[str, Any]
    selected_title: str
    selected_digest: str

    stage: str
    node: str
    notice: str
    error: str
    updated_at: str
