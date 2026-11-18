"""确定性的素材核对：锚点、引语、数字、无来源段落。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from .lint import blocks
from .materials import MARKER_RE, OPINION_RE, find_source, load_content, refs_in, select_paragraphs, strip_markers
from .storage import Project, char_count, parse_frontmatter, read_text, truncate

QUOTE_RE = re.compile(r"[“「『\"]([^”」』\"\n]{4,300})[”」』\"]")
NUMBER_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)*)\s*(%|％|‰|倍|万|亿|千|百|个百分点|元|美元|块|人|位|家|次|天|小时|分钟|个月|年|款|篇|条|项|岁|名|台|套|件|km|kg|GB|TB|MB|w|W|k|K)?"
)
MIN_UNSOURCED_CHARS = 40
FILLERS = ["就是说", "那个", "然后呢", "对吧", "是吧", "嗯", "啊", "呃", "哦", "额"]
NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_for_match(text: str) -> str:
    return NORMALIZE_RE.sub("", unicodedata.normalize("NFKC", str(text or "")).lower())


def strip_fillers(text: str) -> str:
    value = str(text or "")
    for filler in FILLERS:
        value = value.replace(filler, "")
    return value


def bigram_containment(needle: str, hay: str) -> float:
    def grams(value: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for index in range(len(value) - 1):
            gram = value[index : index + 2]
            out[gram] = out.get(gram, 0) + 1
        return out

    a = grams(needle)
    if not a:
        return 1.0 if needle in hay else 0.0
    b = grams(hay)
    hit = sum(min(count, b.get(gram, 0)) for gram, count in a.items())
    return hit / sum(a.values())


def match_quote(quote: str, hay: str) -> dict[str, Any]:
    q = normalize_for_match(quote)
    h = normalize_for_match(hay)
    if not q:
        return {"kind": "exact", "score": 1.0}
    if q in h:
        return {"kind": "exact", "score": 1.0}
    qf = normalize_for_match(strip_fillers(quote))
    hf = normalize_for_match(strip_fillers(hay))
    if qf and qf in hf:
        return {"kind": "filler", "score": 1.0}
    score = bigram_containment(qf or q, hf or h)
    return {"kind": "fuzzy" if score >= 0.85 else "none", "score": score}


def figures_in(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for match in NUMBER_RE.finditer(strip_markers(text)):
        value = match.group(1)
        unit = match.group(2) or ""
        number = float(value.replace(",", ""))
        if not unit and (re.match(r"^(19|20)\d{2}$", value) or len(value) == 1):
            continue
        if unit == "年" and re.match(r"^(19|20)\d{2}$", value):
            continue
        if not unit and number < 10:
            continue
        out.append({"raw": f"{value}{unit}", "value": value.replace(",", "")})
    return out


def quotes_in(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in QUOTE_RE.finditer(strip_markers(text))
        if len(normalize_for_match(match.group(1)).strip()) >= 6
    ]


def body_hash(text: str) -> str:
    _, body = parse_frontmatter(text)
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()[:16]


def check_sources(project: Project, file: str = "article.md") -> dict[str, Any]:
    text = read_text(project.dir / file, None)
    if text is None:
        raise ValueError(f"{file} does not exist yet")
    meta, body = parse_frontmatter(text)
    offset = len(text.split("\n")) - len(body.split("\n"))
    issues: list[dict[str, Any]] = []
    stats = {"paragraphs": 0, "sourced": 0, "opinion": 0, "unsourced": 0, "quotes": 0, "figures": 0}
    cited: set[str] = set()

    def add(severity: str, kind: str, block: dict[str, Any], detail: str, **extra: Any) -> None:
        issues.append(
            {
                "id": f"C{len(issues) + 1:02d}",
                "severity": severity,
                "type": kind,
                "anchor": block["anchor"],
                "line": block["line"] + offset,
                "excerpt": truncate(re.sub(r"\s+", " ", strip_markers(block["text"])).strip(), 80),
                "detail": detail,
                **extra,
            }
        )

    for block in blocks(body):
        if block["kind"] not in {"para", "list", "quote"}:
            continue
        stats["paragraphs"] += 1
        refs = refs_in(block["text"])
        opinion = bool(OPINION_RE.search(block["text"]))
        plain = strip_markers(block["text"])
        evidence: list[str] = []
        for ref in refs:
            source = find_source(project, ref["id"])
            if not source:
                add("error", "unknown-source", block, f"引用了不存在的素材 {ref['id']}", ref=ref["raw"])
                continue
            if source.get("excluded"):
                add("warning", "excluded-source", block, f"{ref['id']} 已被标记为排除，但正文仍在引用", ref=ref["raw"])
            cited.add(ref["id"])
            paragraphs = load_content(project, source, "content") or []
            hit = select_paragraphs(paragraphs, ref, 1)
            if not hit:
                add("error", "bad-anchor", block, f"{ref['raw']} 超出范围（{ref['id']} 共 {len(paragraphs)} 段）", ref=ref["raw"])
                continue
            evidence.append("\n".join(paragraph["text"] for paragraph in hit))
            if source.get("kind") == "spoken":
                cleaned = load_content(project, source, "clean")
                if cleaned:
                    hit_clean = select_paragraphs(cleaned, ref, 1)
                    if hit_clean:
                        evidence.append("\n".join(paragraph["text"] for paragraph in hit_clean))
        if refs:
            stats["sourced"] += 1
        if opinion:
            stats["opinion"] += 1
        hay = "\n".join(evidence)

        for quote in quotes_in(block["text"]):
            stats["quotes"] += 1
            if not refs:
                add("warning" if opinion else "error", "quote-unsourced", block, f"引文“{truncate(quote, 40)}”没有标注出处", quote=quote)
                continue
            if not evidence:
                continue
            result = match_quote(quote, hay)
            if result["kind"] == "none":
                add(
                    "error",
                    "quote-mismatch",
                    block,
                    f"引文“{truncate(quote, 40)}”在 {'、'.join(ref['raw'] for ref in refs)} 中找不到（相似度 {result['score'] * 100:.0f}%）：改回原话、改成转述（去掉引号），或换正确的出处",
                    quote=quote,
                    refs=[ref["raw"] for ref in refs],
                )
            elif result["kind"] == "fuzzy":
                add(
                    "warning",
                    "quote-approximate",
                    block,
                    f"引文“{truncate(quote, 40)}”与原文只是近似（相似度 {result['score'] * 100:.0f}%）；直接引语建议与原文一致",
                    quote=quote,
                    refs=[ref["raw"] for ref in refs],
                )

        for figure in figures_in(block["text"]):
            stats["figures"] += 1
            if not refs:
                add(
                    "warning" if opinion else "error",
                    "figure-in-opinion" if opinion else "figure-unsourced",
                    block,
                    f"数据“{figure['raw']}”{'出现在作者观点段落中，确认是否需要出处' if opinion else '没有标注出处'}",
                    figure=figure["raw"],
                )
                continue
            if not evidence:
                continue
            if figure["value"] not in hay.replace(",", ""):
                add(
                    "error",
                    "figure-mismatch",
                    block,
                    f"数据“{figure['raw']}”在 {'、'.join(ref['raw'] for ref in refs)} 中找不到：核对数值，或改成定性描述",
                    figure=figure["raw"],
                    refs=[ref["raw"] for ref in refs],
                )

        if not refs and not opinion and block["kind"] != "quote" and char_count(plain) >= MIN_UNSOURCED_CHARS:
            stats["unsourced"] += 1
            add("warning", "unsourced-claim", block, "这段没有出处也没有标 [观点]：补出处、标为作者观点，或确认是过渡/常识性表述")

    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    unused = [source["id"] for source in project.meta.get("sources", []) if not source.get("excluded") and source.get("id") not in cited]
    return {
        "file": file,
        "checkedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "articleHash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        "bodyHash": body_hash(text),
        "summary": {"errors": errors, "warnings": warnings, **stats, "citedSources": sorted(cited), "unusedSources": unused},
        "issues": issues,
    }
