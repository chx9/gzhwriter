"""公众号发布规范与原创模式检查。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import regex

from .materials import MARKER_RE, OPINION_RE, load_content, strip_markers
from .render.markdown import PENDING_SRC
from .storage import Project, char_count, parse_frontmatter, read_text, sha256, truncate

ABSOLUTE_TERMS = [
    "最佳", "最好", "最优", "最强", "最大", "最高", "最低", "最先进", "最便宜", "最流行", "最受欢迎", "最新技术", "最高级", "最顶级",
    "第一", "唯一", "首个", "首选", "顶级", "极致", "绝对", "100%", "百分百", "国家级", "世界级", "全网第一", "销量冠军", "史无前例",
    "万能", "永久", "无敌", "独家", "王牌", "领袖品牌", "遥遥领先", "秒杀", "根治", "包治", "零风险", "无副作用",
]
LIMITS = {"title": 64, "digest": 120, "author": 8}

SOURCE_MENTION_RE = regex.compile(
    "|".join(
        [
            "素材",
            "原材料",
            "参考资料",
            "参考来源",
            "资料来源",
            "信息来源",
            "原视频",
            "原作者",
            "原文作者",
            "口播稿",
            "文字稿",
            "字幕",
            "本文(?:基于|根据|参考|整理|改编|编译|综合|来自|转载|摘编)",
            "(?:整理|编译|改编|综合|摘编|转载)自",
            "(?:根据|基于|参考)(?:了)?(?:以上|上述|这|两|几|多)?(?:份|篇|段|个|部)?(?:视频|文章|资料|节目|报道|材料)",
            "(?:YouTube|油管|B站|bilibili|抖音|播客|博主|UP主|up主|频道|节目)(?:里|中|上)?(?:提到|说|讲|介绍|指出|认为|分析)",
            "(?:视频|节目|博主|UP主|作者)(?:里|中)?(?:提到|说|讲|介绍|指出|认为|分析)",
            "(?:有|一位|某位|这位)(?:博主|UP主|作者|频道)",
        ]
    ),
    regex.I,
)
VERBATIM_MIN = 24
LETTERS_RE = regex.compile(r"[\p{Script=Han}a-z]")
NORMALIZE_RE = regex.compile(r"[\s\p{P}\p{S}]+")


def normalize_verbatim(text: str) -> str:
    return NORMALIZE_RE.sub("", unicodedata.normalize("NFKC", str(text)).lower())


def build_material_index(project: Project, minimum: int = VERBATIM_MIN) -> dict[str, Any]:
    parts: list[str] = []
    for source in project.meta.get("sources", []):
        for which in ("content", "clean"):
            paragraphs = load_content(project, source, which)
            if paragraphs:
                parts.extend(normalize_verbatim(paragraph["text"]) for paragraph in paragraphs)
    joined = "".join(parts)
    grams = {joined[index : index + minimum] for index in range(max(0, len(joined) - minimum + 1))}
    return {"joined": joined, "grams": grams}


def verbatim_runs(text: str, index: dict[str, Any], minimum: int = VERBATIM_MIN) -> list[str]:
    normalized = normalize_verbatim(text)
    runs: list[str] = []
    cursor = 0
    while cursor + minimum <= len(normalized):
        if normalized[cursor : cursor + minimum] not in index["grams"]:
            cursor += 1
            continue
        end = cursor + minimum
        while end < len(normalized) and normalized[cursor : end + 1] in index["joined"]:
            end += 1
        run = normalized[cursor:end]
        if len(LETTERS_RE.findall(run)) >= minimum:
            runs.append(run)
        cursor = end
    return runs


def blocks(body: str) -> list[dict[str, Any]]:
    lines = str(body or "").split("\n")
    out: list[dict[str, Any]] = []
    buffer: list[str] = []
    start = 0
    fence = False
    section = 0
    paragraph_in_section = 0

    def flush() -> None:
        nonlocal buffer, section, paragraph_in_section
        if not buffer:
            return
        text = "\n".join(buffer)
        heading = bool(re.match(r"^#{1,6}\s", text))
        if heading and re.match(r"^##?\s", text):
            section += 1
            paragraph_in_section = 0
        if re.match(r"^```", text):
            kind = "code"
        elif heading:
            kind = "heading"
        elif re.match(r"^\s*([-*+]|\d+\.)\s", text):
            kind = "list"
        elif text.startswith(">"):
            kind = "quote"
        elif re.match(r"^\s*(---|\*\*\*|___)\s*$", text):
            kind = "hr"
        elif text.startswith("![") and "\n" not in text:
            kind = "image"
        else:
            kind = "para"
        if not heading:
            paragraph_in_section += 1
        out.append(
            {
                "text": text,
                "line": start + 1,
                "kind": kind,
                "anchor": f"§{section}" if heading else f"§{section}¶{paragraph_in_section}",
            }
        )
        buffer = []

    for index, line in enumerate(lines):
        if line.startswith("```"):
            fence = not fence
        if not fence and line.strip() == "" and not line.startswith("```"):
            flush()
            continue
        if not buffer:
            start = index
        buffer.append(line)
        if not fence and re.match(r"^#{1,6}\s", line):
            flush()
    flush()
    return out


def target_chars_from_brief(brief: str) -> int | None:
    match = re.search(r"篇幅\*\*[：:]\s*[^\d]*(\d{3,5})", brief or "") or re.search(r"(\d{3,5})\s*字", brief or "")
    return int(match.group(1)) if match else None


def load_sensitive_words(path: str | Path | None) -> list[str]:
    if not path:
        return []
    text = read_text(Path(path), "") or ""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def lint_article(
    project: Project,
    *,
    sensitive_words: list[str] | None = None,
    target_chars: int | None = None,
    original_mode: bool = False,
) -> dict[str, Any]:
    text = read_text(project.dir / "article.md", None)
    if text is None:
        raise ValueError("article.md does not exist yet")
    meta, body = parse_frontmatter(text)
    offset = len(text.split("\n")) - len(body.split("\n"))
    issues: list[dict[str, Any]] = []

    def add(severity: str, rule: str, detail: str, **where: Any) -> None:
        issues.append({"id": f"L{len(issues) + 1:02d}", "severity": severity, "rule": rule, "detail": detail, **where})

    title = meta.get("title", "")
    digest = meta.get("digest", "")
    if not title:
        add("error", "title-missing", "frontmatter 缺少 title")
    elif char_count(title) > LIMITS["title"]:
        add("error", "title-length", f"标题 {char_count(title)} 字，超过 {LIMITS['title']} 字上限")
    elif char_count(title) > 30:
        add("info", "title-long", f"标题 {char_count(title)} 字；超过约 30 字时在消息列表里会被折叠")
    if not digest:
        add("warning", "digest-missing", "frontmatter 缺少 digest（摘要）；不填时公众号会自动截取正文开头")
    elif char_count(digest) > LIMITS["digest"]:
        add("error", "digest-length", f"摘要 {char_count(digest)} 字，超过 {LIMITS['digest']} 字上限")
    if meta.get("author") and char_count(meta["author"]) > LIMITS["author"]:
        add("error", "author-length", f"作者名 {char_count(meta['author'])} 字，超过 {LIMITS['author']} 字上限")
    if not meta.get("cover"):
        add("info", "cover-missing", "未指定封面图（cover）；发布时需要上传一张约 2.35:1 的封面")

    words = list(dict.fromkeys([*ABSOLUTE_TERMS, *(sensitive_words or [])]))

    def check_terms(value: str, label: str, **where: Any) -> None:
        for word in words:
            if word and word in value:
                absolute = word in ABSOLUTE_TERMS
                add(
                    "warning" if absolute else "error",
                    "absolute-term" if absolute else "sensitive-word",
                    f"{label}出现{'极限词' if absolute else '敏感词'}“{word}”",
                    **where,
                )

    check_terms(title, "标题", field="title")
    check_terms(digest, "摘要", field="digest")

    material_index = build_material_index(project) if original_mode else None
    if original_mode:
        for field, value in (("title", title), ("digest", digest)):
            hit = SOURCE_MENTION_RE.search(value)
            if hit:
                add(
                    "error",
                    "source-mention",
                    f"{'标题' if field == 'title' else '摘要'}提到了“{hit.group(0)}”：原创文章不交代参考了什么材料",
                    field=field,
                )

    total = 0
    for block in blocks(body):
        plain = strip_markers(block["text"])
        where = {
            "anchor": block["anchor"],
            "line": block["line"] + offset,
            "excerpt": truncate(re.sub(r"\s+", " ", plain).strip(), 60),
        }
        if block["kind"] != "code":
            cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", plain)
            cleaned = re.sub(r"\]\([^)]*\)", "]", cleaned)
            cleaned = re.sub(r"[#>*_`|-]", "", cleaned)
            total += char_count(cleaned)
        if block["kind"] == "code":
            continue
        check_terms(plain, "正文", **where)
        if original_mode:
            mentions = list(dict.fromkeys(match.group(0).strip() for match in SOURCE_MENTION_RE.finditer(plain)))
            if mentions:
                add(
                    "error",
                    "source-mention",
                    f"正文提到了“{'”“'.join(mentions)}”：原创文章不交代参考了什么材料，把这句改成作者自己的陈述或删掉",
                    **where,
                )
            if material_index is not None and block["kind"] != "quote":
                runs = verbatim_runs(plain, material_index)
                if runs:
                    longest = max(runs, key=len)
                    add(
                        "warning",
                        "verbatim-copy",
                        f"有 {len(runs)} 处与素材逐字相同（最长 {len(longest)} 字：“{truncate(longest, 30)}”）：用自己的话重写",
                        **where,
                    )
        if block["kind"] == "para" and char_count(plain) > 300:
            add("info", "long-paragraph", f"段落 {char_count(plain)} 字，手机上阅读偏长，建议拆分", **where)
        for match in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)", plain):
            source = match.group(1)
            if source == PENDING_SRC:
                continue
            if re.match(r"^https?://mmbiz\.qpic\.cn/", source):
                continue
            if not re.match(r"^[a-z][a-z0-9+.-]*:", source, re.I):
                add("info", "image-local", f"本地图片 {truncate(source, 60)}：预览里能看到；复制到公众号后要在编辑器里重新上传这张图", **where)
                continue
            add("warning", "image-not-uploaded", f"图片 {truncate(source, 60)} 不是微信素材库地址，粘贴后可能无法显示；请在公众号编辑器里重新上传", **where)
        external = [
            match.group(1)
            for match in re.finditer(r"\]\((https?://[^)\s]+)\)", plain)
            if "mp.weixin.qq.com" not in match.group(1)
        ]
        if external:
            add("info", "external-link", f"含 {len(external)} 个外链，排版时会转成文末“文中链接”（非认证号正文外链不可点击）", **where)
        bare = re.search(r"https?://\S+", re.sub(r"\]\([^)]*\)", "", plain))
        if bare:
            add("info", "bare-url", f"正文中有裸露网址 {truncate(bare.group(0), 50)}", **where)

    try:
        from .images import find_slots

        pending = [slot for slot in find_slots(body) if slot["pending"]]
        if pending:
            listing = "；".join(
                f"图{slot['n']}「{truncate(slot.get('caption') or '无图注', 20)}」(line {slot['line'] + offset})"
                for slot in pending
            )
            add("warning", "image-pending", f"还有 {len(pending)} 张配图待补：{listing}。在「发布」页补图，补完后重新排版")
    except Exception:
        pass

    html = read_text(project.dir / "dist/article.html", "") or ""
    if html:
        if MARKER_RE.search(html) or OPINION_RE.search(html):
            add("error", "marker-leftover", "dist/article.html 里仍有溯源标记")
        if re.search(r"<style|class=|<script", html, re.I):
            add("error", "html-unsafe", "dist/article.html 含 <style>/class/<script>，公众号会丢弃这些样式")

    if target_chars and total:
        ratio = total / target_chars
        if ratio < 0.7 or ratio > 1.4:
            add("warning", "length-target", f"正文约 {total} 字，brief 目标约 {target_chars} 字")

    return {
        "checkedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "articleHash": sha256(text),
        "summary": {
            "errors": sum(1 for issue in issues if issue["severity"] == "error"),
            "warnings": sum(1 for issue in issues if issue["severity"] == "warning"),
            "infos": sum(1 for issue in issues if issue["severity"] == "info"),
            "chars": total,
            "title": {"text": title, "chars": char_count(title)},
            "digest": {"chars": char_count(digest)},
        },
        "issues": issues,
    }
