"""文笔风格与排版补充说明：全局模板 + 每个项目一份副本。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .errors import ApiError, NotFound
from .storage import parse_frontmatter, read_text, write_text

STYLE_FILES = {"layout": "layout-style.md", "writing": "writing-style.md"}
STYLE_LABELS = {"layout": "排版补充说明", "writing": "文笔风格"}
STYLE_MAX_CHARS = 12000

DEFAULT_LAYOUT_STYLE = """---
# 排版补充说明：真正的版面由「排版主题」（themes/<id>.css）决定，这里只写主题之外想追加的纪律。
# 这几项是可选的机器参数，改动下次排版生效：
#   accent     覆盖主题的强调色（#rrggbb）
#   label      文章顶部栏目标签的默认值
#   footer     文末品牌卡片文案，「标题|副标题」（none 表示不显示）
# theme 只作兜底（换主题用「风格」页点选，或 API POST /theme）
theme: wechat
imageAlign: center
---

# 排版补充

> 主题负责「长什么样」，这份文件负责「还要注意什么」。可以直接改，也可以让 AI 改。

## 还要注意

- 每一节只讲一件事，手机上每段 2–4 行。
- 强调服务于判断和数字：粗体每 2–3 段一处，同一处只用一种强调。
- 全文 2–4 张图，图注写清图里是什么，不写「仅供参考」。
"""

DEFAULT_WRITING_STYLE = """# 文笔风格

> 全局配置：所有文章都按这份要求写。可以直接改这个文件，也可以在「风格」页编辑。

## 定位

- 读者：关心财经、社会和科技议题的普通读者，愿意读 3000 字左右的长文，但没耐心看术语和套话。
- 姿态：像一个认真做过功课的朋友，把一件复杂的事讲清楚，并给出自己的判断。理性、克制、不煽情、不说教。

## 开头

- 前三行就要给出一个具体的场景、反常的事实或尖锐的问题，让读者想知道“为什么”。
- 不从“近年来”“随着……的发展”这类背景铺垫开始。

## 结构

- 每一节回答一个问题；先给结论，再给理由和证据。
- 节与节之间有推进关系（是什么 → 为什么 → 意味着什么），不是素材的平铺罗列。
- 用自己的框架组织内容：同样的事实，换一个切入角度和论证顺序。

## 小节标题：必须带数字编号

每个小节标题都用「两位数字 + 斜杠 + 短语」的格式，按顺序编号，从 01 开始、不跳号：

```
## 01 / 先建立储蓄习惯
## 02 / 理解长期积累的价值
## 03 / 建立自己的财富计划
```

- 编号是文章的骨架，让读者一眼看到结构、知道读到第几步；即使只有两三节也要编号。
- 斜杠两边各留一个空格；数字用 01、02、03 这种两位写法（不是 1、2、3，也不用 ①、一、二）。
- 编号后面那句短语是这一节真正的标题，写结论、不写问句，控制在 16 字以内，不加句号。
- 全文只用一个编号序列；不要在一节里再套一层编号（小节内部的小标题用加粗或列表即可）。
- 小节标题统一用 `##`，正文不用一级标题（文章标题在 frontmatter 里）。

## 句子与用词

- 短句为主，一句一个意思；主语明确，少用长定语和被动句。
- 具体胜过抽象：用人、地点、时间、数字说话；数字写清口径（哪一年、和谁比）。
- 不用套话：首先/其次/最后、值得一提的是、不难看出、众所周知、综上所述、在……的背景下、赋能、抓手、底层逻辑、让我们一起……。
- 不用标题党和情绪词：震惊、炸裂、细思极恐、太可怕了。
- 少用排比和反问，少用“！”。

## 观点

- 每节至少有一个作者自己的判断，并说明理由；判断要能被反驳，而不是正确的废话。
- 承认不确定性：证据不足的地方说“可能”“目前看”，不把推测写成定论。

## 结尾

- 回到开头的问题，给读者一个可以带走的判断或视角。
- 不喊口号，不写“你怎么看？欢迎留言”“点个在看”。"""

DEFAULTS = {"layout": DEFAULT_LAYOUT_STYLE, "writing": DEFAULT_WRITING_STYLE}


def _style_path(directory: Path, kind: str) -> Path:
    if kind not in STYLE_FILES:
        raise ApiError(f"未知风格文件：{kind}", "BAD_STYLE")
    return directory / STYLE_FILES[kind]


class StyleStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.seed()

    def seed(self) -> None:
        for kind, text in DEFAULTS.items():
            path = _style_path(self.root, kind)
            if not path.exists():
                write_text(path, text)

    def read(self, directory: Path, kind: str, *, max_chars: int = STYLE_MAX_CHARS) -> dict[str, Any]:
        path = _style_path(directory, kind)
        if not path.exists():
            return {"kind": kind, "path": str(path), "exists": False, "text": "", "truncated": False}
        text = path.read_text("utf8")
        truncated = len(text) > max_chars
        return {"kind": kind, "path": str(path), "exists": True, "text": text[:max_chars], "truncated": truncated}

    def write(self, directory: Path, kind: str, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ApiError("风格内容不能为空", "BAD_STYLE")
        if len(text) > STYLE_MAX_CHARS:
            raise ApiError(f"风格文件不能超过 {STYLE_MAX_CHARS} 字符", "BAD_STYLE")
        write_text(_style_path(directory, kind), text if text.endswith("\n") else text + "\n")
        return self.read(directory, kind)

    def session_dir(self, slug: str) -> Path:
        return self.root / "sessions" / slug

    def ensure_session(self, slug: str) -> Path:
        directory = self.session_dir(slug)
        directory.mkdir(parents=True, exist_ok=True)
        for kind in STYLE_FILES:
            target = _style_path(directory, kind)
            if target.exists():
                continue
            source = _style_path(self.root, kind)
            if source.exists():
                shutil.copyfile(source, target)
            else:
                write_text(target, DEFAULTS[kind])
        return directory

    def list_session(self, slug: str) -> dict[str, Any]:
        directory = self.ensure_session(slug)
        files = []
        for kind, filename in STYLE_FILES.items():
            local = self.read(directory, kind)
            global_file = self.read(self.root, kind)
            files.append(
                {
                    "path": filename,
                    "kind": kind,
                    "label": STYLE_LABELS[kind],
                    "exists": local["exists"],
                    "bytes": len(local["text"].encode("utf8")),
                    "changed": local["exists"] and global_file["exists"] and local["text"] != global_file["text"],
                }
            )
        return {
            "scope": "styles",
            "slug": slug,
            "title": "本项目风格",
            "globalDir": str(self.root),
            "sessionDir": str(directory),
            "files": files,
        }

    def action(self, slug: str, kind: str, action: str) -> dict[str, Any]:
        directory = self.ensure_session(slug)
        if action not in {"promote", "reset"}:
            raise ApiError("action 只能是 promote 或 reset", "BAD_ACTION")
        if action == "promote":
            source, target = _style_path(directory, kind), _style_path(self.root, kind)
        else:
            source, target = _style_path(self.root, kind), _style_path(directory, kind)
        if not source.exists():
            raise NotFound(f"找不到 {source}", "NO_FILE")
        write_text(target, source.read_text("utf8"))
        return self.list_session(slug)

    def layout_params(self, slug: str) -> dict[str, Any]:
        directory = self.ensure_session(slug)
        guide = self.read(directory, "layout")
        if not guide["exists"]:
            return {}
        meta, _ = parse_frontmatter(guide["text"])
        out: dict[str, Any] = {}
        if meta.get("theme"):
            out["legacyTheme"] = meta["theme"]
        if meta.get("accent") and re.match(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", meta["accent"]):
            out["accent"] = meta["accent"]
        if meta.get("imageAlign") in {"center", "left"}:
            out["imageAlign"] = meta["imageAlign"]
        for key in ("label", "footer", "seal"):
            if meta.get(key):
                out[key] = meta[key]
        return out

    def changed_kinds(self, slug: str) -> list[str]:
        directory = self.ensure_session(slug)
        changed = []
        for kind in STYLE_FILES:
            local = self.read(directory, kind)
            global_file = self.read(self.root, kind)
            if local["exists"] and global_file["exists"] and local["text"] != global_file["text"]:
                changed.append(STYLE_LABELS[kind])
        return changed
