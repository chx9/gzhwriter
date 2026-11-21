"""文章导入导出：整个项目打包成 ZIP 往返，以及单篇 Markdown 导入。"""

from __future__ import annotations

import io
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from .config import Settings
from .errors import ApiError
from .render.themes import ThemeLibrary
from .render_service import render_project
from .storage import (
    Project,
    ProjectStore,
    now_iso,
    safe_join,
    slugify,
    title_of_markdown,
    write_json,
    write_text,
)
from .styles import StyleStore

EXPORT_FORMAT = "gzh-article"
EXPORT_VERSION = 1
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 2000
MARKDOWN_SUFFIXES = {".md", ".markdown", ".txt"}


def _project_title(project: Project) -> str:
    article = ""
    path = project.dir / "article.md"
    if path.is_file():
        article = path.read_text("utf8", errors="replace")
    return title_of_markdown(article) or str(project.meta.get("title") or project.slug)


def build_export(project: Project) -> tuple[bytes, str]:
    """把项目目录打成一个 ZIP；返回 (字节, 下载文件名)。"""
    buffer = io.BytesIO()
    manifest = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "slug": project.slug,
        "title": _project_title(project),
        "exportedAt": now_iso(),
    }
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for current, dirs, names in os.walk(project.dir):
            dirs[:] = sorted(name for name in dirs if not name.startswith("."))
            for name in sorted(names):
                if name.startswith(".") and name != ".materials-comments.json":
                    continue
                if name.endswith(".tmp") or name == "manifest.json":
                    continue
                path = Path(current) / name
                if not path.is_file():
                    continue
                archive.write(path, path.relative_to(project.dir).as_posix())
    return buffer.getvalue(), f"{project.slug}-{date_stamp()}.zip"


def date_stamp() -> str:
    from datetime import date

    return date.today().isoformat()


def _allocate_slug(store: ProjectStore, base: str) -> str:
    candidate = slugify(base) if str(base or "").strip() else ""
    if not candidate:
        candidate = slugify("imported-article")
    if not store.exists(candidate):
        return candidate
    index = 2
    while True:
        suffixed = f"{candidate[:56].rstrip('-')}-{index}"
        if not store.exists(suffixed):
            return suffixed
        index += 1


def _archive_members(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.endswith("/"):
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ApiError("压缩包里包含符号链接，已拒绝导入", "BAD_ARCHIVE")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ApiError(f"压缩包里的路径不合法：{name}", "BAD_ARCHIVE")
        members.append((info, path))
    if len(members) > MAX_ENTRIES:
        raise ApiError(f"压缩包文件太多（超过 {MAX_ENTRIES} 个）", "BAD_ARCHIVE")
    total = sum(info.file_size for info, _ in members)
    if total > MAX_ARCHIVE_BYTES:
        raise ApiError("压缩包解压后超过 200 MB", "TOO_LARGE", 413)
    return members


def _strip_common_prefix(members: list[tuple[zipfile.ZipInfo, PurePosixPath]]) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """支持「整个项目被包在一层文件夹里」的压缩包。"""
    names = {path.as_posix() for _, path in members}
    if "project.json" in names or "article.md" in names:
        return members
    if not members or not all(len(path.parts) > 1 for _, path in members):
        return members
    prefixes = {path.parts[0] for _, path in members}
    if len(prefixes) != 1:
        return members
    prefix = prefixes.pop()
    if prefix in {"sources", "images", "dist"}:
        return members
    return [(info, PurePosixPath(*path.parts[1:])) for info, path in members]


def _read_text(archive: zipfile.ZipFile, by_name: dict[str, zipfile.ZipInfo], name: str) -> str:
    info = by_name.get(name)
    if info is None:
        return ""
    return archive.read(info).decode("utf-8", "replace")


def _import_archive(
    data: bytes,
    filename: str,
    *,
    store: ProjectStore,
    styles: StyleStore,
    themes: ThemeLibrary,
    settings: Settings,
) -> Project:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ApiError("不是有效的 ZIP 文件", "BAD_ARCHIVE") from exc
    with archive:
        members = _strip_common_prefix(_archive_members(archive))
        by_name = {path.as_posix(): info for info, path in members}
        if "project.json" not in by_name and "article.md" not in by_name:
            raise ApiError("压缩包里没有 article.md 或 project.json，无法导入", "BAD_ARCHIVE")

        meta_text = _read_text(archive, by_name, "project.json")
        try:
            meta = json.loads(meta_text) if meta_text.strip() else {}
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        article_text = _read_text(archive, by_name, "article.md")
        title = str(meta.get("title") or "").strip() or title_of_markdown(article_text) or Path(filename).stem or "导入的文章"
        slug = _allocate_slug(store, str(meta.get("slug") or title))

        directory = store.path(slug)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            remaining = MAX_ARCHIVE_BYTES
            for info, path in members:
                relative = path.as_posix()
                if relative in {"manifest.json", "project.json"}:
                    continue
                target = safe_join(directory, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ApiError("压缩包解压后超过 200 MB", "TOO_LARGE", 413)
                        destination.write(chunk)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    meta["version"] = meta.get("version") or 2
    meta["slug"] = slug
    meta["title"] = title
    meta["topic"] = meta.get("topic") or title
    meta["targetChars"] = int(meta.get("targetChars") or settings.target_chars)
    meta["createdAt"] = now_iso()
    meta["updatedAt"] = now_iso()
    meta.setdefault("sources", [])
    article_exists = (directory / "article.md").is_file()
    meta["workflow"] = (
        {"stage": "done", "status": "done"} if article_exists else {"stage": "materials", "status": "idle"}
    )
    theme = str(meta.get("theme") or settings.default_theme)
    if not themes.read_css(theme):
        theme = settings.default_theme
    meta["theme"] = theme
    write_json(directory / "project.json", meta)

    styles.ensure_session(slug)
    try:
        themes.set_session_theme(slug, theme)
    except ApiError:
        pass
    project = store.load(slug)
    if article_exists:
        try:
            render_project(project, settings=settings, themes=themes)
        except Exception:  # noqa: BLE001 - 导入成功优先，排版失败不阻塞
            pass
    return project


def _import_markdown(
    data: bytes,
    filename: str,
    *,
    store: ProjectStore,
    styles: StyleStore,
    themes: ThemeLibrary,
    settings: Settings,
) -> Project:
    text = data.decode("utf-8", "replace").strip()
    if not text:
        raise ApiError("文件是空的", "BAD_ARCHIVE")
    title = title_of_markdown(text) or Path(filename).stem or "导入的文章"
    slug = _allocate_slug(store, title)
    project = store.create(
        title=title,
        slug=slug,
        topic=title,
        target_chars=settings.target_chars,
        theme=settings.default_theme,
    )
    write_text(project.dir / "article.md", text + "\n")
    store.set_stage(slug, "done", "done")
    styles.ensure_session(slug)
    try:
        themes.set_session_theme(slug, settings.default_theme)
    except ApiError:
        pass
    project = store.load(slug)
    try:
        render_project(project, settings=settings, themes=themes)
    except Exception:  # noqa: BLE001
        pass
    return project


def import_upload(
    data: bytes,
    filename: str,
    *,
    store: ProjectStore,
    styles: StyleStore,
    themes: ThemeLibrary,
    settings: Settings,
) -> Project:
    if not data:
        raise ApiError("文件是空的", "BAD_ARCHIVE")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ApiError("文件超过 200 MB", "TOO_LARGE", 413)
    suffix = Path(filename or "").suffix.lower()
    if data[:4] in {b"PK\x03\x04", b"PK\x05\x06"}:
        return _import_archive(data, filename, store=store, styles=styles, themes=themes, settings=settings)
    if suffix in MARKDOWN_SUFFIXES:
        return _import_markdown(data, filename, store=store, styles=styles, themes=themes, settings=settings)
    if suffix == ".zip":
        raise ApiError("不是有效的 ZIP 文件", "BAD_ARCHIVE")
    raise ApiError("只支持 .zip 或 .md 文件", "UNSUPPORTED")
