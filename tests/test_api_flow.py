from __future__ import annotations

import base64
import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.errors import ApiError
from app.main import create_app

from .conftest import add_text, create_project, resume, wait_idle

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n4sAAAAASUVORK5CYII="
)


def test_full_demo_pipeline_single_pause(client: TestClient) -> None:
    """v2：后台一口气跑完 research+draft，只停一次（初稿）。"""
    slug = create_project(client)
    add_text(client, slug)
    response = client.post(f"/api/projects/{slug}/start", json={})
    assert response.status_code == 200

    state = wait_idle(client, slug)
    assert state["status"] == "interrupted" and state["interrupt"]["kind"] == "draft"
    # 内部产物落盘但不对用户展示
    for internal in ("brief.md", "outline.md", "digest.md"):
        assert (client.app.state.ctx.store.load(slug).dir / internal).exists()

    resume(client, slug, {"action": "approve"})

    state = wait_idle(client, slug)
    assert state["status"] == "done"
    assert state["stage"] == "awaiting_title"
    titles = state["values"]["titles"]
    assert len(titles) == 5

    # 用户在内联候选条选定标题
    picked = client.post(
        f"/api/projects/{slug}/title",
        json={"title": titles[0], "digest": state["values"]["title_options"]["digest"]},
    )
    assert picked.status_code == 200, picked.text

    deliverable = client.get(f"/api/projects/{slug}/deliverable").json()
    html = deliverable["copyHtml"]
    assert "class=" not in html
    assert "<style" not in html
    assert "<script" not in html
    assert "style=" in html
    assert "【配图待补】" in html
    assert "ww-img:pending" not in html
    assert "<img" not in html
    assert deliverable["pending"] == 2
    assert deliverable["checks"]["lint"]["summary"]["errors"] == 0
    article = client.get(f"/api/projects/{slug}/file", params={"path": "article.md"}).json()["text"]
    assert f"title: {titles[0]}" in article
    assert "[A01¶1]" in article
    state = client.get(f"/api/projects/{slug}/state").json()
    assert state["stage"] == "done"


def test_revise_loops_back_to_draft(client: TestClient) -> None:
    slug = create_project(client)
    add_text(client, slug)
    client.post(f"/api/projects/{slug}/start", json={})
    wait_idle(client, slug)
    resume(client, slug, {"action": "revise", "feedback": "开头再具体一点"})
    state = wait_idle(client, slug)
    assert state["status"] == "interrupted"
    assert state["interrupt"]["kind"] == "draft"
    assert state["values"]["draft_feedback"] == ""
    resume(client, slug, {"action": "approve"})
    state = wait_idle(client, slug)
    assert state["stage"] == "awaiting_title"


def test_review_is_on_demand_api(client: TestClient) -> None:
    slug = create_project(client)
    add_text(client, slug)
    project = client.app.state.ctx.store.load(slug)
    (project.dir / "article.md").write_text(
        "---\ntitle: 测试\ndigest: 摘要\n---\n\n正文。[A01¶1]\n", "utf8"
    )
    response = client.post(f"/api/projects/{slug}/review")
    assert response.status_code == 200, response.text
    assert "评审" in response.json()["review"]
    assert (project.dir / "reviews.md").exists()


def test_live_preview_endpoint(client: TestClient) -> None:
    slug = create_project(client)
    add_text(client, slug)
    project = client.app.state.ctx.store.load(slug)
    (project.dir / "article.md").write_text(
        "---\ntitle: 测试\ndigest: 摘要\n---\n\n## 01 / 小节\n\n正文。[A01¶1]\n", "utf8"
    )
    response = client.post(f"/api/projects/{slug}/preview", json={})
    assert response.status_code == 200
    data = response.json()
    html = data["html"]
    assert 'id="moxia"' in html  # 渲染片段（行内样式，无 class）
    assert "class=" not in html
    assert "[A01¶1]" not in html
    assert data["title"] == "测试"
    assert data["minutes"] >= 1
    # 不落盘：dist 仍然不存在
    assert not (project.dir / "dist/article.html").exists()
    # 传入自定义 markdown
    response2 = client.post(
        f"/api/projects/{slug}/preview",
        json={"markdown": "---\ntitle: x\ndigest: y\n---\n\n临时内容。"},
    )
    assert "临时内容" in response2.json()["html"]
    assert response2.json()["title"] == "x"


def test_restart_resumes_from_checkpoint(tmp_path: Path) -> None:
    from app.config import load_settings

    base = load_settings(tmp_path / "data")
    settings = replace(base, demo=True, seed=False, api_key="")
    app1 = create_app(settings, seed=False)
    with TestClient(app1) as client:
        slug = create_project(client)
        add_text(client, slug)
        client.post(f"/api/projects/{slug}/start", json={})
        state = wait_idle(client, slug)
        assert state["interrupt"]["kind"] == "draft"

    app2 = create_app(settings, seed=False)
    with TestClient(app2) as client:
        state = client.get(f"/api/projects/{slug}/state").json()
        assert state["status"] == "interrupted"
        assert state["interrupt"]["kind"] == "draft"
        resume(client, slug, {"action": "approve"})
        state = wait_idle(client, slug)
        assert state["stage"] == "awaiting_title"


def test_comments_crud(client: TestClient) -> None:
    slug = create_project(client)
    created = client.post(
        f"/api/projects/{slug}/comments",
        json={"id": "c_test", "path": "article.md", "body": "这一段再具体一点", "lineStart": 3, "lineEnd": 3},
    )
    assert created.status_code == 200
    comments = client.get(f"/api/projects/{slug}/comments").json()["comments"]
    assert len(comments) == 1 and comments[0]["status"] == "open"
    patched = client.patch(f"/api/projects/{slug}/comments/c_test", json={"status": "sent"})
    assert patched.status_code == 200 and patched.json()["status"] == "sent"
    deleted = client.delete(f"/api/projects/{slug}/comments/c_test")
    assert deleted.status_code == 200
    assert client.get(f"/api/projects/{slug}/comments").json()["comments"] == []


def test_path_traversal_and_whitelist(client: TestClient) -> None:
    slug = create_project(client)
    response = client.get(f"/api/projects/{slug}/file", params={"path": "../../etc/passwd"})
    assert response.status_code == 400
    response = client.get(f"/api/projects/{slug}/raw/../../etc/passwd")
    assert response.status_code in (400, 404)
    add_text(client, slug)
    response = client.put(
        f"/api/projects/{slug}/file",
        json={"path": "sources/A01-素材一/content.md", "text": "被改了"},
    )
    assert response.status_code == 403


def test_slots_fill_and_clear(client: TestClient) -> None:
    slug = create_project(client)
    article = """---
title: 测试
digest: 摘要
---

第一段。

![图注](ww-img:pending "google: test keyword | 中文")
"""
    (client.app.state.ctx.store.load(slug).dir / "article.md").write_text(article, "utf8")
    slots = client.get(f"/api/projects/{slug}/slots").json()
    assert slots["pending"] == 1
    slot = slots["slots"][0]
    filled = client.post(
        f"/api/projects/{slug}/slots/fill",
        json={
            "line": slot["line"],
            "raw": slot["raw"],
            "dataUrl": "data:image/png;base64," + base64.b64encode(PNG).decode(),
        },
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["pending"] == 0
    project = client.app.state.ctx.store.load(slug)
    assert (project.dir / "images/fig-01.png").exists()
    text = (project.dir / "article.md").read_text("utf8")
    assert "images/fig-01.png" in text
    deliverable = client.get(f"/api/projects/{slug}/deliverable").json()
    assert "data:image/png;base64" in deliverable["html"]
    assert "images/fig-01.png" in deliverable["copyHtml"]
    slot2 = client.get(f"/api/projects/{slug}/slots").json()["slots"][0]
    cleared = client.post(f"/api/projects/{slug}/slots/clear", json={"line": slot2["line"], "raw": slot2["raw"]})
    assert cleared.status_code == 200
    assert cleared.json()["pending"] == 1
    assert "ww-img:pending" in (project.dir / "article.md").read_text("utf8")


def test_theme_switch_does_not_change_article(client: TestClient) -> None:
    slug = create_project(client)
    add_text(client, slug)
    article = """---
title: 测试
digest: 摘要
---

## 01 / 小节

正文。[A01¶1]
"""
    project = client.app.state.ctx.store.load(slug)
    (project.dir / "article.md").write_text(article, "utf8")
    before = (project.dir / "article.md").read_text("utf8")
    response = client.post(f"/api/projects/{slug}/theme", json={"theme": "swiss", "rerender": True})
    assert response.status_code == 200, response.text
    assert (project.dir / "article.md").read_text("utf8") == before
    assert (project.dir / "dist/article.html").exists()
    themes = client.get("/api/themes").json()["themes"]
    assert len(themes) == 11


def test_style_promote_and_reset(client: TestClient, settings) -> None:
    slug = create_project(client)
    listed = client.get(f"/api/projects/{slug}/styles").json()
    assert {item["kind"] for item in listed["files"]} == {"writing", "layout"}
    text = "# 新文笔\n\n只改本项目的文笔。\n"
    saved = client.put(f"/api/projects/{slug}/styles", json={"kind": "writing", "text": text})
    assert saved.status_code == 200
    promoted = client.post(f"/api/projects/{slug}/styles/action", json={"kind": "writing", "action": "promote"})
    assert promoted.status_code == 200
    assert (settings.styles_root / "writing-style.md").read_text("utf8").startswith("# 新文笔")
    reset = client.post(f"/api/projects/{slug}/styles/reset", json={"kind": "writing"})
    assert reset.status_code == 200
    assert "只改本项目" in (settings.styles_root / "sessions" / slug / "writing-style.md").read_text("utf8")


def test_delete_project_removes_directory_and_checkpoint(client: TestClient, settings) -> None:
    slug = create_project(client)
    add_text(client, slug)
    client.post(f"/api/projects/{slug}/start", json={})
    wait_idle(client, slug)
    project_dir = settings.projects_root / slug
    assert project_dir.exists()
    response = client.delete(f"/api/projects/{slug}")
    assert response.status_code == 200
    assert not project_dir.exists()
    assert client.get(f"/api/projects/{slug}").status_code == 404
    assert client.get(f"/api/projects/{slug}/state").status_code == 404


def test_upload_markdown_material(client: TestClient) -> None:
    slug = create_project(client)
    response = client.post(
        f"/api/projects/{slug}/materials/upload",
        files=[("files", ("note.md", "# 上传的素材\n\n[¶1] 这是上传文件里的第一段。\n\n[¶2] 第二段。".encode(), "text/markdown"))],
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results[0]["status"] == "imported"
    assert results[0]["id"] == "A01"
    sources = client.get(f"/api/projects/{slug}/sources").json()["sources"]
    assert sources[0]["origin"]["name"] == "note.md"


def test_import_docx(client: TestClient, tmp_path: Path) -> None:
    from docx import Document

    slug = create_project(client)
    document = Document()
    document.add_paragraph("这是 docx 的第一段。")
    document.add_paragraph("这是 docx 的第二段。")
    path = tmp_path / "sample.docx"
    document.save(path)
    response = client.post(
        f"/api/projects/{slug}/materials/upload",
        files=[("files", ("sample.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
    )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "imported"
    assert result["paragraphs"] >= 2


def test_import_url(client: TestClient) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = """<html><head><title>网页素材</title></head><body>
            <div id="js_content"><h1>网页标题</h1><p>网页里的第一段。</p><p>网页里的第二段。</p></div>
            <script>ignored()</script></body></html>""".encode()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        slug = create_project(client)
        response = client.post(
            f"/api/projects/{slug}/materials/url",
            json={"url": f"http://127.0.0.1:{server.server_port}/article"},
        )
        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        assert result["status"] == "imported"
        assert result["title"] == "网页素材"
    finally:
        server.shutdown()
        server.server_close()


def test_edit_endpoint_keeps_article_and_renders(client: TestClient) -> None:
    slug = create_project(client)
    project = client.app.state.ctx.store.load(slug)
    (project.dir / "article.md").write_text(
        "---\ntitle: 测试\ndigest: 摘要\n---\n\n第一段要改。\n\n第二段保留。\n",
        "utf8",
    )
    response = client.post(
        f"/api/projects/{slug}/edit",
        json={"instruction": "把第一段改得更具体", "selected": "第一段要改。"},
    )
    assert response.status_code == 200, response.text
    article = (project.dir / "article.md").read_text("utf8")
    assert "第二段保留" in article
    assert (project.dir / "dist/article.html").exists()


def test_bilibili_material_import(client: TestClient, monkeypatch) -> None:
    from app.api import routes

    srt = """1
00:00:01,000 --> 00:00:04,500
大家好，今天聊聊定期存款怎么分层。

2
00:00:05,000 --> 00:00:09,000
先问这笔钱什么时候要用，再决定存多久。
"""
    monkeypatch.setattr(
        routes,
        "fetch_bilibili_subtitles",
        lambda url, *, cookies_browser=None, cookie_file=None: {"title": "测试视频标题", "srt": srt},
    )
    slug = create_project(client)
    response = client.post(
        f"/api/projects/{slug}/materials/bilibili",
        json={"url": "https://www.bilibili.com/video/BV1xx411c7mD"},
    )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "imported"
    assert result["id"] == "S01"  # 字幕 → 口语稿
    assert result["kind"] == "spoken"
    sources = client.get(f"/api/projects/{slug}/sources").json()["sources"]
    assert sources[0]["origin"]["type"] == "url"
    assert "bilibili.com" in sources[0]["origin"]["url"]
    # 时间锚点进了 content.md
    source_dir = sources[0]["dir"]
    content = client.app.state.ctx.store.load(slug).dir / source_dir / "content.md"
    assert "[¶1 @00:01]" in content.read_text("utf8")


def test_bilibili_bad_url(client: TestClient, monkeypatch) -> None:
    from app.api import routes

    monkeypatch.setattr(
        routes,
        "fetch_bilibili_subtitles",
        lambda url, *, cookies_browser=None, cookie_file=None: (_ for _ in ()).throw(ApiError("这不是 B 站链接", "BAD_URL")),
    )
    slug = create_project(client)
    response = client.post(f"/api/projects/{slug}/materials/bilibili", json={"url": "https://example.com/x"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "BAD_URL"


def test_bilibili_srt_conversion() -> None:
    from app.bilibili import _srt_from_bilibili_json, _srt_time

    assert _srt_time(3661.5) == "01:01:01,500"
    srt = _srt_from_bilibili_json(
        {"body": [{"from": 0.0, "to": 2.0, "content": "第一句"}, {"from": 2.5, "to": 4.0, "content": "第二句"}]}
    )
    assert "1\n00:00:00,000 --> 00:00:02,000\n第一句" in srt
    assert "第二句" in srt
