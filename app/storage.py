"""项目目录、文件读写、frontmatter 与安全路径。"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ApiError, BadPath, NotFound

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
EDITABLE = {
    "article.md",
    "brief.md",
    "outline.md",
    "sources/index.md",
}
EDITABLE_PATTERNS = (
    re.compile(r"^sources/[AS]\d{2,3}-[^/]+/digest\.md$"),
    re.compile(r"^sources/[AS]\d{2,3}-[^/]+/clean\.md$"),
)

CATEGORIES = [
    {"id": "source", "label": "素材原件"},
    {"id": "intake", "label": "入库与消化"},
    {"id": "draft", "label": "成稿与过程"},
    {"id": "output", "label": "排版交付"},
]

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
HTML_EXT = {".html", ".htm"}
TEXT_EXT = {".md", ".markdown", ".txt", ".srt", ".vtt", ".csv", ".yml", ".yaml", ".json"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def char_count(text: str) -> int:
    """可见字符数：CJK 算 1，空白不计。"""
    return len(re.sub(r"\s+", "", str(text or "")))


def truncate(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "…"


def validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ApiError("项目名必须是小写字母、数字、-，2–64 位", "BAD_SLUG")
    return slug


def slugify(text: str, fallback: str | None = None) -> str:
    import unicodedata

    value = unicodedata.normalize("NFKD", str(text or "").lower())
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value).strip("-")
    value = re.sub(r"-+", "-", value)[:48].strip("-")
    if len(value) >= 3 and SLUG_RE.match(value):
        return value
    return fallback or f"article-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def safe_join(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise BadPath("缺少文件路径")
    if "\x00" in relative:
        raise BadPath("路径含非法字符")
    if os.path.isabs(relative):
        raise BadPath("路径必须是项目内相对路径")
    root = root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BadPath() from exc
    if target == root:
        raise BadPath()
    return target


_MISSING = object()


def read_text(path: Path, fallback: Any = _MISSING) -> str | None:
    try:
        return path.read_text("utf-8")
    except (FileNotFoundError, IsADirectoryError):
        if fallback is not _MISSING:
            return fallback
        raise


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{datetime.now().timestamp()}.tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(path)


def read_json(path: Path, fallback: Any = None) -> Any:
    text = read_text(path, None)
    if text is None:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


# ── frontmatter ─────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", str(text or ""))
    if not match:
        return {}, str(text or "")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not kv:
            continue
        value = kv.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[kv.group(1)] = value
    return meta, str(text or "")[match.end() :]


def stringify_frontmatter(meta: dict[str, Any], body: str) -> str:
    lines: list[str] = []
    for key, value in meta.items():
        if value is None:
            continue
        text = str(value)
        if re.search(r"[:#'\"]|^\s|\s$", text):
            text = json.dumps(text, ensure_ascii=False)
        lines.append(f"{key}: {text}")
    return "---\n" + "\n".join(lines) + "\n---\n\n" + str(body or "").lstrip("\n")


def set_frontmatter_value(text: str, key: str, value: str) -> str:
    quoted = json.dumps(value, ensure_ascii=False) if re.search(r"[:#'\"]|^\s|\s$", value) else value
    if not str(text or "").startswith("---"):
        return f"---\n{key}: {quoted}\n---\n\n{text}"
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---", text)
    if not match:
        return f"---\n{key}: {quoted}\n---\n\n{text}"
    head = match.group(1)
    line = re.compile(rf"^{re.escape(key)}\s*:.*$", re.M)
    if line.search(head):
        head = line.sub(f"{key}: {quoted}", head, count=1)
    else:
        head = head.rstrip() + f"\n{key}: {quoted}"
    return "---\n" + head + "\n---" + text[match.end() :]


def title_of_markdown(text: str) -> str | None:
    meta, body = parse_frontmatter(text)
    if meta.get("title"):
        return meta["title"].strip()
    match = re.search(r"^#\s+(.+)$", body, re.M)
    return match.group(1).strip() if match else None


# ── files ───────────────────────────────────────────────────────────


def categorize(relative: str) -> str:
    path = relative.replace("\\", "/")
    if path.startswith("dist/"):
        return "output"
    if path.startswith("sources/"):
        if path.startswith("sources/inbox/"):
            return "source"
        if re.search(r"/(original|meta)\.[^/]+$", path):
            return "source"
        return "intake"
    return "draft"


def source_id_of(relative: str) -> str | None:
    match = re.match(r"^sources/([AS]\d{2,3})-", relative.replace("\\", "/"))
    return match.group(1) if match else None


def kind_of(relative: str) -> str:
    ext = Path(relative).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in HTML_EXT:
        return "html"
    if ext == ".json":
        return "json"
    if ext in TEXT_EXT:
        return "text"
    if ext == ".docx":
        return "docx"
    return "other"


def mime_of(relative: str) -> str:
    return mimetypes.guess_type(relative)[0] or "application/octet-stream"


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def is_editable(relative: str) -> bool:
    clean = relative.replace("\\", "/")
    if clean in EDITABLE:
        return True
    return any(pattern.match(clean) for pattern in EDITABLE_PATTERNS)


# ── project model ───────────────────────────────────────────────────


@dataclass
class Project:
    dir: Path
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return str(self.meta.get("slug") or self.dir.name)

    @property
    def sources(self) -> list[dict[str, Any]]:
        return list(self.meta.get("sources") or [])

    @property
    def workflow(self) -> dict[str, Any]:
        return dict(self.meta.get("workflow") or {})


class ProjectStore:
    def __init__(self, projects_root: Path, styles_root: Path) -> None:
        self.root = Path(projects_root)
        self.styles_root = Path(styles_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.styles_root.mkdir(parents=True, exist_ok=True)

    def path(self, slug: str) -> Path:
        validate_slug(slug)
        return self.root / slug

    def exists(self, slug: str) -> bool:
        return self.path(slug).is_dir()

    def create(
        self,
        *,
        title: str,
        slug: str | None = None,
        topic: str = "",
        goal: str = "",
        audience: str = "",
        target_chars: int = 2800,
        theme: str = "wechat",
    ) -> Project:
        slug = validate_slug(slug) if slug else slugify(title or topic)
        directory = self.path(slug)
        if directory.exists():
            raise ApiError(f"项目「{slug}」已存在", "PROJECT_EXISTS", 409)
        (directory / "sources/inbox").mkdir(parents=True, exist_ok=True)
        (directory / "dist").mkdir(parents=True, exist_ok=True)
        (directory / "images").mkdir(parents=True, exist_ok=True)
        meta = {
            "version": 2,
            "slug": slug,
            "title": title or topic or slug,
            "topic": topic,
            "goal": goal,
            "audience": audience,
            "targetChars": int(target_chars or 2800),
            "theme": theme or "wechat",
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "sources": [],
            "workflow": {"stage": "materials", "status": "idle", "reviewRound": 0},
        }
        write_json(directory / "project.json", meta)
        return Project(directory, meta)

    def load(self, slug: str) -> Project:
        directory = self.path(slug)
        if not directory.is_dir():
            raise NotFound(f"项目「{slug}」不存在", "NO_PROJECT")
        meta = read_json(directory / "project.json", None)
        if not isinstance(meta, dict):
            meta = {"version": 2, "slug": slug, "sources": [], "workflow": {}}
        meta.setdefault("slug", slug)
        meta.setdefault("sources", [])
        meta.setdefault("workflow", {})
        return Project(directory, meta)

    def save(self, project: Project) -> None:
        project.meta["updatedAt"] = now_iso()
        write_json(project.dir / "project.json", project.meta)

    def set_stage(self, slug: str, stage: str, status: str | None = None, **extra: Any) -> None:
        project = self.load(slug)
        workflow = dict(project.workflow)
        workflow["stage"] = stage
        if status is not None:
            workflow["status"] = status
        workflow.update(extra)
        workflow["updatedAt"] = now_iso()
        project.meta["workflow"] = workflow
        self.save(project)

    def delete(self, slug: str) -> None:
        import shutil

        directory = self.path(slug)
        if directory.is_dir():
            shutil.rmtree(directory)
        session = self.styles_root / "sessions" / slug
        if session.is_dir():
            shutil.rmtree(session)

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return items
        for directory in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                project = self.load(directory.name)
            except ApiError:
                continue
            article = read_text(directory / "article.md", "") or ""
            workflow = project.workflow
            items.append(
                {
                    "slug": project.slug,
                    "title": title_of_markdown(article) or project.meta.get("title") or project.slug,
                    "topic": project.meta.get("topic", ""),
                    "stage": workflow.get("stage") or "materials",
                    "status": workflow.get("status", "idle"),
                    "sourceCount": len(project.sources),
                    "hasArticle": bool(article.strip()),
                    "hasOutput": (directory / "dist/article.html").exists(),
                    "pendingImages": (article.count("ww-img:pending")),
                    "targetChars": int(project.meta.get("targetChars") or 0),
                    "theme": project.meta.get("theme") or "wechat",
                    "createdAt": project.meta.get("createdAt"),
                    "updatedAt": project.meta.get("updatedAt"),
                }
            )
        return items

    def files(self, slug: str, max_entries: int = 800) -> dict[str, Any]:
        project = self.load(slug)
        files: list[dict[str, Any]] = []
        for current, dirs, names in os.walk(project.dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                if name.startswith("."):
                    continue
                path = Path(current) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                relative = path.relative_to(project.dir).as_posix()
                parent = Path(relative).parent.as_posix()
                files.append(
                    {
                        "path": relative,
                        "name": name,
                        "dir": "" if parent == "." else parent,
                        "bytes": stat.st_size,
                        "mtime": stat.st_mtime * 1000,
                        "category": categorize(relative),
                        "sourceId": source_id_of(relative),
                        "kind": kind_of(relative),
                        "editable": is_editable(relative),
                    }
                )
                if len(files) >= max_entries:
                    break
            if len(files) >= max_entries:
                break
        article = read_text(project.dir / "article.md", "") or ""
        return {
            "slug": project.slug,
            "dir": str(project.dir),
            "title": title_of_markdown(article) or project.meta.get("title") or project.slug,
            "stage": project.workflow.get("stage") or "",
            "sources": [
                {"id": s.get("id", ""), "dir": s.get("dir", ""), "title": s.get("title", "")} for s in project.sources
            ],
            "files": files,
            "truncated": len(files) >= max_entries,
        }

    def read_file(self, slug: str, relative: str, max_bytes: int = 512 * 1024) -> dict[str, Any]:
        project = self.load(slug)
        target = safe_join(project.dir, relative)
        if not target.is_file():
            raise NotFound(f"文件不存在：{relative}", "NO_FILE")
        stat = target.stat()
        kind = kind_of(relative)
        if kind in {"image", "docx", "other"}:
            data: dict[str, Any] = {
                "path": relative,
                "kind": kind,
                "bytes": stat.st_size,
                "mtime": stat.st_mtime * 1000,
                "text": "",
                "truncated": False,
                "editable": is_editable(relative),
            }
            if kind == "image":
                if stat.st_size > 10 * 1024 * 1024:
                    data["note"] = "图片超过 10 MB，未内联预览"
                    data["truncated"] = True
                else:
                    data["dataUrl"] = f"data:{mime_of(relative)};base64,{base64.b64encode(target.read_bytes()).decode()}"
            return data
        buffer = target.read_bytes()
        truncated = len(buffer) > max_bytes
        return {
            "path": relative,
            "kind": kind,
            "bytes": stat.st_size,
            "mtime": stat.st_mtime * 1000,
            "text": buffer[:max_bytes].decode("utf-8", "replace"),
            "truncated": truncated,
            "editable": is_editable(relative),
        }

    def write_file(self, slug: str, relative: str, text: str) -> dict[str, Any]:
        if not is_editable(relative):
            raise ApiError("这个文件不允许编辑", "NOT_EDITABLE", 403)
        if len(text) > 2_000_000:
            raise ApiError("文件内容过大", "TOO_LARGE", 413)
        project = self.load(slug)
        target = safe_join(project.dir, relative)
        if not target.exists():
            # 白名单文件允许新建（如素材的 digest.md）
            if not target.parent.is_dir():
                raise NotFound(f"目录不存在：{relative}", "NO_FILE")
        write_text(target, text)
        return self.read_file(slug, relative)
