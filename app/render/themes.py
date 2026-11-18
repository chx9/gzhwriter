"""主题库：CSS 资产、元信息、项目选择与样式表解析。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from ..errors import ApiError, NotFound

THEME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ASSETS = Path(__file__).resolve().parent / "assets"
MOXIA_BASE = "moxia-base"
DEFAULT_FILE = "default.txt"
BUILTIN_IDS = ["wechat", "business", "swiss", "manfu", "gelv", "jiaotang", "moyun", "qingkong", "shiping", "shiguang", "sujian"]


def theme_id(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text if THEME_ID_RE.match(text) else None


def parse_theme_meta(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    head = str(text or "")[:4000]
    comments = []
    for match in re.finditer(r"/\*([\s\S]*?)\*/", head):
        lines = [re.sub(r"^\s*\*?\s?", "", line).rstrip() for line in match.group(1).split("\n")]
        comments.append("\n".join(lines))

    def take(key: str) -> str | None:
        for block in comments:
            found = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+)$", block, re.I | re.M)
            if found:
                return found.group(1).strip()
        return None

    for key in ("name", "description", "footer", "seal", "label", "accent", "font"):
        value = take(key)
        if value:
            meta[key] = value
    for block in comments:
        found = re.search(r"^\s*guide\s*:\s*([\s\S]*)$", block, re.I | re.M)
        if found and found.group(1).strip():
            meta["guide"] = found.group(1).strip()
            break
    return meta


def accent_of_css(text: str) -> str | None:
    match = re.search(r"--accent\s*:\s*([^;{}]+)", str(text or ""))
    value = match.group(1).strip() if match else ""
    return value if re.match(r"^#[0-9a-fA-F]{3,6}$", value) else None


def mix_with_white(hex_color: str, ratio: float) -> str:
    match = re.match(r"^#?([0-9a-fA-F]{6})$", str(hex_color or "").strip())
    if not match:
        return str(hex_color or "")
    number = int(match.group(1), 16)
    channels = []
    for shift in (16, 8, 0):
        original = (number >> shift) & 255
        channels.append(round(original * ratio + 255 * (1 - ratio)))
    return "#" + "".join(f"{channel:02x}" for channel in channels)


class ThemeLibrary:
    def __init__(self, styles_root: Path, fallback: str = "wechat") -> None:
        self.root = Path(styles_root)
        self.dir = self.root / "themes"
        self.fallback = theme_id(fallback) or "wechat"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seed()

    def _seed(self) -> None:
        assets = ASSETS / "themes"
        for source in assets.glob("*.css"):
            target = self.dir / source.name
            if not target.exists():
                shutil.copyfile(source, target)
        default = self.dir / DEFAULT_FILE
        if not default.exists():
            default.write_text(f"{self.fallback}\n", "utf8")

    def _path(self, theme: str) -> Path:
        return self.dir / f"{theme}.css"

    def read_css(self, theme: str | None) -> str | None:
        ident = theme_id(theme)
        if not ident:
            return None
        path = self._path(ident)
        if path.exists():
            return path.read_text("utf8")
        asset = ASSETS / "themes" / f"{ident}.css"
        return asset.read_text("utf8") if asset.exists() else None

    def write_css(self, theme: str, css: str) -> Path:
        ident = theme_id(theme)
        if not ident:
            raise ApiError("主题 id 只能用小写字母、数字、-", "BAD_THEME")
        if not str(css or "").strip():
            raise ApiError("主题内容不能为空", "BAD_THEME")
        if len(css) > 60000:
            raise ApiError("主题 CSS 不能超过 60000 字符", "BAD_THEME")
        target = self._path(ident)
        target.write_text(css if css.endswith("\n") else css + "\n", "utf8")
        return target

    def list(self) -> list[dict[str, Any]]:
        items: dict[str, dict[str, Any]] = {}
        for ident in BUILTIN_IDS:
            css = self.read_css(ident)
            meta = parse_theme_meta(css or "")
            items[ident] = {
                "id": ident,
                "name": meta.get("name") or ident,
                "description": meta.get("description", ""),
                "accent": accent_of_css(css or "") or meta.get("accent"),
                "builtin": True,
                "source": "builtin" if not self._path(ident).exists() else "file",
            }
        for path in sorted(self.dir.glob("*.css")):
            ident = path.stem
            if not THEME_ID_RE.match(ident) or ident == MOXIA_BASE:
                continue
            css = path.read_text("utf8")
            meta = parse_theme_meta(css)
            items[ident] = {
                "id": ident,
                "name": meta.get("name") or ident,
                "description": meta.get("description", ""),
                "accent": accent_of_css(css) or meta.get("accent"),
                "builtin": ident in BUILTIN_IDS,
                "source": "file",
                "path": str(path),
                "bytes": len(css),
            }
        return sorted(items.values(), key=lambda item: (not item["builtin"], item["name"]))

    def default_id(self) -> str:
        stored = theme_id((self.dir / DEFAULT_FILE).read_text("utf8").strip() if (self.dir / DEFAULT_FILE).exists() else None)
        return stored or self.fallback

    def set_default(self, theme: str) -> str:
        ident = theme_id(theme)
        if not ident or not self.read_css(ident):
            raise ApiError(f"没有这个主题：{theme}", "NO_THEME")
        (self.dir / DEFAULT_FILE).write_text(f"{ident}\n", "utf8")
        return ident

    def session_dir(self, slug: str) -> Path:
        return self.root / "sessions" / slug

    def session_theme(self, slug: str) -> str | None:
        path = self.session_dir(slug) / "theme.txt"
        if not path.exists():
            return None
        return theme_id(path.read_text("utf8").strip())

    def set_session_theme(self, slug: str, theme: str | None) -> None:
        directory = self.session_dir(slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "theme.txt"
        if theme is None:
            path.unlink(missing_ok=True)
            return
        ident = theme_id(theme)
        if not ident or not self.read_css(ident):
            raise ApiError(f"没有这个主题：{theme}", "NO_THEME")
        path.write_text(f"{ident}\n", "utf8")

    def effective_id(self, slug: str, legacy: str | None = None) -> str:
        return self.session_theme(slug) or self.default_id() or theme_id(legacy or "") or self.fallback

    def resolve(self, theme: str | None, *, accent: str | None = None) -> dict[str, Any]:
        ident = theme_id(theme) or self.default_id()
        css = self.read_css(ident)
        if css is None:
            ident = self.fallback
            css = self.read_css(ident) or ""
        meta = parse_theme_meta(css)
        is_moxia = bool(re.search(r"#moxia\b", css))
        base = self.read_css(MOXIA_BASE) if is_moxia else None
        if base is None:
            base = (ASSETS / "base.css").read_text("utf8")
        derived = ""
        if is_moxia:
            own_soft = re.search(r"--accent-soft\s*:", css) is not None
            own_light = re.search(r"--accent-light\s*:", css) is not None
            accent_value = accent if accent and re.match(r"^#[0-9a-fA-F]{3,6}$", accent) else (accent_of_css(css) or "")
            if accent_value:
                rules = []
                if not own_soft:
                    rules.append(f"#moxia {{ --accent-soft: {mix_with_white(accent_value, 0.08)}; }}")
                if not own_light:
                    rules.append(f"#moxia {{ --accent-light: {mix_with_white(accent_value, 0.2)}; }}")
                derived = "\n".join(rules)
        accent_rule = f":root {{ --accent: {accent}; }}" if accent and re.match(r"^#[0-9a-fA-F]{3,6}$", accent) else ""
        sheet = "\n".join(part for part in [base, css, derived, accent_rule] if part)
        return {
            "id": ident,
            "css": sheet,
            "meta": {**meta, **({"accent": accent} if accent else {})},
            "exists": True,
        }

    def read_theme(self, theme: str) -> dict[str, Any]:
        ident = theme_id(theme)
        css = self.read_css(ident)
        if css is None:
            raise NotFound(f"没有这个主题：{theme}", "NO_THEME")
        return {"theme": ident, "css": css, "meta": parse_theme_meta(css), "path": str(self._path(ident))}

    def preview(self, theme: str) -> dict[str, Any]:
        from .css import inline_css
        from .markdown import markdown_to_html

        sample = """## 01 / 先建立储蓄习惯

每个月收到收入后，先为自己安排一笔储蓄，再规划日常消费。比例可以从 **20%** 开始。

> 真正的财富积累，往往从管理好第一笔存款开始。

> [!TIP] 理财小建议
> 先从记录支出、设定储蓄目标开始。

```cards
示例月收入 | ¥10,000 | 演示数据
示例储蓄 | ¥2,000 | 演示数据
示例比例 | 20% | 演示数据
```

```chart
title: 财富积累示意图
第1年 | 30
第2年 | 53
第3年 | 80
第4年 | 95
```

> [!WARNING] 风险提醒
> 任何承诺「稳赚不赔」的产品都要格外警惕。"""
        resolved = self.resolve(theme)
        semantic = f'<section class="wx-article">\n{markdown_to_html(sample, {"links": [], "images": [], "pending": []})}\n</section>'
        html, warnings = inline_css(semantic, resolved["css"])
        return {"theme": resolved["id"], "name": resolved["meta"].get("name", resolved["id"]), "html": html, "warnings": warnings}
