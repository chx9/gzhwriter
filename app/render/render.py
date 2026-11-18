"""文章渲染：article.md → dist/article.html + dist/preview.html。"""

from __future__ import annotations

import base64
import html as html_lib
import mimetypes
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..materials import MARKER_RE, OPINION_RE, find_source, refs_in
from ..storage import Project, char_count, parse_frontmatter, read_text, sha256, write_text
from .css import inline_css
from .markdown import markdown_to_html

PREVIEW_IMAGE_LIMIT = 5 * 1024 * 1024
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def attr_esc(value: Any) -> str:
    return esc(value).replace('"', "&quot;")


def apply_citations(body: str, mode: str) -> tuple[str, list[str]]:
    order: list[str] = []

    def note(source_id: str) -> int:
        if source_id not in order:
            order.append(source_id)
        return order.index(source_id) + 1

    out = str(body)
    if mode == "footnotes":
        out = MARKER_RE.sub(
            lambda match: "".join(f"^[{note(source_id)}]" for source_id in dict.fromkeys(r["id"] for r in refs_in(match.group(0)))),
            out,
        )
    else:
        for ref in refs_in(out):
            note(ref["id"])
        out = MARKER_RE.sub("", out)
    out = OPINION_RE.sub("", out)
    out = re.sub(r"[ \t]+([，。！？；：、,.!?;:）)」”])", r"\1", out)
    out = re.sub(r"^[ \t]+", "", out, flags=re.M)
    out = re.sub(r"[ \t]+$", "", out, flags=re.M)
    return out, order


def source_line(source: dict[str, Any]) -> str:
    origin = source.get("origin") or {}
    parts = [str(source.get("title", ""))]
    if origin.get("author"):
        parts.append(str(origin["author"]))
    if origin.get("type") == "url":
        parts.append(str(origin.get("url", "")))
    return "，".join(parts)


def footer_from(meta: dict[str, str], theme_meta: dict[str, Any], moxia: bool) -> list[str]:
    value = meta.get("footer") or theme_meta.get("footer")
    if not value or value in {"none", "off"}:
        return []
    if moxia:
        return [str(value).strip()]
    return [part.strip() for part in str(value).split("|") if part.strip()]


def local_image_data_url(project_dir: Path, src: str) -> str | None:
    if re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) or src.startswith("/") or ".." in src:
        return None
    suffix = Path(src).suffix.lower()
    mime = MIME.get(suffix)
    if not mime:
        return None
    try:
        target = (project_dir / src).resolve()
        target.relative_to(project_dir.resolve())
        data = target.read_bytes()
    except (OSError, ValueError):
        return None
    if len(data) > PREVIEW_IMAGE_LIMIT:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def compose_article(
    project: Project,
    text: str,
    *,
    theme_css: str,
    theme: str = "wechat",
    theme_meta: dict[str, Any] | None = None,
    accent: str | None = None,
    citation_mode: str = "strip",
    author: str = "",
) -> dict[str, Any]:
    """把 article.md 文本渲染成 HTML（不写文件）。返回 fragment / preview_html / info。"""
    theme_meta = theme_meta or {}
    meta, body = parse_frontmatter(text)
    mode = citation_mode if citation_mode in {"references", "strip", "footnotes"} else "strip"
    body, cited = apply_citations(body, mode)
    title = meta.get("title") or project.meta.get("title") or project.slug
    body = re.sub(
        r"^\s*#\s+(.+)\n",
        lambda match: "" if meta.get("title") and match.group(1).strip() == meta["title"].strip() else match.group(0),
        body,
        count=1,
    )
    moxia = bool(re.search(r"#moxia\b", theme_css))
    ctx: dict[str, Any] = {"links": [], "images": [], "pending": [], "moxiaChrome": moxia}
    content = markdown_to_html(re.sub(r"\^\[(\d+)\]", lambda m: f"\x01{m.group(1)}\x01", body), ctx)
    content = re.sub(r"\x01(\d+)\x01", lambda m: f"<sup>[{m.group(1)}]</sup>", content)

    head: list[str] = []
    label = meta.get("label") or theme_meta.get("label")
    if label:
        head.append(f'<p class="wx-label eyebrow">{esc(label)}</p>')
    if meta.get("byline"):
        head.append(f'<p class="wx-byline">{esc(meta["byline"])}</p>')

    tail: list[str] = []
    if ctx["links"]:
        tail.append('<p class="wx-refs-title">文中链接</p>')
        tail.append(
            '<ol class="wx-refs">'
            + "".join(f'<li>{esc(item["label"])}：{esc(item["href"])}</li>' for item in ctx["links"])
            + "</ol>"
        )
    sources = [find_source(project, source_id) for source_id in cited]
    sources = [source for source in sources if source]
    if mode != "strip" and sources:
        tail.append('<p class="wx-refs-title">参考来源</p>')
        tail.append('<ol class="wx-refs">' + "".join(f"<li>{esc(source_line(source))}</li>" for source in sources) + "</ol>")
    footer_lines = footer_from(meta, theme_meta, moxia)
    if footer_lines:
        if moxia:
            tail.append(f'<p class="wx-footer brand-footer">{esc("|".join(footer_lines))}</p>')
        else:
            seal_text = meta.get("seal") or theme_meta.get("seal")
            seal = f'<section class="wx-seal">{esc(seal_text)}</section>' if seal_text else ""
            first, rest = footer_lines[0], footer_lines[1:]
            tail.append(
                f'<section class="wx-footer">{seal}<p class="wx-footer-line wx-footer-title">{esc(first)}</p>'
                + "".join(f'<p class="wx-footer-line">{esc(line)}</p>' for line in rest)
                + "</section>"
            )

    semantic = f'<section class="wx-article" id="moxia">\n{chr(10).join(head)}\n{content}\n{chr(10).join(tail)}\n</section>'
    rendered, warnings = inline_css(semantic, theme_css, {"--accent": accent} if accent else {})
    fragment = rendered + "\n"

    by = meta.get("author") or author or ""
    minutes = max(1, round(char_count(body) / 400))
    preview_fragment = fragment
    for src in set(ctx["images"]):
        data_url = local_image_data_url(project.dir, src)
        if data_url:
            preview_fragment = preview_fragment.replace(f'src="{esc(src)}"', f'src="{data_url}"')
    preview = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title></head>
<body style="margin:0;background:#ededed;">
<div style="max-width:375px;margin:24px auto;background:#ffffff;padding:20px 16px 40px;box-shadow:0 2px 12px rgba(0,0,0,.08);">
<h1 style="font-size:22px;line-height:1.4;margin:0 0 12px;color:#111;font-weight:bold;">{esc(title)}</h1>
<div style="font-size:15px;color:#576b95;margin-bottom:22px;">{esc(by)}{'&nbsp;&nbsp;' if by else ''}<span style="color:#999">{date.today().isoformat()} · 阅读约 {minutes} 分钟</span></div>
<div id="wx-article">
{preview_fragment}
</div>
</div>
<div style="max-width:375px;margin:0 auto 40px;text-align:center;">
<button id="copy" style="font-size:15px;padding:10px 22px;border:none;border-radius:6px;background:#07c160;color:#fff;cursor:pointer;">复制到公众号</button>
<div id="tip" style="font-size:13px;color:#888;margin-top:8px;"></div>
</div>
<script>
document.getElementById('copy').onclick = async () => {{
  const node = document.getElementById('wx-article');
  const html = node.innerHTML;
  try {{
    await navigator.clipboard.write([new ClipboardItem({{ 'text/html': new Blob([html], {{ type: 'text/html' }}), 'text/plain': new Blob([node.innerText], {{ type: 'text/plain' }}) }})]);
  }} catch (e) {{
    const range = document.createRange(); range.selectNodeContents(node);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range); document.execCommand('copy'); sel.removeAllRanges();
  }}
  document.getElementById('tip').textContent = '已复制，到公众号编辑器里直接粘贴';
}};
</script>
</body></html>
"""
    return {
        "renderedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "articleHash": sha256(text),
        "theme": theme,
        "accent": accent or None,
        "citationMode": mode,
        "title": title,
        "links": len(ctx["links"]),
        "images": ctx["images"],
        "pendingImages": ctx["pending"],
        "citedSources": cited,
        "htmlChars": len(fragment),
        "warnings": warnings,
        "fragment": fragment,
        "previewHtml": preview,
        # 片段版（图片已内联 data URL）：供前端直接渲染进 div，无需 iframe
        "previewFragment": preview_fragment,
        "byline": by,
        "minutes": minutes,
    }


def render_article(
    project: Project,
    *,
    theme_css: str,
    theme: str = "wechat",
    theme_meta: dict[str, Any] | None = None,
    accent: str | None = None,
    citation_mode: str = "strip",
    author: str = "",
) -> dict[str, Any]:
    text = read_text(project.dir / "article.md", None)
    if text is None:
        raise ValueError("article.md does not exist yet")
    result = compose_article(
        project,
        text,
        theme_css=theme_css,
        theme=theme,
        theme_meta=theme_meta,
        accent=accent,
        citation_mode=citation_mode,
        author=author,
    )
    write_text(project.dir / "dist/article.html", result["fragment"])
    write_text(project.dir / "dist/preview.html", result["previewHtml"])
    return result
