"""FastAPI 入口。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import ROOT, Settings, load_settings
from .context import AppContext
from .errors import ApiError
from .events import EventBus
from .graph.runner import GraphRunner
from .llm import WriterEngine
from .render.themes import ThemeLibrary
from .seed import seed_sample
from .storage import ProjectStore
from .styles import StyleStore

WEB_DIST = ROOT / "web" / "dist"


def create_app(settings: Settings | None = None, *, seed: bool | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=getattr(logging, settings.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        store = ProjectStore(settings.projects_root, settings.styles_root)
        styles = StyleStore(settings.styles_root)
        themes = ThemeLibrary(settings.styles_root, settings.default_theme)
        events = EventBus()
        events.bind_loop(asyncio.get_running_loop())
        llm = WriterEngine(settings, styles, themes)
        runner = GraphRunner(settings, store, styles, themes, llm, events)
        app.state.ctx = AppContext(settings, store, styles, themes, llm, events, runner)
        if seed is None:
            seed_sample(settings, store, styles, themes)
        elif seed:
            seed_sample(settings, store, styles, themes)
        yield
        runner.close()

    app = FastAPI(title="稿纸 · 公众号写作工作台", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return JSONResponse({"detail": {"message": exc.message, "code": exc.code}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logging.exception("unhandled error")
        return JSONResponse(
            {"detail": {"message": str(exc) or "服务器内部错误", "code": "INTERNAL"}},
            status_code=500,
        )

    app.include_router(router)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return RedirectResponse("/favicon.svg")

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def missing_frontend():
            return HTMLResponse(
                """<!doctype html><meta charset="utf-8"><title>稿纸</title>
<body style="font-family:system-ui;max-width:680px;margin:80px auto;line-height:1.7">
<h1>稿纸 · 公众号写作工作台</h1>
<p>后端已启动，但还没有前端构建产物。</p>
<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>API 文档：<a href="/docs">/docs</a> · 健康检查：<a href="/api/health">/api/health</a></p>
</body>"""
            )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
