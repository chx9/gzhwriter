"""LangGraph 状态字段（v3：大纲停一次）。"""

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
    outline: str
    draft: str

    outline_feedback: str

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
