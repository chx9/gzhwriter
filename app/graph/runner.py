"""图运行器：SQLite checkpointer、后台线程、事件发布、状态查询。"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..config import Settings
from ..errors import ApiError, Conflict
from ..events import EventBus
from ..llm import WriterEngine
from ..storage import ProjectStore, now_iso
from ..styles import StyleStore
from ..render.themes import ThemeLibrary
from .build import build_graph
from .nodes import PipelineNodes
from .state import WriterState


class GraphRunner:
    def __init__(
        self,
        settings: Settings,
        store: ProjectStore,
        styles: StyleStore,
        themes: ThemeLibrary,
        llm: WriterEngine,
        events: EventBus,
    ) -> None:
        self.settings = settings
        self.store = store
        self.styles = styles
        self.themes = themes
        self.llm = llm
        self.events = events
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._write_conn = sqlite3.connect(str(settings.db_path), check_same_thread=False, timeout=30)
        self._read_conn = sqlite3.connect(str(settings.db_path), check_same_thread=False, timeout=30)
        for conn in (self._write_conn, self._read_conn):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        self.saver = SqliteSaver(self._write_conn)
        self.read_saver = SqliteSaver(self._read_conn)
        self.nodes = PipelineNodes(store, styles, themes, llm, events, settings)
        self.graph = build_graph(self.nodes).compile(checkpointer=self.saver)
        self.read_graph = build_graph(self.nodes).compile(checkpointer=self.read_saver)
        self._running: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, slug: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(slug, threading.Lock())

    def _config(self, slug: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": slug}, "recursion_limit": 60}

    def is_running(self, slug: str) -> bool:
        with self._guard:
            return slug in self._running

    def _mark_running(self, slug: str) -> None:
        with self._guard:
            if slug in self._running:
                raise Conflict("这个项目已经在运行中", "RUNNING")
            self._running.add(slug)

    def _mark_done(self, slug: str) -> None:
        with self._guard:
            self._running.discard(slug)

    def _snapshot(self, slug: str):
        with self._lock(slug):
            try:
                return self.read_graph.get_state(self._config(slug))
            except Exception:
                return None

    def _interrupt_of(self, snapshot: Any) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        tasks = getattr(snapshot, "tasks", ()) or ()
        for task in tasks:
            interrupts = getattr(task, "interrupts", ()) or ()
            for item in interrupts:
                value = getattr(item, "value", None)
                if isinstance(value, dict):
                    return value
        return None

    def state(self, slug: str) -> dict[str, Any]:
        workflow: dict[str, Any] = {}
        if self.store.exists(slug):
            try:
                workflow = self.store.load(slug).workflow
            except Exception:
                workflow = {}
        fallback_values = {
            "stage": workflow.get("stage") or "materials",
            "review_round": int(workflow.get("reviewRound") or 0),
            "max_review_rounds": self.settings.max_review_rounds,
        }
        snapshot = self._snapshot(slug)
        if snapshot is None:
            status = "done" if workflow.get("status") == "done" else "idle"
            return {
                "slug": slug,
                "status": status,
                "stage": fallback_values["stage"],
                "node": None,
                "next": [],
                "values": fallback_values,
                "interrupt": None,
                "running": False,
                "hasCheckpoint": False,
                "updatedAt": workflow.get("updatedAt"),
            }
        checkpoint_values = dict(getattr(snapshot, "values", {}) or {})
        has_checkpoint = bool(checkpoint_values)
        values = checkpoint_values or fallback_values
        next_nodes = list(getattr(snapshot, "next", ()) or ())
        interrupt = self._interrupt_of(snapshot)
        running = self.is_running(slug)
        if running:
            status = "running"
        elif interrupt is not None:
            status = "interrupted"
        elif next_nodes:
            status = "resumable"
        elif has_checkpoint or workflow.get("status") == "done":
            # 图已走完（含 titles 完成、等待用户选标题的 awaiting_title）
            status = "done"
        else:
            status = "idle"
        return {
            "slug": slug,
            "status": status,
            # 节点和 /title 等 API 都会把进度写进 project.workflow；以它为准，
            # 保证图走完后用户操作（定标题）能及时反映到状态里。
            "stage": workflow.get("stage") or values.get("stage") or "materials",
            "node": next_nodes[0] if next_nodes else None,
            "next": next_nodes,
            "values": values,
            "interrupt": interrupt,
            "running": running,
            "hasCheckpoint": has_checkpoint,
            "updatedAt": values.get("updated_at") or workflow.get("updatedAt"),
        }

    def _publish_state(self, slug: str, event_type: str = "state") -> None:
        self.events.publish(slug, {"type": event_type, "slug": slug, "state": self.state(slug), "at": now_iso()})

    def _run(self, slug: str, *, resume: dict[str, Any] | None = None, continue_run: bool = False) -> None:
        config = self._config(slug)
        try:
            if resume is not None:
                stream = self.graph.stream(Command(resume=resume), config, stream_mode="updates")
            elif continue_run:
                stream = self.graph.stream(None, config, stream_mode="updates")
            else:
                initial: WriterState = {
                    "slug": slug,
                    "review_round": 0,
                    "max_review_rounds": self.settings.max_review_rounds,
                    "stage": "prepare",
                }
                stream = self.graph.stream(initial, config, stream_mode="updates")
            for update in stream:
                if not isinstance(update, dict):
                    continue
                for node, payload in update.items():
                    self.events.publish(
                        slug,
                        {
                            "type": "node",
                            "slug": slug,
                            "node": node,
                            "status": "completed",
                            "payload": payload if isinstance(payload, dict) else {},
                            "at": now_iso(),
                        },
                    )
                    self._publish_state(slug)
            state = self.state(slug)
            if state["interrupt"]:
                self.events.publish(
                    slug,
                    {"type": "interrupt", "slug": slug, "payload": state["interrupt"], "state": state, "at": now_iso()},
                )
            elif state["status"] == "done":
                self.events.publish(slug, {"type": "done", "slug": slug, "state": state, "at": now_iso()})
            else:
                self._publish_state(slug)
        except Exception as exc:  # noqa: BLE001 - 错误要变成可读状态
            message = getattr(exc, "message", None) or str(exc)
            code = getattr(exc, "code", "RUN_FAILED")
            try:
                self.store.set_stage(slug, "error", "error", error=message, errorCode=code)
            except Exception:
                pass
            self.events.publish(
                slug,
                {
                    "type": "error",
                    "slug": slug,
                    "message": message,
                    "code": code,
                    "detail": traceback.format_exc(limit=8),
                    "at": now_iso(),
                },
            )
            self._publish_state(slug)
        finally:
            self._mark_done(slug)
            self._publish_state(slug)

    def _spawn(self, slug: str, *, resume: dict[str, Any] | None = None, continue_run: bool = False) -> None:
        self._mark_running(slug)
        self.events.publish(slug, {"type": "run_started", "slug": slug, "at": now_iso()})
        thread = threading.Thread(
            target=self._run,
            kwargs={"slug": slug, "resume": resume, "continue_run": continue_run},
            daemon=True,
            name=f"gzh-{slug}",
        )
        thread.start()

    def start(self, slug: str, *, restart: bool = False) -> dict[str, Any]:
        project = self.store.load(slug)
        if not project.meta.get("sources"):
            raise ApiError("还没有素材：请先上传文件、粘贴文字或贴链接。", "NO_MATERIALS")
        if self.is_running(slug):
            raise Conflict("这个项目已经在运行中", "RUNNING")
        state = self.state(slug)
        if state["interrupt"] and not restart:
            raise Conflict("当前停在确认点，请用「继续」提交确认结果。", "INTERRUPTED")
        if state.get("hasCheckpoint") and not restart:
            raise Conflict("这个项目已有运行记录；若要重新开始，请传 restart=true。", "HAS_STATE")
        if restart:
            self.delete_thread(slug)
        self._spawn(slug)
        return {"started": True, "slug": slug}

    def resume(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.is_running(slug):
            raise Conflict("这个项目已经在运行中", "RUNNING")
        state = self.state(slug)
        if not state["interrupt"]:
            raise Conflict("当前没有待确认的检查点", "NOT_INTERRUPTED")
        if not payload.get("action"):
            raise ApiError("缺少 action", "BAD_ACTION")
        self._spawn(slug, resume=payload)
        return {"resumed": True, "slug": slug, "action": payload["action"]}

    def continue_run(self, slug: str) -> dict[str, Any]:
        if self.is_running(slug):
            raise Conflict("这个项目已经在运行中", "RUNNING")
        state = self.state(slug)
        if state["interrupt"]:
            raise Conflict("当前停在确认点，请先用 resume 提交确认结果。", "INTERRUPTED")
        if not state["next"]:
            raise Conflict("没有可继续运行的检查点", "NOTHING_TO_CONTINUE")
        self._spawn(slug, continue_run=True)
        return {"continued": True, "slug": slug}

    def delete_thread(self, slug: str) -> None:
        with self._lock(slug):
            try:
                self.saver.delete_thread(slug)
            except Exception:
                for table in ("writes", "checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                    try:
                        self._write_conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (slug,))
                    except sqlite3.Error:
                        pass
                self._write_conn.commit()

    def wait(self, slug: str, timeout: float = 30) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running(slug):
                return self.state(slug)
            time.sleep(0.05)
        return self.state(slug)

    def close(self) -> None:
        try:
            self._write_conn.close()
            self._read_conn.close()
        except Exception:
            pass
