"""文笔风格：全局模板 + 每个项目一份副本。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .errors import ApiError, NotFound
from .storage import write_text

STYLE_FILES = {"writing": "writing-style.md"}
STYLE_LABELS = {"writing": "文笔风格"}
STYLE_MAX_CHARS = 12000

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

DEFAULTS = {"writing": DEFAULT_WRITING_STYLE}


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

    # ── 全局风格：所有文章共用一份 ─────────────────────────────

    def list_global(self) -> dict[str, Any]:
        files = []
        for kind, filename in STYLE_FILES.items():
            data = self.read(self.root, kind)
            files.append(
                {
                    "kind": kind,
                    "label": STYLE_LABELS[kind],
                    "filename": filename,
                    "exists": data["exists"],
                    "text": data["text"],
                    "bytes": len(data["text"].encode("utf8")),
                    "defaultText": DEFAULTS[kind],
                }
            )
        return {"files": files, "root": str(self.root), "maxChars": STYLE_MAX_CHARS}

    def _session_dirs(self) -> list[Path]:
        directory = self.root / "sessions"
        if not directory.is_dir():
            return []
        return sorted(
            (path for path in directory.iterdir() if path.is_dir() and not path.name.startswith(".")),
            key=lambda path: path.name,
        )

    def changed_ids(self, slug: str) -> list[str]:
        """哪些风格文件被这篇文章单独改过（与全局不一致）。"""
        directory = self.session_dir(slug)
        changed = []
        for kind in STYLE_FILES:
            local = _style_path(directory, kind)
            global_file = _style_path(self.root, kind)
            if local.exists() and global_file.exists() and local.read_text("utf8") != global_file.read_text("utf8"):
                changed.append(kind)
        return changed

    def sessions(self) -> list[dict[str, Any]]:
        return [{"slug": directory.name, "changed": self.changed_ids(directory.name)} for directory in self._session_dirs()]

    @staticmethod
    def _validate(kind: str, text: Any) -> str:
        if kind not in STYLE_FILES:
            raise ApiError(f"未知风格文件：{kind}", "BAD_STYLE")
        if not isinstance(text, str) or not text.strip():
            raise ApiError("风格内容不能为空", "BAD_STYLE")
        if len(text) > STYLE_MAX_CHARS:
            raise ApiError(f"风格文件不能超过 {STYLE_MAX_CHARS} 字符", "BAD_STYLE")
        return text if text.endswith("\n") else text + "\n"

    def write_global(
        self,
        kind: str,
        text: str,
        *,
        sync: bool = True,
        overwrite_custom: bool = False,
    ) -> dict[str, Any]:
        """写全局风格；默认同步到所有「跟随全局」的文章，单独改过的保留。"""
        value = self._validate(kind, text)
        target = _style_path(self.root, kind)
        previous = target.read_text("utf8") if target.exists() else None
        write_text(target, value)
        synced = 0
        skipped: list[str] = []
        if sync:
            for directory in self._session_dirs():
                copy = _style_path(directory, kind)
                current = copy.read_text("utf8") if copy.exists() else None
                if not overwrite_custom and previous is not None and current is not None and current != previous:
                    skipped.append(directory.name)
                    continue
                write_text(copy, value)
                synced += 1
        return {"synced": synced, "skipped": skipped}

    def sync(
        self,
        kind: str,
        *,
        slug: str | None = None,
        overwrite_custom: bool = True,
    ) -> dict[str, Any]:
        """把全局风格覆盖到文章副本；slug 为空时覆盖全部。"""
        if kind not in STYLE_FILES:
            raise ApiError(f"未知风格文件：{kind}", "BAD_STYLE")
        source = _style_path(self.root, kind)
        if not source.exists():
            write_text(source, DEFAULTS[kind])
        value = source.read_text("utf8")
        targets = [self.session_dir(slug)] if slug else self._session_dirs()
        synced = 0
        skipped: list[str] = []
        for directory in targets:
            copy = _style_path(directory, kind)
            if not overwrite_custom and copy.exists() and copy.read_text("utf8") != value:
                skipped.append(directory.name)
                continue
            write_text(copy, value)
            synced += 1
        return {"synced": synced, "skipped": skipped}

    def reset_global(self, kind: str, *, sync: bool = True) -> dict[str, Any]:
        return self.write_global(kind, DEFAULTS.get(kind, ""), sync=sync, overwrite_custom=True)

    def changed_kinds(self, slug: str) -> list[str]:
        directory = self.ensure_session(slug)
        changed = []
        for kind in STYLE_FILES:
            local = self.read(directory, kind)
            global_file = self.read(self.root, kind)
            if local["exists"] and global_file["exists"] and local["text"] != global_file["text"]:
                changed.append(STYLE_LABELS[kind])
        return changed
