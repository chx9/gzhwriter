"""LangGraph 图定义（v3：prepare → research → outline → 唯一一次 interrupt → draft → titles → END）。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import PipelineNodes
from .state import WriterState


def _outline_ready_route(state: WriterState) -> str:
    return "outline" if state.get("decision") == "revise" else "draft"


def build_graph(nodes: PipelineNodes):
    builder = StateGraph(WriterState)
    builder.add_node("prepare", nodes.prepare)
    builder.add_node("research", nodes.research)
    builder.add_node("outline", nodes.outline)
    builder.add_node("outline_ready", nodes.outline_ready)
    builder.add_node("draft", nodes.draft)
    builder.add_node("titles", nodes.titles)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "research")
    builder.add_edge("research", "outline")
    builder.add_edge("outline", "outline_ready")
    builder.add_conditional_edges(
        "outline_ready",
        _outline_ready_route,
        {"outline": "outline", "draft": "draft"},
    )
    builder.add_edge("draft", "titles")
    builder.add_edge("titles", END)
    return builder
