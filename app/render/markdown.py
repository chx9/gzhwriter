"""Markdown → 公众号语义 HTML。

只产出结构，不带颜色/字号；样式由 css.py 的主题引擎后处理。
"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

PENDING_SRC = "ww-img:pending"
IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\((\S+?)(?:\s+\"([^\"]*)\")?\)\s*$")
CHART_HEIGHT = 120

CALLOUTS = {
    "TIP": {"mod": "tip", "title": "提示"},
    "NOTE": {"mod": "note", "title": "说明"},
    "INFO": {"mod": "note", "title": "说明"},
    "IMPORTANT": {"mod": "tip", "title": "重点"},
    "WARNING": {"mod": "warn", "title": "注意"},
    "CAUTION": {"mod": "warn", "title": "风险提醒"},
}


def esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def attr_esc(value: Any) -> str:
    return esc(value).replace('"', "&quot;")


def parse_keywords(title: str | None) -> list[str]:
    raw = re.sub(r"^\s*google\s*[:：]\s*", "", str(title or ""), flags=re.I)
    return [part.strip() for part in raw.split("|") if part.strip()]


def google_images_url(keyword: str) -> str:
    from urllib.parse import quote

    return f"https://www.google.com/search?tbm=isch&q={quote(keyword)}"


def inline(text: str, ctx: dict[str, Any]) -> str:
    codes: list[str] = []

    def code_repl(match: re.Match[str]) -> str:
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    value = re.sub(r"`([^`]+)`", code_repl, str(text))
    value = esc(value)
    value = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)",
        lambda m: f'<img src="{attr_esc(m.group(2))}" alt="{attr_esc(m.group(1))}" class="wx-img">',
        value,
    )

    def link_repl(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        if re.match(r"^https?://mp\.weixin\.qq\.com/", href):
            return f'<a href="{href}">{label}</a>'
        if re.match(r"^https?://", href):
            links = ctx["links"]
            index = next((i for i, item in enumerate(links) if item["href"] == href), -1) + 1
            if index == 0:
                links.append({"href": href, "label": label})
                index = len(links)
            return f"{label}<sup>[{index}]</sup>"
        return label

    value = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", link_repl, value)

    def linkify_repl(match: re.Match[str]) -> str:
        prefix, url = match.group(1), match.group(2)
        if ctx.get("linkify") is False:
            return match.group(0)
        if re.match(r"^https?://mp\.weixin\.qq\.com/", url):
            return f'{prefix}<a href="{attr_esc(url)}">{url}</a>'
        links = ctx["links"]
        index = next((i for i, item in enumerate(links) if item["href"] == url), -1) + 1
        if index == 0:
            links.append({"href": url, "label": url})
            index = len(links)
        return f"{prefix}{url}<sup>[{index}]</sup>"

    value = re.sub(r"(^|[^\"'=\w])(https?://[^\s<>\"'）)]+)", linkify_repl, value)
    value = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", value)
    value = re.sub(r"\+\+([^+\n]+?)\+\+", r"<u>\1</u>", value)
    value = re.sub(r"==([^=\n]+?)==", r"<mark>\1</mark>", value)
    value = re.sub(r"(^|[^*])\*([^*\n]+)\*(?!\*)", lambda m: f"{m.group(1)}<em>{m.group(2)}</em>", value)
    value = re.sub(r"~~([^~]+)~~", r"<s>\1</s>", value)
    value = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{esc(codes[int(m.group(1))])}</code>", value)
    return value


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in re.sub(r"^\||\|$", "", line.strip()).split("|")]


def render_table(lines: list[str], ctx: dict[str, Any]) -> str:
    head = _cells(lines[0])
    rows = [_cells(line) for line in lines[2:]]
    th = "".join(f'<th class="wx-th">{inline(cell, ctx)}</th>' for cell in head)
    tr = "".join(
        "<tr>" + "".join(f'<td class="wx-td">{inline(cell, ctx)}</td>' for cell in row) + "</tr>" for row in rows
    )
    return f'<section class="wx-table-wrap table-wrap"><table class="wx-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></section>'


def render_cards(body: str, ctx: dict[str, Any]) -> str:
    rows = [line.strip() for line in body.split("\n") if line.strip() and not line.strip().startswith("#")]
    cards: list[str] = []
    for row in rows:
        parts = [part.strip() for part in row.split("|")]
        label = parts[0] if len(parts) > 0 else ""
        value = parts[1] if len(parts) > 1 else ""
        note = parts[2] if len(parts) > 2 else ""
        cards.append(
            f'<section class="wx-card data-card"><p class="wx-card-label data-label">{inline(label, ctx)}</p>'
            f'<p class="wx-card-value data-value">{inline(value, ctx)}</p>'
            + (f'<p class="wx-card-note data-note">{inline(note, ctx)}</p>' if note else "")
            + "</section>"
        )
    count = min(4, max(1, len(cards)))
    return f'<section class="wx-cards wx-cards-{count} data-cards">{"".join(cards)}</section>'


def _js_round(value: float) -> int:
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def render_chart(body: str, ctx: dict[str, Any]) -> str:
    title = ""
    note = ""
    bars: list[dict[str, Any]] = []
    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            continue
        kv = re.match(r"^(title|note)\s*[:：]\s*(.+)$", line, re.I)
        if kv:
            if kv.group(1).lower() == "title":
                title = kv.group(2).strip()
            else:
                note = kv.group(2).strip()
            continue
        parts = [part.strip() for part in line.split("|")]
        name = parts[0] if parts else ""
        value = parts[1] if len(parts) > 1 else ""
        number = re.sub(r"[^\d.-]", "", value)
        try:
            num = float(number) if number else 0.0
        except ValueError:
            num = 0.0
        bars.append({"name": name, "value": value, "num": num})
    if not bars:
        return ""
    maximum = max([bar["num"] for bar in bars] + [0])
    scale = CHART_HEIGHT / maximum if maximum > 0 else 0
    cells: list[str] = []
    for index, bar in enumerate(bars):
        if scale:
            px = max(2, _js_round(abs(bar["num"]) * scale))
        else:
            px = _js_round(CHART_HEIGHT * (0.4 + ((index * 10) % 50) / 100))
        alt = " wx-bar-alt" if index % 2 == 1 else ""
        height = min(CHART_HEIGHT, px)
        value_label = f'<span class="bar-value">{esc(bar["value"])}</span>' if ctx.get("moxiaChrome") else ""
        name_label = f'<span class="bar-label">{esc(bar["name"])}</span>' if ctx.get("moxiaChrome") else ""
        cells.append(
            f'<section class="wx-bar-cell bar-col">{value_label}'
            f'<section class="wx-bar bar{alt}" style="height:{height}px"></section>{name_label}</section>'
        )
    axis = (
        ""
        if ctx.get("moxiaChrome")
        else '<section class="wx-chart-axis">'
        + "".join(f'<section class="wx-chart-axis-cell">{esc(bar["name"])}</section>' for bar in bars)
        + "</section>"
    )
    title_html = f'<p class="wx-chart-title chart-title">{inline(title, ctx)}</p>' if title else ""
    note_html = f'<p class="wx-chart-note chart-note">{inline(note, ctx)}</p>' if note else ""
    return (
        f'<section class="wx-chart bar-chart">{title_html}'
        f'<section class="wx-chart-bars bar-row">{"".join(cells)}</section>{axis}{note_html}</section>'
    )


def render_callout(kind: str, title: str, body_lines: list[str], ctx: dict[str, Any]) -> str:
    conf = CALLOUTS.get(kind.upper(), CALLOUTS["TIP"])
    body = "".join(f'<p class="wx-callout-body">{inline(line.strip(), ctx)}</p>' for line in body_lines if line.strip())
    head = f'<p class="wx-callout-title">{inline(title, ctx)}</p>' if title else ""
    moxia_mod = "callout-warning" if conf["mod"] == "warn" else f"callout-{conf['mod']}"
    return f'<section class="wx-callout wx-callout-{conf["mod"]} callout {moxia_mod}">{head}{body}</section>'


def markdown_to_html(markdown: str, ctx: dict[str, Any] | None = None) -> str:
    ctx = ctx if ctx is not None else {"links": [], "images": [], "pending": []}
    ctx.setdefault("links", [])
    ctx.setdefault("images", [])
    ctx.setdefault("pending", [])
    lines = str(markdown).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    index = 0

    def flush_para() -> None:
        if para:
            out.append(f'<p class="wx-p">{inline(chr(10).join(para), ctx)}</p>')
            para.clear()

    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            flush_para()
            language = line[3:].strip().lower()
            buffer: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                buffer.append(lines[index])
                index += 1
            index += 1
            body = "\n".join(buffer)
            if language == "cards":
                out.append(render_cards(body, ctx))
            elif language == "chart":
                out.append(render_chart(body, ctx))
            else:
                out.append(f'<pre class="wx-pre"><code>{esc(body)}</code></pre>')
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_para()
            raw = heading.group(2).strip()
            level = len(heading.group(1))
            keep_h1 = bool(ctx.get("moxiaChrome")) and level == 1
            cls = "wx-h1" if keep_h1 else "wx-h2" if level <= 2 else "wx-h3" if level == 3 else "wx-h4"
            tag = "h1" if keep_h1 else "h2" if level <= 2 else "h3" if level == 3 else "h4"
            out.append(f'<{tag} class="{cls}"><span class="title">{inline(raw, ctx)}</span></{tag}>')
            index += 1
            continue
        if re.match(r"^\s*(---|\*\*\*|___)\s*$", line):
            flush_para()
            out.append('<hr class="wx-hr">')
            index += 1
            continue
        if re.match(r"^\s*$", line):
            flush_para()
            index += 1
            continue
        if line.startswith(">"):
            flush_para()
            buffer = []
            while index < len(lines) and lines[index].startswith(">"):
                buffer.append(re.sub(r"^>\s?", "", lines[index]))
                index += 1
            head = re.match(r"^\s*\[!([A-Za-z]+)\]\s*(.*)$", buffer[0] if buffer else "")
            if head:
                out.append(render_callout(head.group(1).upper(), head.group(2).strip(), buffer[1:], ctx))
                continue
            out.append(f'<blockquote class="wx-quote"><p>{inline(chr(10).join(buffer), ctx)}</p></blockquote>')
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            flush_para()
            ordered = bool(re.match(r"^\s*\d", line))
            items: list[str] = []
            while index < len(lines) and (
                re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[index])
                or (re.match(r"^\s{2,}\S", lines[index]) and items)
            ):
                if re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[index]):
                    items.append(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", lines[index]))
                else:
                    items[-1] += "\n" + lines[index].strip()
                index += 1
            tag = "ol" if ordered else "ul"
            out.append(f'<{tag} class="wx-list">' + "".join(f'<li class="wx-li">{inline(item, ctx)}</li>' for item in items) + f"</{tag}>")
            continue
        if re.match(r"^\|.*\|\s*$", line) and index + 1 < len(lines) and re.match(r"^\|?\s*:?-{3,}", lines[index + 1]):
            flush_para()
            buffer = []
            while index < len(lines) and re.match(r"^\|.*\|\s*$", lines[index]):
                buffer.append(lines[index])
                index += 1
            out.append(render_table(buffer, ctx))
            continue
        image = IMAGE_LINE_RE.match(line)
        if image:
            flush_para()
            caption, src, title = image.group(1), image.group(2), image.group(3)
            if src == PENDING_SRC:
                keywords = parse_keywords(title)
                kw = (
                    f'<span class="wx-slot-kw">搜图关键词：{esc(" / ".join(keywords))}</span>'
                    if keywords
                    else ""
                )
                out.append(
                    f'<figure class="wx-figure wx-slot-figure"><p class="wx-slot"><strong>【配图待补】</strong>{kw}</p>'
                    + (f'<figcaption class="wx-caption">{esc(caption)}</figcaption>' if caption else "")
                    + "</figure>"
                )
                ctx["pending"].append({"caption": caption, "keywords": keywords})
            else:
                out.append(
                    f'<figure class="wx-figure"><img src="{esc(src)}" alt="{esc(caption)}" class="wx-img">'
                    + (f'<figcaption class="wx-caption">{esc(caption)}</figcaption>' if caption else "")
                    + "</figure>"
                )
                ctx["images"].append(src)
            index += 1
            continue
        para.append(line)
        index += 1
    flush_para()
    return "\n".join(out)
