"""文笔风格：全局模板 + 每个项目一份副本。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .errors import ApiError, NotFound
from .storage import write_text

LEGACY_FILE = "writing-style.md"
STYLE_FILES = {"writing": LEGACY_FILE}
STYLE_LABELS = {"writing": "文笔风格"}
STYLE_MAX_CHARS = 12000
DOC_SUFFIX = ".md"
DOC_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

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


def _filename_for(kind: str) -> str:
    """兼容旧的 kind=writing；其余按文件名（可省略 .md）。"""
    kind = str(kind or "").strip()
    if kind in STYLE_FILES:
        return STYLE_FILES[kind]
    name = kind if kind.endswith(DOC_SUFFIX) else f"{kind}{DOC_SUFFIX}"
    if not DOC_NAME_RE.match(name):
        raise ApiError(f"未知风格文件：{kind}", "BAD_STYLE")
    return name


def _style_path(directory: Path, kind: str) -> Path:
    return directory / _filename_for(kind)


def _documents(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    docs = [path for path in directory.iterdir() if path.is_file() and path.suffix == DOC_SUFFIX and not path.name.startswith(".")]
    docs.sort(key=lambda path: (path.name != LEGACY_FILE, path.name.lower()))
    return docs


def _body(text: str) -> str:
    """去掉 YAML frontmatter，只保留真正的正文。"""
    lines = str(text or "").splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return "\n".join(lines[index + 1 :]).strip()
    return str(text or "").strip()


def _doc_title(text: str, filename: str) -> str:
    for line in _body(text).splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1)
    return filename[: -len(DOC_SUFFIX)] if filename.endswith(DOC_SUFFIX) else filename


def _kind_of(filename: str) -> str:
    return "writing" if filename == LEGACY_FILE else filename[: -len(DOC_SUFFIX)]


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
        if not _documents(self.root):
            self.seed()
        for source in _documents(self.root):
            target = directory / source.name
            if not target.exists():
                shutil.copyfile(source, target)
        return directory

    def list_session(self, slug: str) -> dict[str, Any]:
        directory = self.ensure_session(slug)
        files = []
        for path in _documents(directory):
            text = path.read_text("utf8")
            global_path = self.root / path.name
            global_text = global_path.read_text("utf8") if global_path.exists() else None
            files.append(
                {
                    "path": path.name,
                    "kind": _kind_of(path.name),
                    "label": _doc_title(text, path.name),
                    "title": _doc_title(text, path.name),
                    "filename": path.name,
                    "exists": True,
                    "bytes": len(text.encode("utf8")),
                    "changed": global_text is not None and text != global_text,
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
        if not _documents(self.root):
            self.seed()
        files = []
        for path in _documents(self.root):
            text = path.read_text("utf8")
            files.append(
                {
                    "kind": _kind_of(path.name),
                    "label": _doc_title(text, path.name),
                    "title": _doc_title(text, path.name),
                    "filename": path.name,
                    "exists": True,
                    "text": text,
                    "bytes": len(text.encode("utf8")),
                    "defaultText": DEFAULTS["writing"] if path.name == LEGACY_FILE else "",
                    "builtin": path.name == LEGACY_FILE,
                }
            )
        return {"files": files, "root": str(self.root), "maxChars": STYLE_MAX_CHARS}

    def read_all(self, directory: Path) -> str:
        """按文件顺序合并成一份 system prompt 用的文笔说明。"""
        parts = []
        for path in _documents(directory):
            text = _body(path.read_text("utf8"))
            if text:
                parts.append(text)
        return "\n\n---\n\n".join(parts)

    def _next_doc_name(self) -> str:
        for number in range(1, 1000):
            name = f"rule-{number:02d}{DOC_SUFFIX}"
            if not (self.root / name).exists():
                return name
        raise ApiError("规则文件太多了", "BAD_STYLE")

    def create_global(self, title: str) -> dict[str, Any]:
        title = re.sub(r"\s+", " ", str(title or "")).strip() or "新规则"
        title = title[:60]
        filename = self._next_doc_name()
        text = f"# {title}\n\n"
        write_text(self.root / filename, text)
        for directory in self._session_dirs():
            write_text(directory / filename, text)
        return {"filename": filename, "title": title, "text": text}

    def delete_global(self, kind: str) -> None:
        filename = _filename_for(kind)
        path = self.root / filename
        if not path.exists():
            raise NotFound(f"找不到 {filename}", "NO_FILE")
        if len(_documents(self.root)) <= 1:
            raise ApiError("至少保留一个风格文件", "LAST_STYLE")
        path.unlink()
        for directory in self._session_dirs():
            (directory / filename).unlink(missing_ok=True)

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
        for local in _documents(directory):
            global_file = self.root / local.name
            if global_file.exists() and local.read_text("utf8") != global_file.read_text("utf8"):
                changed.append(_kind_of(local.name))
        return changed

    def sessions(self) -> list[dict[str, Any]]:
        return [{"slug": directory.name, "changed": self.changed_ids(directory.name)} for directory in self._session_dirs()]

    @staticmethod
    def _validate(kind: str, text: Any) -> str:
        _filename_for(kind)
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
        filename = _filename_for(kind)
        target = self.root / filename
        previous = target.read_text("utf8") if target.exists() else None
        write_text(target, value)
        synced = 0
        skipped: list[str] = []
        if sync:
            for directory in self._session_dirs():
                copy = directory / filename
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
        """把某份全局风格覆盖到文章副本；slug 为空时覆盖全部。"""
        filename = _filename_for(kind)
        source = self.root / filename
        if not source.exists():
            if filename != LEGACY_FILE:
                raise NotFound(f"找不到 {filename}", "NO_FILE")
            self.seed()
        value = source.read_text("utf8")
        targets = [self.session_dir(slug)] if slug else self._session_dirs()
        synced = 0
        skipped: list[str] = []
        for directory in targets:
            copy = directory / filename
            if not overwrite_custom and copy.exists() and copy.read_text("utf8") != value:
                skipped.append(directory.name)
                continue
            write_text(copy, value)
            synced += 1
        return {"synced": synced, "skipped": skipped}

    def reset_global(self, kind: str, *, sync: bool = True) -> dict[str, Any]:
        filename = _filename_for(kind)
        if filename != LEGACY_FILE:
            raise ApiError("自定义规则没有默认内容", "NO_DEFAULT")
        return self.write_global("writing", DEFAULTS["writing"], sync=sync, overwrite_custom=True)
