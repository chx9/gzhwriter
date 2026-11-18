"""资料评论：项目目录下的 .materials-comments.json。"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ApiError
from .storage import Project, now_iso, write_text

COMMENTS_FILE = ".materials-comments.json"
LIMITS = {"comments": 500, "body": 4000, "quote": 2000, "path": 512}
STATUSES = {"open", "sent"}


def _fail(message: str) -> None:
    raise ApiError(message, "BAD_COMMENT", 400)


def _optional_line(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < 1 or value > 10_000_000:
        _fail(f"{field} 必须是正整数")
    return value


def _validate_path(path: str) -> None:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not path:
        _fail("评论路径必须是项目内相对路径")


def normalize_comment(raw: Any, index: int = 0) -> dict[str, Any]:
    if not isinstance(raw, dict):
        _fail(f"第 {index + 1} 条评论不是对象")
    ident = raw.get("id")
    if not isinstance(ident, str) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", ident):
        _fail(f"第 {index + 1} 条评论的 id 不合法")
    path = raw.get("path")
    if not isinstance(path, str) or not path or len(path) > LIMITS["path"]:
        _fail(f"评论 {ident} 缺少文件路径")
    _validate_path(path)
    body = str(raw.get("body") or "").strip()
    if not body:
        _fail(f"评论 {ident} 没有内容")
    if len(body) > LIMITS["body"]:
        _fail(f"评论 {ident} 超过 {LIMITS['body']} 字")
    quote = str(raw.get("quote") or "")
    if len(quote) > LIMITS["quote"]:
        _fail(f"评论 {ident} 的引文超过 {LIMITS['quote']} 字")
    line_start = _optional_line(raw.get("lineStart"), "lineStart")
    line_end = _optional_line(raw.get("lineEnd"), "lineEnd")
    if line_start is not None and line_end is not None and line_end < line_start:
        _fail(f"评论 {ident} 的行号区间颠倒")
    status = raw.get("status") if raw.get("status") in STATUSES else "open"
    created_at = raw.get("createdAt") if isinstance(raw.get("createdAt"), str) else now_iso()
    comment = {"id": ident, "path": path, "body": body, "status": status, "createdAt": created_at}
    if quote:
        comment["quote"] = quote
    if line_start is not None:
        comment["lineStart"] = line_start
    if line_end is not None:
        comment["lineEnd"] = line_end
    if status == "sent" and isinstance(raw.get("sentAt"), str):
        comment["sentAt"] = raw["sentAt"]
    return comment


def normalize_comments(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        _fail("comments 必须是数组")
    if len(items) > LIMITS["comments"]:
        _fail(f"评论最多 {LIMITS['comments']} 条")
    seen: set[str] = set()
    result = []
    for index, raw in enumerate(items):
        comment = normalize_comment(raw, index)
        if comment["id"] in seen:
            _fail(f"评论 id 重复：{comment['id']}")
        seen.add(comment["id"])
        result.append(comment)
    return result


def read_comments(project: Project) -> dict[str, Any]:
    path = project.dir / COMMENTS_FILE
    if not path.exists():
        return {"comments": []}
    try:
        parsed = json.loads(path.read_text("utf8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"comments": [], "warning": f"评论文件无法解析，已忽略：{exc}"}
    try:
        return {"comments": normalize_comments(parsed if isinstance(parsed, list) else parsed.get("comments", []))}
    except ApiError as exc:
        return {"comments": [], "warning": str(exc)}


def write_comments(project: Project, items: Any) -> dict[str, Any]:
    comments = normalize_comments(items)
    write_text(project.dir / COMMENTS_FILE, json.dumps({"version": 1, "comments": comments}, ensure_ascii=False, indent=2) + "\n")
    return {"comments": comments}


def add_comment(project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    current = read_comments(project)["comments"]
    comment = normalize_comment(payload, len(current))
    if any(item["id"] == comment["id"] for item in current):
        raise ApiError(f"评论 id 已存在：{comment['id']}", "BAD_COMMENT", 409)
    current.append(comment)
    write_comments(project, current)
    return comment


def update_comment(project: Project, comment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = read_comments(project)["comments"]
    found = None
    for index, item in enumerate(current):
        if item["id"] == comment_id:
            merged = {**item, **payload, "id": comment_id}
            current[index] = normalize_comment(merged, index)
            found = current[index]
            break
    if found is None:
        raise ApiError("评论不存在", "NO_COMMENT", 404)
    write_comments(project, current)
    return found


def delete_comment(project: Project, comment_id: str) -> dict[str, Any]:
    current = read_comments(project)["comments"]
    remaining = [item for item in current if item["id"] != comment_id]
    if len(remaining) == len(current):
        raise ApiError("评论不存在", "NO_COMMENT", 404)
    write_comments(project, remaining)
    return {"comments": remaining}
