"""进程内 SSE 事件总线。事件只是通知；状态以 SQLite 为准。"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._lock = threading.RLock()
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._history_size = history

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def publish(self, slug: str, event: dict[str, Any]) -> None:
        with self._lock:
            history = self._history.setdefault(slug, deque(maxlen=self._history_size))
            history.append(event)
            queues = list(self._subscribers.get(slug, set()))
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in queues:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass

    def snapshot(self, slug: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(slug, []))

    def subscribe(self, slug: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(slug, set()).add(queue)
        for event in self.snapshot(slug):
            queue.put_nowait(event)
        return queue

    def unsubscribe(self, slug: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.get(slug, set()).discard(queue)
            if not self._subscribers.get(slug):
                self._subscribers.pop(slug, None)
