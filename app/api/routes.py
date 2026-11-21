"""HTTP API。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .. import __version__
from ..comments import add_comment, delete_comment, read_comments, update_comment, write_comments
from ..context import AppContext
from ..errors import ApiError, Conflict, NotFound
from ..images import clear_slot, fill_slot, list_slots
from ..bilibili import fetch_bilibili_subtitles
from ..materials import import_materials, read_refs
from ..render_service import apply_title
from ..render_service import deliverable as build_deliverable
from ..render_service import render_project
from ..storage import safe_join
from ..transfer import build_export, import_upload

router = APIRouter(prefix="/api")


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _project(request: Request, slug: str):
    return ctx(request).store.load(slug)


def _summary(request: Request, slug: str) -> dict[str, Any]:
    for item in ctx(request).store.list():
        if item["slug"] == slug:
            return item
    return {"slug": slug}


def _fetch_url(url: str, *, max_bytes: int = 2 * 1024 * 1024) -> dict[str, Any]:
    if not re.match(r"^https?://", url, re.I):
        raise ApiError("只支持 http(s) 链接", "BAD_URL")
    try:
        with httpx.Client(follow_redirects=True, timeout=25.0) as client:
            response = client.get(
                url,
                headers={
                    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 gzh-langraph",
                    "accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                },
            )
    except httpx.HTTPError as exc:
        raise ApiError(f"抓取失败：{exc}", "FETCH_FAILED", 502) from exc
    content = response.content[:max_bytes]
    kind = "html" if "html" in response.headers.get("content-type", "").lower() or b"<html" in content[:2048].lower() else "text"
    return {
        "statusCode": response.status_code,
        "kind": kind,
        "content": content.decode("utf-8", "replace"),
        "truncated": len(response.content) > max_bytes,
        "url": url,
    }


# ── health / projects ───────────────────────────────────────────────


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    app = ctx(request)
    base = (
        app.settings.anthropic_base_url
        if app.settings.provider == "anthropic"
        else app.settings.base_url
    )
    return {
        "ok": True,
        "version": __version__,
        "demo": app.llm.demo_mode,
        "provider": app.settings.provider,
        "model": None if app.llm.demo_mode else app.settings.model,
        "baseUrl": None if app.llm.demo_mode else base,
        "dataDir": str(app.settings.data_dir),
        "maxReviewRounds": app.settings.max_review_rounds,
        "maxTokens": int(os.environ.get("GZH_MAX_TOKENS", "1000000")),
    }


@router.get("/projects")
def projects(request: Request) -> dict[str, Any]:
    return {"projects": ctx(request).store.list()}


@router.post("/projects")
def create_project(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    title = str(payload.get("title") or payload.get("topic") or "").strip()
    if not title:
        raise ApiError("请填写文章标题或选题", "MISSING_TITLE")
    theme = str(payload.get("theme") or app.settings.default_theme)
    if not app.themes.read_css(theme):
        theme = app.settings.default_theme
    project = app.store.create(
        title=title,
        slug=payload.get("slug") or None,
        topic=str(payload.get("topic") or title),
        goal=str(payload.get("goal") or ""),
        audience=str(payload.get("audience") or ""),
        target_chars=int(payload.get("targetChars") or payload.get("target_chars") or app.settings.target_chars),
        theme=theme,
    )
    app.styles.ensure_session(project.slug)
    try:
        app.themes.set_session_theme(project.slug, theme)
    except ApiError:
        pass
    return {"project": _summary(request, project.slug), "state": app.runner.state(project.slug)}


@router.post("/projects/import")
async def import_project(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """导入文章：支持导出的 .zip 整项目包，或单篇 .md。"""
    app = ctx(request)
    data = await file.read()
    project = await asyncio.to_thread(
        import_upload,
        data,
        file.filename or "upload.zip",
        store=app.store,
        styles=app.styles,
        themes=app.themes,
        settings=app.settings,
    )
    return {"project": _summary(request, project.slug), "state": app.runner.state(project.slug)}


@router.get("/projects/{slug}/export")
def export_project(request: Request, slug: str) -> Response:
    """把整篇文章（正文、素材、配图、过程稿、排版产物）打成 ZIP 下载。"""
    app = ctx(request)
    project = app.store.load(slug)
    if app.runner.is_running(slug):
        raise Conflict("文章正在运行，等它停下再导出", "RUNNING")
    data, filename = build_export(project)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{slug}")
def project_detail(request: Request, slug: str) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    return {
        "project": {**project.meta, "slug": project.slug},
        "summary": _summary(request, slug),
        "state": app.runner.state(slug),
        "checks": build_checks_quiet(project),
    }


@router.delete("/projects/{slug}")
def delete_project(request: Request, slug: str) -> dict[str, Any]:
    app = ctx(request)
    if app.runner.is_running(slug):
        raise Conflict("项目正在运行，先等它停下再删除", "RUNNING")
    app.store.load(slug)
    app.runner.delete_thread(slug)
    app.store.delete(slug)
    return {"deleted": True, "slug": slug}


# ── materials ───────────────────────────────────────────────────────


@router.post("/projects/{slug}/materials/upload")
async def upload_materials(request: Request, slug: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    inputs: list[dict[str, Any]] = []
    for file in files:
        name = Path(file.filename or "upload.txt").name
        ext = Path(name).suffix.lower()
        if ext not in {".md", ".markdown", ".txt", ".html", ".htm", ".srt", ".vtt", ".docx"}:
            raise ApiError(f"不支持的文件类型：{name}；支持 .md .txt .html .srt .vtt .docx", "UNSUPPORTED")
        data = await file.read()
        if len(data) > app.settings.max_upload_bytes:
            raise ApiError(f"{name} 超过上传上限", "TOO_LARGE", 413)
        inbox = project.dir / "sources/inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        target = inbox / name
        if target.exists():
            target = inbox / f"{Path(name).stem}-{int(target.stat().st_mtime)}-{Path(name).suffix}"
        target.write_bytes(data)
        inputs.append({"path": str(target), "fromInbox": True, "title": Path(name).stem})
    results = import_materials(app.store, project, inputs)
    return {"results": results, "project": _summary(request, slug), "sources": project.meta.get("sources", [])}


@router.post("/projects/{slug}/materials/text")
def text_material(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    text = str(payload.get("text") or "")
    if not text.strip():
        raise ApiError("粘贴内容不能为空", "EMPTY")
    kind = payload.get("kind") if payload.get("kind") in {"written", "spoken"} else None
    item = {"text": text, "title": payload.get("title") or None}
    if kind:
        item["kind"] = kind
    results = import_materials(app.store, project, [item])
    return {"results": results, "project": _summary(request, slug), "sources": project.meta.get("sources", [])}


@router.post("/projects/{slug}/materials/bilibili")
async def bilibili_material(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """B 站视频 → 字幕 → 口语稿素材（Sxx，带时间锚点）。"""
    import os

    app = ctx(request)
    project = app.store.load(slug)
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ApiError("请填写 B 站视频链接", "BAD_URL")
    result = await asyncio.to_thread(
        fetch_bilibili_subtitles,
        url,
        cookies_browser=os.environ.get("GZH_BILIBILI_BROWSER") or None,
        cookie_file=os.environ.get("GZH_BILIBILI_COOKIE_FILE") or None,
    )
    results = import_materials(
        app.store,
        project,
        [{"text": result["srt"], "title": result["title"], "kind": "spoken", "origin_url": url}],
    )
    return {"results": results, "project": _summary(request, slug), "sources": project.meta.get("sources", [])}


@router.post("/projects/{slug}/materials/url")
def url_material(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ApiError("请填写链接", "BAD_URL")
    fetched = _fetch_url(url)
    results = import_materials(
        app.store,
        project,
        [{"url": url, "title": payload.get("title") or None}],
        fetch_url=lambda _: fetched,
    )
    return {"results": results, "project": _summary(request, slug), "sources": project.meta.get("sources", [])}


@router.get("/projects/{slug}/sources")
def sources(request: Request, slug: str) -> dict[str, Any]:
    project = _project(request, slug)
    index = ""
    path = project.dir / "sources/index.md"
    if path.exists():
        index = path.read_text("utf8")
    return {"sources": project.meta.get("sources", []), "index": index}


@router.get("/projects/{slug}/sources/{source_id}/read")
def read_source(request: Request, slug: str, source_id: str, ref: str | None = None, version: str = "content", context: int = 0) -> dict[str, Any]:
    project = _project(request, slug)
    tokens = [ref] if ref else [source_id]
    result = read_refs(project, tokens, context=min(3, max(0, context)), version=version)
    return {"refs": result}


# ── workflow ────────────────────────────────────────────────────────


@router.post("/projects/{slug}/start")
def start(request: Request, slug: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    return app.runner.start(slug, restart=bool((payload or {}).get("restart")))


@router.post("/projects/{slug}/resume")
def resume(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    return app.runner.resume(slug, payload)


@router.post("/projects/{slug}/continue")
def continue_run(request: Request, slug: str) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    return app.runner.continue_run(slug)


@router.get("/projects/{slug}/state")
def state(request: Request, slug: str) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    return app.runner.state(slug)


@router.get("/projects/{slug}/events")
async def events(request: Request, slug: str) -> StreamingResponse:
    app = ctx(request)
    app.store.load(slug)
    queue = app.events.subscribe(slug)

    async def generator():
        try:
            yield f"event: snapshot\ndata: {json.dumps({'type': 'snapshot', 'state': app.runner.state(slug)}, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                event_type = event.get("type", "message")
                yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            app.events.unsubscribe(slug, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "connection": "keep-alive", "x-accel-buffering": "no"},
    )


@router.post("/projects/{slug}/edit")
async def edit_article(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """AI 改稿：默认 article.md，也支持其他可编辑文件（digest.md / clean.md / outline.md 等）。"""
    app = ctx(request)
    project = app.store.load(slug)
    if app.runner.is_running(slug):
        raise Conflict("流水线正在运行，等它停下再改", "RUNNING")
    instruction = str(payload.get("instruction") or "").strip()
    if not instruction:
        raise ApiError("请填写要求", "MISSING_INSTRUCTION")
    target = str(payload.get("file") or "article.md")
    from ..storage import is_editable, safe_join

    target_path = safe_join(project.dir, target)
    if not target_path.exists():
        raise NotFound(f"文件不存在：{target}", "NO_FILE")
    if not is_editable(target):
        raise ApiError("这个文件不允许修改（原件只读；素材请改 digest.md / clean.md）", "NOT_EDITABLE", 403)
    original = target_path.read_text("utf8")
    selected = str(payload.get("selected") or "").strip()
    edited = await asyncio.to_thread(
        app.llm.edit,
        project,
        article=original,
        instruction=instruction,
        selected=selected,
        file_label=target,
    )
    result: dict[str, Any] = {"before": original, "article": edited, "file": target, "applied": False}
    # 默认只返回 diff 预览，不落盘；用户在界面上接受后才写入（apply=true）。
    if bool(payload.get("apply", False)):
        target_path.write_text(edited.rstrip() + "\n", "utf8")
        result["applied"] = True
        if target == "article.md":
            info, checks = await asyncio.to_thread(
                render_project, project, settings=app.settings, themes=app.themes
            )
            result.update({"info": info, "checks": checks, "deliverable": build_deliverable(project)})
    return result


# ── 实时预览 / 标题 / 按需评审 ──────────────────────────────────────


@router.post("/projects/{slug}/preview")
def live_preview(request: Request, slug: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """不落盘的实时预览：传 markdown 用传入的，否则读 article.md。返回整页 HTML。"""
    app = ctx(request)
    project = app.store.load(slug)
    text = payload.get("markdown") if payload else None
    if text is None:
        from ..storage import read_text

        text = read_text(project.dir / "article.md", None)
        if text is None:
            raise NotFound("还没有 article.md", "NO_ARTICLE")
    from ..render_service import resolve_sheet
    from ..render.render import compose_article

    css, meta = resolve_sheet(app.themes, slug)
    result = compose_article(
        project,
        text,
        theme_css=css,
        theme=app.themes.effective_id(slug),
        theme_meta=meta,
        author=app.settings.author,
    )
    return {
        "html": result["previewFragment"],
        "title": result["title"],
        "by": result.get("byline") or "",
        "minutes": result.get("minutes") or 1,
        "renderedAt": result["renderedAt"],
    }


@router.post("/projects/{slug}/title")
async def pick_title(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """用户从候选条选定标题/摘要：写回 frontmatter、渲染、标记完成。"""
    app = ctx(request)
    project = app.store.load(slug)
    if app.runner.is_running(slug):
        raise Conflict("流水线正在运行，等它停下再定标题", "RUNNING")
    result = await asyncio.to_thread(
        apply_title,
        project,
        title=str(payload.get("title") or ""),
        digest=str(payload.get("digest") or ""),
        settings=app.settings,
        themes=app.themes,
    )
    app.store.set_stage(slug, "done", "done", title=str(payload.get("title") or ""))
    return {**result, "deliverable": build_deliverable(project)}


# ── files / deliverable / checks ────────────────────────────────────


@router.get("/projects/{slug}/files")
def files(request: Request, slug: str, scope: str = "project") -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    if scope == "styles":
        return app.styles.list_session(slug)
    return app.store.files(slug)


@router.get("/projects/{slug}/file")
def read_file(request: Request, slug: str, path: str, scope: str = "project", maxBytes: int | None = None) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    if scope == "styles":
        if path != "writing-style.md":
            raise ApiError("只能查看文笔风格文件", "BAD_PATH")
        directory = app.styles.ensure_session(slug)
        guide = app.styles.read(directory, "writing")
        return {"path": path, "kind": "text", "text": guide["text"], "editable": True, "bytes": len(guide["text"].encode("utf8")), "mtime": 0}
    return app.store.read_file(slug, path, max_bytes=maxBytes or app.settings.max_read_bytes)


@router.put("/projects/{slug}/file")
def write_file(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    path = str(payload.get("path") or "")
    text = payload.get("text")
    if not isinstance(text, str):
        raise ApiError("text 必须是字符串", "BAD_BODY")
    if payload.get("scope") == "styles":
        if path != "writing-style.md":
            raise ApiError("只能编辑文笔风格文件", "BAD_PATH")
        return app.styles.write(app.styles.ensure_session(slug), "writing", text)
    return app.store.write_file(slug, path, text)


@router.get("/projects/{slug}/raw/{path:path}")
def raw_file(request: Request, slug: str, path: str):
    app = ctx(request)
    project = app.store.load(slug)
    target = safe_join(project.dir, path)
    if not target.is_file():
        raise NotFound("文件不存在", "NO_FILE")
    return FileResponse(target)


@router.get("/projects/{slug}/deliverable")
def deliverable(request: Request, slug: str) -> dict[str, Any]:
    project = _project(request, slug)
    return build_deliverable(project)


def build_checks_quiet(project) -> dict[str, Any] | None:
    path = project.dir / "checks.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf8"))
    except Exception:
        return None


@router.get("/projects/{slug}/checks")
def checks(request: Request, slug: str) -> dict[str, Any]:
    project = _project(request, slug)
    existing = build_checks_quiet(project)
    if existing:
        return existing
    _, fresh = render_project(project, settings=ctx(request).settings, themes=ctx(request).themes)
    return fresh


@router.post("/projects/{slug}/render")
def render(request: Request, slug: str) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    info, checks = render_project(project, settings=app.settings, themes=app.themes)
    return {"info": info, "checks": checks, "deliverable": build_deliverable(project)}


# ── image slots ─────────────────────────────────────────────────────


@router.get("/projects/{slug}/slots")
def slots(request: Request, slug: str) -> dict[str, Any]:
    return list_slots(_project(request, slug))


@router.post("/projects/{slug}/slots/fill")
async def fill(request: Request, slug: str) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    content_type = request.headers.get("content-type", "")
    line: int
    raw: str
    data_url = None
    data = None
    mime = None
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        line = int(str(form.get("line") or 0))
        raw = str(form.get("raw") or "")
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ApiError("缺少图片文件", "BAD_IMAGE")
        data = await upload.read()
        mime = getattr(upload, "content_type", None)
    else:
        payload = await request.json()
        line = int(payload.get("line") or 0)
        raw = str(payload.get("raw") or "")
        data_url = payload.get("dataUrl")
        if not data_url:
            raise ApiError("缺少 dataUrl 或文件", "BAD_IMAGE")
    result = fill_slot(project, line=line, raw=raw, data_url=data_url, data=data, mime=mime)
    try:
        info, checks = render_project(project, settings=app.settings, themes=app.themes)
        result["render"] = {"ok": True, "info": info, "checks": checks}
    except Exception as exc:  # noqa: BLE001
        result["render"] = {"ok": False, "code": getattr(exc, "code", "RENDER_FAILED"), "message": str(exc)}
    return result


@router.post("/projects/{slug}/slots/clear")
def clear(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    result = clear_slot(project, line=int(payload.get("line") or 0), raw=str(payload.get("raw") or ""))
    try:
        info, checks = render_project(project, settings=app.settings, themes=app.themes)
        result["render"] = {"ok": True, "info": info, "checks": checks}
    except Exception as exc:  # noqa: BLE001
        result["render"] = {"ok": False, "code": getattr(exc, "code", "RENDER_FAILED"), "message": str(exc)}
    return result


# ── comments ────────────────────────────────────────────────────────


@router.get("/projects/{slug}/comments")
def comments(request: Request, slug: str) -> dict[str, Any]:
    return read_comments(_project(request, slug))


@router.post("/projects/{slug}/comments")
def create_comment(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return add_comment(_project(request, slug), payload)


@router.patch("/projects/{slug}/comments/{comment_id}")
def patch_comment(request: Request, slug: str, comment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return update_comment(_project(request, slug), comment_id, payload)


@router.delete("/projects/{slug}/comments/{comment_id}")
def remove_comment(request: Request, slug: str, comment_id: str) -> dict[str, Any]:
    return delete_comment(_project(request, slug), comment_id)


@router.put("/projects/{slug}/comments")
def replace_comments(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return write_comments(_project(request, slug), payload.get("comments"))


# ── styles / themes ─────────────────────────────────────────────────


def _style_projects(app: AppContext) -> list[dict[str, Any]]:
    sessions = {item["slug"]: item for item in app.styles.sessions()}
    projects = []
    for item in app.store.list():
        session = sessions.get(item["slug"]) or {"changed": []}
        projects.append({"slug": item["slug"], "title": item["title"], "changed": session["changed"]})
    return projects


def _style_payload(app: AppContext) -> dict[str, Any]:
    return {**app.styles.list_global(), "projects": _style_projects(app)}


@router.get("/style")
def global_style(request: Request) -> dict[str, Any]:
    """全局文章风格：所有文章共用一份文笔 / 排版说明。"""
    return _style_payload(ctx(request))


@router.put("/style")
def write_global_style(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    kind = str(payload.get("kind") or "")
    text = payload.get("text")
    if not isinstance(text, str):
        raise ApiError("text 必须是字符串", "BAD_BODY")
    result = app.styles.write_global(
        kind,
        text,
        sync=bool(payload.get("sync", True)),
        overwrite_custom=bool(payload.get("overwriteCustom", False)),
    )
    return {**_style_payload(app), "result": result}


@router.post("/style/sync")
def sync_global_style(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """把全局风格强制覆盖到某篇文章（slug）或全部文章。"""
    app = ctx(request)
    result = app.styles.sync(
        str(payload.get("kind") or ""),
        slug=str(payload.get("slug") or "") or None,
        overwrite_custom=bool(payload.get("overwriteCustom", True)),
    )
    return {**_style_payload(app), "result": result}


@router.post("/style/reset")
def reset_global_style(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """恢复默认风格，并同步到所有文章。"""
    app = ctx(request)
    result = app.styles.reset_global(str(payload.get("kind") or ""), sync=bool(payload.get("sync", True)))
    return {**_style_payload(app), "result": result}


@router.get("/projects/{slug}/styles")
def styles(request: Request, slug: str) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    return app.styles.list_session(slug)


@router.put("/projects/{slug}/styles")
def write_style(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    kind = str(payload.get("kind") or "")
    text = payload.get("text")
    if not isinstance(text, str):
        raise ApiError("text 必须是字符串", "BAD_BODY")
    app.styles.write(app.styles.ensure_session(slug), kind, text)
    return app.styles.list_session(slug)


@router.post("/projects/{slug}/styles/action")
def style_action(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    return app.styles.action(slug, str(payload.get("kind") or ""), str(payload.get("action") or ""))


@router.post("/projects/{slug}/styles/reset")
def style_reset(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    app.store.load(slug)
    return app.styles.action(slug, str(payload.get("kind") or ""), "reset")


@router.get("/themes")
def themes(request: Request) -> dict[str, Any]:
    app = ctx(request)
    return {"default": app.themes.default_id(), "themes": app.themes.list()}


@router.post("/projects/{slug}/theme")
def set_theme(request: Request, slug: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    project = app.store.load(slug)
    theme = str(payload.get("theme") or "")
    if not app.themes.read_css(theme):
        raise ApiError(f"没有这个主题：{theme}", "NO_THEME")
    app.themes.set_session_theme(slug, theme)
    if payload.get("makeDefault"):
        app.themes.set_default(theme)
    project.meta["theme"] = theme
    app.store.save(project)
    response: dict[str, Any] = {"theme": theme, "themes": app.themes.list(), "default": app.themes.default_id()}
    if payload.get("rerender", True) and (project.dir / "article.md").exists():
        info, checks = render_project(project, settings=app.settings, themes=app.themes)
        response["info"] = info
        response["checks"] = checks
        response["deliverable"] = build_deliverable(project)
    return response


@router.get("/themes/{theme}")
def read_theme(request: Request, theme: str) -> dict[str, Any]:
    return ctx(request).themes.read_theme(theme)


@router.put("/themes/{theme}")
def write_theme(request: Request, theme: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    app = ctx(request)
    path = app.themes.write_css(theme, str(payload.get("css") or ""))
    return {**app.themes.read_theme(theme), "path": str(path)}


@router.get("/themes/{theme}/preview")
def preview_theme(request: Request, theme: str) -> dict[str, Any]:
    return ctx(request).themes.preview(theme)
