"""素材入库与按锚点读取。

content.md 格式（与原插件一致）：

    [¶1] 第一段……
    [¶1 @12:30] 口语稿第一段……
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from pathlib import Path
from typing import Any, Callable

from .errors import ApiError
from .storage import Project, ProjectStore, char_count, now_iso, read_json, read_text, write_json, write_text

TEXT_EXT = {".md", ".markdown", ".txt", ".html", ".htm", ".srt", ".vtt", ".docx"}
MAX_READ_CHARS = 12000


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── parsing ─────────────────────────────────────────────────────────


def _split_long_paragraph(paragraph: str, limit: int = 600) -> list[str]:
    """字幕转录常常整篇没有空行，会被当成一段；按句子重新切块，让锚点可用。"""
    if len(paragraph) <= limit * 1.5:
        return [paragraph]
    lines = paragraph.split("\n")
    head = ""
    if lines and re.match(r"^#{1,6}\s", lines[0]):
        head, lines = lines[0], lines[1:]
    parts = re.split(r"(?<=[。！？!?；;])|\n+", "\n".join(lines))
    chunks: list[str] = []
    buffer = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        joiner = " " if buffer and re.search(r"[A-Za-z0-9]$", buffer) and re.match(r"^[A-Za-z0-9]", part) else ""
        if buffer and len(buffer) + len(part) + len(joiner) > limit:
            chunks.append(buffer)
            buffer = part
        else:
            buffer += joiner + part
    if buffer:
        chunks.append(buffer)
    if head:
        if chunks:
            chunks[0] = f"{head}\n{chunks[0]}"
        else:
            chunks = [head]
    return chunks


def text_to_paragraphs(text: str) -> dict[str, Any]:
    value = str(text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    title = None
    fm = re.match(r"^---\n([\s\S]*?)\n---\n", value)
    if fm:
        match = re.search(r"^title:\s*(.+)$", fm.group(1), re.M)
        if match:
            title = match.group(1).strip().strip("\"'")
        value = value[fm.end() :]
    paragraphs: list[str] = []
    buffer: list[str] = []
    fence = False
    for line in value.split("\n"):
        if line.startswith("```"):
            fence = not fence
        if not fence and line.strip() == "":
            if buffer:
                paragraphs.append("\n".join(buffer).strip())
                buffer = []
            continue
        buffer.append(line)
        if not fence and re.match(r"^#{1,6}\s", line):
            paragraphs.append("\n".join(buffer).strip())
            buffer = []
    if buffer:
        paragraphs.append("\n".join(buffer).strip())
    if not title:
        for paragraph in paragraphs:
            if re.match(r"^#\s", paragraph):
                title = re.sub(r"^#\s+", "", paragraph).strip()
                break
    expanded: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        expanded.extend(_split_long_paragraph(paragraph) if len(paragraph) > 2000 else [paragraph])
    return {"title": title, "paragraphs": expanded}


def decode_entities(text: str) -> str:
    return html_lib.unescape(text)


def html_to_paragraphs(raw: str) -> dict[str, Any]:
    title_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', raw, re.I)
    if not title_match:
        title_match = re.search(r"<title[^>]*>([\s\S]*?)</title>", raw, re.I)
    title = decode_entities(title_match.group(1)).strip() if title_match else None
    author_match = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']*)["\']', raw, re.I)
    author = author_match.group(1) if author_match else None
    body = raw
    wx = re.search(r'<div[^>]+id=["\']js_content["\'][^>]*>([\s\S]*?)</div>\s*<script', raw, re.I)
    if not wx:
        wx = re.search(r'<div[^>]+id=["\']js_content["\'][^>]*>([\s\S]*)', raw, re.I)
    art = re.search(r"<article[^>]*>([\s\S]*?)</article>", raw, re.I)
    bd = re.search(r"<body[^>]*>([\s\S]*?)</body>", raw, re.I)
    chosen = wx or art or bd
    if chosen:
        body = chosen.group(1)
    body = re.sub(r"<(script|style|noscript|svg|iframe|nav|footer|header|form)[\s\S]*?</\1>", "", body, flags=re.I)
    body = re.sub(r"<!--[\s\S]*?-->", "", body)
    body = re.sub(r"<h([1-6])[^>]*>([\s\S]*?)</h\1>", lambda m: f"\n\n{'#' * int(m.group(1))} {m.group(2)}\n\n", body, flags=re.I)
    body = re.sub(r"<li[^>]*>", "\n\n- ", body, flags=re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"</(p|div|section|blockquote|li|ul|ol|tr|table|h\d)>", "\n\n", body, flags=re.I)
    body = re.sub(
        r'<img[^>]*?(?:data-src|src)=["\']([^"\']+)["\'][^>]*>',
        lambda m: f"\n\n![]({m.group(1)})\n\n",
        body,
        flags=re.I,
    )
    body = re.sub(r"<[^>]+>", "", body)
    paragraphs = [
        re.sub(r"[ \t ]+", " ", p).strip()
        for p in re.split(r"\n{2,}", decode_entities(body))
    ]
    return {"title": title, "author": author, "paragraphs": [p for p in paragraphs if p]}


def parse_subtitles(text: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    blocks = str(text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    timestamp = re.compile(r"(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}")
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if index < 0:
            continue
        times = timestamp.findall(lines[index])
        stamps = timestamp.finditer(lines[index])
        values = [m.group(0) for m in stamps]
        if len(values) < 2:
            continue

        def to_seconds(value: str) -> float:
            value = value.replace(",", ".")
            day, _, frac = value.partition(".")
            parts = [int(p) for p in day.split(":")]
            total = 0
            for part in parts:
                total = total * 60 + part
            return total + float(f"0.{frac}" if frac else 0)

        body = " ".join(lines[index + 1 :])
        body = re.sub(r"<[^>]+>", "", body)
        body = re.sub(r"\{\\[^}]*\}", "", body).strip()
        if body:
            cues.append({"start": to_seconds(values[0]), "end": to_seconds(values[1]), "text": body})
    return cues


def parse_timestamped_text(text: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    pattern = re.compile(r"^\s*\[?((?:\d{1,2}:)?\d{1,2}:\d{2})(?:[.,]\d+)?\]?\s*(?:-->\s*\S+\s*)?(.*)$")
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        match = pattern.match(line)
        if match:
            if match.group(2).strip():
                cues.append({"start": parse_time(match.group(1)), "end": None, "text": match.group(2).strip()})
        elif line.strip() and cues:
            cues[-1]["text"] += f" {line.strip()}"
    return cues


def looks_timestamped(text: str) -> bool:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    hits = sum(1 for line in lines if re.match(r"^\s*\[?(\d{1,2}:)?\d{1,2}:\d{2}\]?\s", line))
    return hits / len(lines) >= 0.5


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[。！？!?…」”]$", text or ""))


def _fix_end(text: str) -> str:
    return text if re.search(r"[。！？!?…」”.]$", text or "") else f"{text}。"


def merge_cues(cues: list[dict[str, Any]], gap_seconds: float = 2.5, target_chars: int = 220, max_chars: int = 420) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for cue in cues:
        text = re.sub(r"\s+", " ", str(cue.get("text") or "")).strip()
        if not text:
            continue
        gap = 0
        if current is not None and current.get("lastEnd") is not None:
            gap = float(cue["start"]) - float(current["lastEnd"])
        length = char_count(current["text"]) if current else 0
        should_break = current is not None and (
            gap >= gap_seconds or length >= max_chars or (length >= target_chars and _ends_sentence(current["text"]))
        )
        if current is None or should_break:
            if current is not None:
                paragraphs.append(current)
            current = {"start": cue["start"], "text": text, "lastEnd": cue.get("end") or cue["start"]}
        else:
            join = " "
            if re.search(r"[一-鿿，。！？、；：]$", current["text"]) or re.match(r"^[一-鿿]", text):
                join = "" if _ends_sentence(current["text"]) or re.search(r"[，、；：]$", current["text"]) else "，"
            current["text"] += join + text
            current["lastEnd"] = cue.get("end") or cue["start"]
    if current is not None:
        paragraphs.append(current)
    return [{"time": p["start"], "text": _fix_end(p["text"])} for p in paragraphs]


def garbled_ratio(text: str) -> float:
    chars = list(str(text or ""))
    if not chars:
        return 0.0
    bad = sum(1 for char in chars if char == "�" or re.match(r"[\u0000-\u0008\u000e-\u001f]", char) or char in "ÃÂâ€")
    return bad / len(chars)


def glossary_rules(text: str) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for line in str(text or "").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        match = re.match(r"^(.+?)\s*(?:=>|→|->)\s*(.+)$", clean)
        if match:
            for wrong in re.split(r"[|,，]", match.group(1)):
                wrong = wrong.strip()
                if wrong:
                    rules.append((wrong, match.group(2).strip()))
    return rules


def parse_time(value: str) -> int:
    parts = [int(part) for part in str(value).split(":") if part.isdigit()]
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


def format_time(seconds: float | int | None) -> str:
    total = max(0, int(float(seconds or 0)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


# ── import ──────────────────────────────────────────────────────────


def _slugify_material(text: str, fallback: str) -> str:
    ascii_text = re.sub(r"[^\w\s-]", "", str(text or "").lower()).strip()
    ascii_text = re.sub(r"[\s_]+", "-", ascii_text).strip("-")
    return ascii_text[:32] or fallback


def import_materials(
    store: ProjectStore,
    project: Project,
    inputs: list[dict[str, Any]] | None = None,
    *,
    cwd: Path | None = None,
    fetch_url: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    meta = project.meta
    meta.setdefault("sources", [])
    glossary = glossary_rules(read_text(project.dir / "glossary.txt", "") or "")
    items = list(inputs or [])
    if not items:
        inbox = project.dir / "sources/inbox"
        items = [{"path": str(p)} for p in sorted(inbox.glob("*")) if p.is_file() and not p.name.startswith(".")]
        if not items:
            raise ApiError("没有输入，且 sources/inbox/ 为空", "NO_INPUT")
    results: list[dict[str, Any]] = []
    for item in items:
        label = item.get("path") or item.get("url") or (f"text:{item.get('title')}" if item.get("title") else "text")
        try:
            result = import_one(project, item, cwd=cwd, glossary=glossary, fetch_url=fetch_url)
            results.append({"input": label, **result})
            if item.get("fromInbox") and result.get("status") != "error":
                inbox = project.dir / "sources/inbox"
                imported = inbox / ".imported"
                imported.mkdir(parents=True, exist_ok=True)
                try:
                    Path(item["path"]).rename(imported / Path(item["path"]).name)
                except OSError:
                    pass
        except Exception as exc:  # noqa: BLE001 - 每个输入独立报错
            results.append({"input": label, "status": "error", "error": str(exc)})
    store.save(project)
    write_index(project)
    return results


def import_one(
    project: Project,
    item: dict[str, Any],
    *,
    cwd: Path | None = None,
    glossary: list[tuple[str, str]] | None = None,
    fetch_url: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    meta = project.meta
    sources = meta.setdefault("sources", [])
    provided = sum(1 for key in ("path", "url", "text") if item.get(key) is not None)
    if provided != 1:
        raise ApiError("每个输入只能提供 path / url / text 之一", "BAD_INPUT")
    raw: str
    ext: str
    origin: dict[str, Any]
    fetched_title: str | None = None
    docx_paragraphs: list[str] | None = None
    if item.get("path"):
        path = Path(str(item["path"]))
        if not path.is_absolute():
            path = (cwd or Path.cwd()) / path
        ext = path.suffix.lower()
        if ext not in TEXT_EXT:
            raise ApiError(
                f"不支持的文件类型 {ext or '(无扩展名)'}；支持 {' '.join(sorted(TEXT_EXT))}。PDF/音频/视频请先转换。",
                "UNSUPPORTED",
            )
        if ext == ".docx":
            from docx import Document

            document = Document(str(path))
            docx_paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
            raw = "\n\n".join(docx_paragraphs)
        else:
            raw = path.read_text("utf-8")
        origin = {"type": "file", "path": str(path), "name": path.name}
    elif item.get("url"):
        url = str(item["url"])
        if not re.match(r"^https?://", url, re.I):
            raise ApiError(f"不是 http(s) 链接：{url}", "BAD_URL")
        if fetch_url is None:
            raise ApiError("当前环境不支持网页抓取", "NO_FETCH")
        fetched = fetch_url(url)
        status = int(fetched.get("statusCode") or 0)
        if status >= 400:
            raise ApiError(f"抓取 {url} 返回 HTTP {status}", "FETCH_FAILED")
        raw = str(fetched.get("content") or "")
        ext = ".html" if fetched.get("kind") == "html" else ".txt"
        origin = {"type": "url", "url": url}
        if fetched.get("truncated"):
            origin["truncated"] = True
    else:
        raw = str(item.get("text") or "")
        ext = ".txt"
        if raw.lstrip().startswith("WEBVTT"):
            ext = ".vtt"
        elif re.match(r"^\s*\d+\s*\n[\d:,.]+\s*-->", raw):
            ext = ".srt"
        origin = {"type": "url", "url": item["origin_url"]} if item.get("origin_url") else {"type": "pasted"}

    digest_hash = sha256(raw)
    duplicate = next((s for s in sources if s.get("hash") == digest_hash), None)
    if duplicate:
        return {"status": "duplicate", "id": duplicate.get("id"), "title": duplicate.get("title"), "note": f"与 {duplicate.get('id')} 相同"}

    kind = item.get("kind") if item.get("kind") in {"written", "spoken"} else None
    subtitle = ext in {".srt", ".vtt"}
    timestamped = not subtitle and ext in {".txt", ".md"} and looks_timestamped(raw)
    if kind is None:
        kind = "spoken" if subtitle or timestamped else "written"

    if subtitle or timestamped:
        cues = parse_subtitles(raw) if subtitle else parse_timestamped_text(raw)
        paragraphs = merge_cues(cues)
    elif docx_paragraphs is not None:
        paragraphs = [{"text": text} for text in docx_paragraphs]
    elif ext in {".html", ".htm"}:
        parsed = html_to_paragraphs(raw)
        paragraphs = [{"text": p} for p in parsed["paragraphs"]]
        fetched_title = parsed.get("title")
        if parsed.get("author"):
            origin["author"] = parsed["author"]
    else:
        parsed = text_to_paragraphs(raw)
        paragraphs = [{"text": p} for p in parsed["paragraphs"]]
        fetched_title = parsed.get("title")

    title = item.get("title") or fetched_title or (Path(item["path"]).stem if item.get("path") else item.get("url") or "未命名素材")
    corrections: dict[str, int] = {}
    for paragraph in paragraphs:
        for wrong, right in glossary or []:
            if wrong != right and wrong in paragraph["text"]:
                key = f"{wrong}→{right}"
                corrections[key] = corrections.get(key, 0) + paragraph["text"].count(wrong)
                paragraph["text"] = paragraph["text"].replace(wrong, right)

    prefix = "S" if kind == "spoken" else "A"
    number = 1 + max([int(s["id"][1:]) for s in sources if str(s.get("id", "")).startswith(prefix)] or [0])
    source_id = f"{prefix}{number:02d}"
    source_dir = f"sources/{source_id}-{_slugify_material(title, 'transcript' if kind == 'spoken' else 'article')}"
    body = "\n\n".join(
        f"[¶{i + 1}{' @' + format_time(p['time']) if p.get('time') is not None else ''}] {p['text']}"
        for i, p in enumerate(paragraphs)
    )
    chars = sum(char_count(p["text"]) for p in paragraphs)
    warnings: list[str] = []
    if not paragraphs or chars == 0:
        warnings.append("内容为空")
    elif chars < 80:
        warnings.append(f"内容很短（{chars} 字）")
    garbled = garbled_ratio(raw)
    if garbled > 0.02:
        warnings.append(f"疑似乱码字符占 {garbled * 100:.1f}%（检查文件编码是否为 UTF-8）")
    if origin.get("truncated"):
        warnings.append("网页内容被截断，可能不完整")
    if kind == "spoken" and paragraphs and all(p.get("time") is None for p in paragraphs):
        warnings.append("口语稿没有解析到时间戳")
    if not paragraphs:
        raise ApiError(f"无法从 {item.get('path') or item.get('url') or '粘贴文本'} 提取文字", "EMPTY")

    time_range = None
    if kind == "spoken" and paragraphs:
        time_range = f"{format_time(paragraphs[0].get('time') or 0)}–{format_time(paragraphs[-1].get('time') or 0)}"
    header = f"# {source_id} {title}\n\n> 类型：{'口语稿' if kind == 'spoken' else '书面稿'} · {len(paragraphs)} 段 · {chars} 字"
    if time_range:
        header += f" · {time_range}"
    header += "\n\n"
    write_text(project.dir / source_dir / "content.md", header + body + "\n")
    if ext == ".docx" and item.get("path"):
        (project.dir / source_dir / "original.docx").write_bytes(Path(item["path"]).read_bytes())
    else:
        write_text(project.dir / source_dir / f"original{ext}", raw)
    entry = {
        "id": source_id,
        "kind": kind,
        "title": title,
        "dir": source_dir,
        "hash": digest_hash,
        "paragraphs": len(paragraphs),
        "chars": chars,
        "origin": origin,
        "importedAt": now_iso(),
    }
    if time_range:
        entry["timeRange"] = time_range
    if corrections:
        entry["corrections"] = corrections
    if warnings:
        entry["warnings"] = warnings
    write_json(project.dir / source_dir / "meta.json", entry)
    sources.append(entry)
    result = {
        "status": "imported",
        "id": source_id,
        "kind": kind,
        "title": title,
        "dir": source_dir,
        "paragraphs": len(paragraphs),
        "chars": chars,
        "warnings": warnings,
        "corrections": corrections,
    }
    if time_range:
        result["timeRange"] = time_range
    return result


def write_index(project: Project) -> None:
    lines = ["# 素材目录", "", "| ID | 类型 | 标题 | 规模 | 状态 | 一句话摘要 |", "|---|---|---|---|---|---|"]
    for source in project.meta.get("sources", []):
        digest = read_text(project.dir / str(source.get("dir")) / "digest.md", "") or ""
        one_line = ""
        match = re.search(r"^>\s*(.+)$", digest, re.M)
        if match:
            one_line = match.group(1).strip()
        if not one_line:
            match = re.search(r"一句话[^：:]*[：:]\s*(.+)$", digest, re.M)
            if match:
                one_line = match.group(1).strip()
        if not one_line and digest:
            one_line = "（见 digest.md）"
        if not one_line:
            one_line = "待消化"
        state = " ".join(
            filter(
                None,
                [
                    "排除" if source.get("excluded") else "",
                    "主素材" if source.get("primary") else "",
                    ("⚠ " + "；".join(source.get("warnings") or [])) if source.get("warnings") else "",
                ],
            )
        ) or "—"
        scale = f"{source.get('paragraphs', 0)} 段 / {source.get('chars', 0)} 字"
        if source.get("timeRange"):
            scale += f" / {source['timeRange']}"
        lines.append(
            f"| {source.get('id', '')} | {'口语' if source.get('kind') == 'spoken' else '书面'} | "
            f"{str(source.get('title', '')).replace('|', '/')} | {scale} | {state} | {one_line.replace('|', '/')} |"
        )
    write_text(project.dir / "sources/index.md", "\n".join(lines) + "\n")


# ── refs / read ─────────────────────────────────────────────────────

TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
REF_TOKEN = rf"[AS]\d{{2,3}}(?:¶\d+(?:-\d+)?|@{TIME}(?:-{TIME})?)?"
MARKER_RE = re.compile(rf"\[({REF_TOKEN}(?:\s*[;；,，、]\s*{REF_TOKEN})*)\]")
OPINION_RE = re.compile(r"\[观点\]")
# 模型偶尔写出不合规的标记：[A02标题]、[A01¶1；观点]。渲染时一并剥掉。
# 后面紧跟 "(" 的是 Markdown 链接/图片的文字，例如 ![A02 图](…)，不能剥。
LOOSE_MARKER_RE = re.compile(r"\[(?:[AS]\d{1,3}|观点)[^\[\]\n]{0,60}\](?!\()")


def parse_ref(token: str) -> dict[str, Any] | None:
    match = re.match(rf"^([AS]\d{{2,3}})(?:¶(\d+)(?:-(\d+))?|@({TIME})(?:-({TIME}))?)?$", str(token).strip())
    if not match:
        return None
    ref: dict[str, Any] = {"id": match.group(1), "raw": str(token).strip()}
    if match.group(2):
        ref["para"] = [int(match.group(2)), int(match.group(3) or match.group(2))]
    if match.group(4):
        ref["time"] = [parse_time(match.group(4)), parse_time(match.group(5)) if match.group(5) else None]
    return ref


def refs_in(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for match in MARKER_RE.finditer(str(text or "")):
        for token in re.split(r"\s*[;；,，、]\s*", match.group(1)):
            ref = parse_ref(token)
            if ref:
                refs.append(ref)
    return refs


def strip_markers(text: str) -> str:
    value = OPINION_RE.sub("", MARKER_RE.sub("", str(text or "")))
    return LOOSE_MARKER_RE.sub("", value)


def parse_content(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pattern = re.compile(r"^\[¶(\d+)(?: @([\d:]+))?\] ([\s\S]*?)(?=\n\n\[¶\d+|\n*$)", re.M)
    for match in pattern.finditer(str(text or "")):
        item: dict[str, Any] = {"n": int(match.group(1)), "text": match.group(3).strip()}
        if match.group(2):
            item["time"] = parse_time(match.group(2))
        out.append(item)
    return out


def find_source(project: Project, source_id: str) -> dict[str, Any] | None:
    return next((s for s in project.meta.get("sources", []) if s.get("id") == source_id), None)


def load_content(project: Project, source: dict[str, Any], which: str = "content") -> list[dict[str, Any]] | None:
    text = read_text(project.dir / str(source.get("dir")) / f"{which}.md", None)
    if text is None:
        return None
    return parse_content(text)


def select_paragraphs(paragraphs: list[dict[str, Any]], ref: dict[str, Any], context: int = 0) -> list[dict[str, Any]]:
    if "para" not in ref and "time" not in ref:
        return paragraphs
    if "para" in ref:
        lo = next((i for i, p in enumerate(paragraphs) if p["n"] == ref["para"][0]), -1)
        hi = next((i for i, p in enumerate(paragraphs) if p["n"] == ref["para"][1]), -1)
        if lo < 0:
            return []
        if hi < 0:
            hi = len(paragraphs) - 1
    else:
        start, end = ref["time"]
        indexes = [i for i, p in enumerate(paragraphs) if p.get("time") is not None and p["time"] <= start]
        lo = indexes[-1] if indexes else -1
        if end is None:
            hi = lo
        else:
            ends = [i for i, p in enumerate(paragraphs) if p.get("time") is not None and p["time"] <= end]
            hi = ends[-1] if ends else lo
        lo = max(0, lo)
        hi = max(lo, hi)
    return paragraphs[max(0, lo - context) : min(len(paragraphs), hi + 1 + context)]


def read_refs(project: Project, refs: list[str], *, context: int = 0, version: str = "content") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    budget = MAX_READ_CHARS
    for token in refs:
        ref = parse_ref(token)
        if not ref:
            out.append({"ref": token, "error": "无法识别的锚点；用 A01、A01¶3、A01¶3-5、S01@12:30 或 S01@12:30-15:10"})
            continue
        source = find_source(project, ref["id"])
        if not source:
            out.append({"ref": token, "error": f"没有素材 {ref['id']}"})
            continue
        which = "clean" if version == "clean" and source.get("kind") == "spoken" else "content"
        paragraphs = load_content(project, source, which)
        if not paragraphs:
            which = "content"
            paragraphs = load_content(project, source, "content") or []
        selected = select_paragraphs(paragraphs, ref, context)
        if not selected:
            out.append({"ref": token, "error": f"{ref['id']} 共 {len(paragraphs)} 段；锚点超出范围"})
            continue
        lines: list[str] = []
        truncated = False
        for paragraph in selected:
            anchor = f"[{ref['id']}¶{paragraph['n']}"
            if paragraph.get("time") is not None:
                anchor += f" @{format_time(paragraph['time'])}"
            line = anchor + f"] {paragraph['text']}"
            if budget - len(line) < 0:
                truncated = True
                break
            budget -= len(line)
            lines.append(line)
        out.append(
            {
                "ref": token,
                "source": f"{source.get('id')} {source.get('title')}",
                "version": which,
                "paragraphs": len(lines),
                "text": "\n\n".join(lines),
                "truncated": truncated,
            }
        )
        if budget <= 0:
            break
    return out


def material_context(project: Project, *, max_chars: int = 30000) -> str:
    """给 LLM 的素材总览：索引 + 每份 digest，不塞全文。"""
    parts: list[str] = []
    index = read_text(project.dir / "sources/index.md", "") or ""
    if index:
        parts.append(index)
    for source in project.meta.get("sources", []):
        if source.get("excluded"):
            continue
        digest = read_text(project.dir / str(source.get("dir")) / "digest.md", "") or ""
        if digest:
            parts.append(f"## {source.get('id')} {source.get('title')}\n\n{digest}")
    text = "\n\n".join(parts)
    return text[:max_chars]
