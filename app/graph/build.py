"""LangGraph 图定义（v2：prepare → research → draft → 唯一一次 interrupt → titles → END）。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import PipelineNodes
from .state import WriterState


def _draft_ready_route(state: WriterState) -> str:
    return "draft" if state.get("decision") == "revise" else "titles"


def build_graph(nodes: PipelineNodes):
    builder = StateGraph(WriterState)
    builder.add_node("prepare", nodes.prepare)
    builder.add_node("research", nodes.research)
    builder.add_node("draft", nodes.draft)
    builder.add_node("draft_ready", nodes.draft_ready)
    builder.add_node("titles", nodes.titles)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "research")
    builder.add_edge("research", "draft")
    builder.add_edge("draft", "draft_ready")
    builder.add_conditional_edges(
        "draft_ready",
        _draft_ready_route,
        {"draft": "draft", "titles": "titles"},
    )
    builder.add_edge("titles", END)
    return builder
