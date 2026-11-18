"""配图图位：列表、填充、清除。

占位符格式与原插件一致：

    ![图注](ww-img:pending "google: English keywords | 中文关键词")
    ![图注](images/fig-01.jpg "google: …")
"""

from __future__ import annotations

import base64
import re
import threading
from pathlib import Path
from typing import Any

from .errors import ApiError, NotFound
from .storage import Project, write_text

PENDING_SRC = "ww-img:pending"
IMAGES_DIR = "images"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\((\S+?)(?:\s+\"([^\"]*)\")?\)\s*$")
EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
LOCKS: dict[str, threading.Lock] = {}
LOCKS_GUARD = threading.Lock()


def lock_for(project_dir: Path) -> threading.Lock:
    key = str(project_dir)
    with LOCKS_GUARD:
        return LOCKS.setdefault(key, threading.Lock())


def parse_keywords(title: str | None) -> list[str]:
    raw = re.sub(r"^\s*google\s*[:：]\s*", "", str(title or ""), flags=re.I)
    return [part.strip() for part in raw.split("|") if part.strip()]


def google_images_url(keyword: str) -> str:
    from urllib.parse import quote

    return f"https://www.google.com/search?tbm=isch&q={quote(keyword)}"


def find_slots(markdown: str) -> list[dict[str, Any]]:
    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    slots: list[dict[str, Any]] = []
    fence = False
    for index, line in enumerate(lines):
        if line.startswith("```"):
            fence = not fence
        if fence:
            continue
        match = IMAGE_LINE_RE.match(line)
        if not match:
            continue
        caption, src, title = match.group(1), match.group(2), match.group(3)
        keywords = parse_keywords(title)
        slots.append(
            {
                "n": len(slots) + 1,
                "line": index + 1,
                "raw": line,
                "caption": caption,
                "src": src,
                "pending": src == PENDING_SRC,
                "keywords": keywords,
                "searches": [{"keyword": keyword, "url": google_images_url(keyword)} for keyword in keywords],
                "context": context_before(lines, index),
            }
        )
    return slots


def context_before(lines: list[str], index: int) -> str:
    for cursor in range(index - 1, -1, -1):
        text = lines[cursor].strip()
        if not text or text == "---" or IMAGE_LINE_RE.match(text):
            continue
        if re.match(r"^#{1,6}\s", text):
            return re.sub(r"^#+\s*", "", text)
        cleaned = re.sub(r"\[(?:[AS]\d{2,3}[^\]]*|观点)\]", "", text)
        cleaned = re.sub(r"[*_`>]", "", cleaned)
        return cleaned[-60:]
    return ""


def looks_like(mime: str, data: bytes) -> bool:
    if mime == "image/png":
        return len(data) > 8 and data[:4] == b"\x89PNG"
    if mime == "image/jpeg":
        return len(data) > 3 and data[:3] == b"\xff\xd8\xff"
    if mime == "image/gif":
        return len(data) > 6 and data[:4] == b"GIF8"
    if mime == "image/webp":
        return len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def sniff_mime(data: bytes) -> str | None:
    return next((mime for mime in EXT_BY_MIME if looks_like(mime, data)), None)


def decode_data_url(data_url: str) -> tuple[str, str, bytes]:
    match = re.match(r"^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$", str(data_url or ""))
    if not match:
        raise ApiError("只支持粘贴 PNG / JPEG / GIF / WebP 图片", "BAD_IMAGE")
    mime = match.group(1)
    data = base64.b64decode(re.sub(r"\s+", "", match.group(2)))
    if not data:
        raise ApiError("图片是空的", "BAD_IMAGE")
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB（公众号正文图片上限）", "BAD_IMAGE")
    if not looks_like(mime, data):
        raise ApiError("文件内容与图片格式不符", "BAD_IMAGE")
    return mime, EXT_BY_MIME[mime], data


def prepare_uploaded(data: bytes, declared_mime: str | None = None) -> tuple[str, str, bytes]:
    if not data:
        raise ApiError("图片是空的", "BAD_IMAGE")
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB（公众号正文图片上限）", "BAD_IMAGE")
    mime = sniff_mime(data)
    if not mime:
        raise ApiError("只支持 PNG / JPEG / GIF / WebP 图片", "BAD_IMAGE")
    return mime, EXT_BY_MIME[mime], data


def _esc_title(value: str) -> str:
    return str(value).replace('"', "“")


def slot_line(caption: str, src: str, keywords: list[str]) -> str:
    title = f' "google: {_esc_title(" | ".join(keywords))}"' if keywords else ""
    return f"![{re.sub(r'[\\[\\]]', '', caption)}]({src}{title})"


def _locate(text: str, line: int, raw: str) -> tuple[str, list[str], int]:
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = re.split(r"\r?\n", text)
    index = int(line) - 1
    if index < 0 or index >= len(lines) or lines[index] != raw:
        raise ApiError("文章刚被修改过，这个图位的位置变了；刷新后再试", "STALE_SLOT", 409)
    if not IMAGE_LINE_RE.match(raw):
        raise ApiError("这一行不是图位", "STALE_SLOT", 409)
    return eol, lines, index


def _atomic_write(path: Path, text: str) -> None:
    write_text(path, text)


def _load_article(project: Project) -> tuple[Path, str]:
    path = project.dir / "article.md"
    if not path.exists():
        raise NotFound("这篇文章还没有 article.md", "NO_ARTICLE")
    return path, path.read_text("utf8")


def _next_name(directory: Path, ext: str) -> str:
    for number in range(1, 1000):
        name = f"fig-{number:02d}.{ext}"
        if not (directory / name).exists():
            return name
    raise ApiError("images/ 里的图片太多了", "BAD_IMAGE")


def list_slots(project: Project) -> dict[str, Any]:
    _, text = _load_article(project)
    slots = find_slots(text)
    return {"slots": slots, "pending": sum(1 for slot in slots if slot["pending"]), "total": len(slots)}


def fill_slot(
    project: Project,
    *,
    line: int,
    raw: str,
    data_url: str | None = None,
    data: bytes | None = None,
    mime: str | None = None,
) -> dict[str, Any]:
    if data_url:
        _, ext, image = decode_data_url(data_url)
    elif data is not None:
        _, ext, image = prepare_uploaded(data, mime)
    else:
        raise ApiError("缺少图片数据", "BAD_IMAGE")
    with lock_for(project.dir):
        path, text = _load_article(project)
        eol, lines, index = _locate(text, line, raw)
        match = IMAGE_LINE_RE.match(raw)
        directory = project.dir / IMAGES_DIR
        directory.mkdir(parents=True, exist_ok=True)
        name = _next_name(directory, ext)
        target = directory / name
        target.write_bytes(image)
        try:
            lines[index] = slot_line(match.group(1), f"{IMAGES_DIR}/{name}", parse_keywords(match.group(3)))
            _atomic_write(path, eol.join(lines))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {**list_slots(project), "saved": f"{IMAGES_DIR}/{name}", "bytes": len(image)}


def clear_slot(project: Project, *, line: int, raw: str) -> dict[str, Any]:
    with lock_for(project.dir):
        path, text = _load_article(project)
        eol, lines, index = _locate(text, line, raw)
        match = IMAGE_LINE_RE.match(raw)
        lines[index] = slot_line(match.group(1), PENDING_SRC, parse_keywords(match.group(3)))
        _atomic_write(path, eol.join(lines))
        return list_slots(project)
