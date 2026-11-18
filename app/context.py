"""应用级服务容器。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .events import EventBus
from .graph.runner import GraphRunner
from .llm import WriterEngine
from .render.themes import ThemeLibrary
from .storage import ProjectStore
from .styles import StyleStore


@dataclass
class AppContext:
    settings: Settings
    store: ProjectStore
    styles: StyleStore
    themes: ThemeLibrary
    llm: WriterEngine
    events: EventBus
    runner: GraphRunner
