"""受限 CSS 引擎：把主题样式算成行内 style，并删除 class。

公众号编辑器只认 style="..."，会丢掉 class 与 <style>。支持的选择器子集与原插件一致：
标签 / 类 / id / 复合 / 后代 / 子代 / 逗号分组 / :root 变量 / var() / :not / :nth-child。
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from typing import Any

VOID = {"img", "br", "hr", "input", "meta", "link"}


def split_top(text: str, sep: str) -> list[str]:
    out: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = ""
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            continue
        if depth == 0 and char == sep:
            out.append(text[start:index])
            start = index + 1
    out.append(text[start:])
    return out


def parse_decls(body: str) -> list[tuple[str, str]]:
    decls: list[tuple[str, str]] = []
    for chunk in split_top(body, ";"):
        raw = chunk.strip()
        if not raw or ":" not in raw:
            continue
        index = raw.find(":")
        prop = raw[:index].strip().lower()
        value = re.sub(r"\s*!important\s*$", "", raw[index + 1 :].strip(), flags=re.I)
        if not prop or not value:
            continue
        value = value.replace('"', "'")
        decls.append((prop, value))
    return decls


@dataclass
class Compound:
    tag: str = ""
    id: str = ""
    classes: list[str] = field(default_factory=list)
    pseudos: list[dict[str, Any]] = field(default_factory=list)
    spec: tuple[int, int, int] = (0, 0, 0)
    root: bool = False


def parse_compound(selector: str) -> Compound | None:
    sel = selector.strip()
    if not sel:
        return None
    if sel == ":root":
        return Compound(root=True)
    match = re.match(
        r"^([a-zA-Z][a-zA-Z0-9]*)?((?:[.#][A-Za-z_][\w-]*|:(?:not|nth-child|first-child|last-child|nth-of-type)\((?:[^()]*)\)|:(?:first-child|last-child))*)$",
        sel,
    )
    if not match:
        return None
    tag = match.group(1) or ""
    compound = Compound(tag=tag)
    for part in re.findall(r"[.#][A-Za-z_][\w-]*|:[a-z-]+(?:\([^()]*\))?", match.group(2) or ""):
        if part.startswith("."):
            compound.classes.append(part[1:])
        elif part.startswith("#"):
            if compound.id:
                return None
            compound.id = part[1:]
        else:
            pm = re.match(r"^:([a-z-]+)(?:\(([^()]*)\))?$", part)
            if not pm:
                return None
            name = pm.group(1)
            if name not in {"not", "nth-child", "nth-of-type", "first-child", "last-child"}:
                return None
            pseudo = {"name": name, "arg": (pm.group(2) or "").strip()}
            if name == "not":
                inner = parse_compound(pseudo["arg"])
                if inner is None or inner.root:
                    return None
                pseudo["compound"] = inner
            compound.pseudos.append(pseudo)
    if not tag and not compound.classes and not compound.id and not compound.pseudos:
        return None
    compound.spec = (len(compound.classes) + len(compound.pseudos), 1 if compound.id else 0, 1 if tag else 0)
    return compound


@dataclass
class Selector:
    root: bool
    parts: list[dict[str, Any]]
    spec: tuple[int, int, int] = (0, 0, 0)


def parse_selector(selector: str) -> Selector | None:
    sel = selector.strip()
    if not sel:
        return None
    if sel == ":root":
        return Selector(True, [], (0, 0, 0))
    if re.search(r"[+~\[]", sel):
        return None
    tokens: list[str] = []
    buffer = ""
    depth = 0
    for char in sel:
        if char == "(":
            depth += 1
        if char == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (char.isspace() or char == ">"):
            if char == ">":
                if buffer.strip():
                    tokens.append(buffer.strip())
                tokens.append(">")
                buffer = ""
                continue
            if buffer.strip():
                tokens.append(buffer.strip())
                buffer = ""
            continue
        buffer += char
    if buffer.strip():
        tokens.append(buffer.strip())
    parts: list[dict[str, Any]] = []
    pending: str | None = None
    for token in tokens:
        if token == ">":
            if not parts or pending == ">":
                return None
            pending = ">"
            continue
        compound = parse_compound(token)
        if compound is None or compound.root:
            return None
        parts.append({"comb": (pending or " ") if parts else None, "compound": compound})
        pending = None
    if not parts or pending:
        return None
    spec = [0, 0, 0]
    for part in parts:
        for i in range(3):
            spec[i] += part["compound"].spec[i]
    return Selector(False, parts, tuple(spec))


@dataclass
class Rule:
    root: bool
    parts: list[dict[str, Any]]
    decls: list[tuple[str, str]]
    order: int
    spec: tuple[int, int, int] = (0, 0, 0)


def parse_css(text: str) -> tuple[dict[str, str], list[Rule], list[str]]:
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    css = re.sub(r"/\*[\s\S]*?\*/", lambda m: " " * len(m.group(0)), source)
    variables: dict[str, str] = {}
    rules: list[Rule] = []
    warnings: list[str] = []
    index = 0
    while index < len(css):
        brace = css.find("{", index)
        if brace == -1:
            rest = css[index:].strip()
            if rest.startswith("@"):
                warnings.append(f"忽略不支持的命令：{rest[:40]}")
            break
        prelude = css[index:brace].strip()
        depth = 1
        cursor = brace + 1
        while cursor < len(css) and depth > 0:
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
            cursor += 1
        body = css[brace + 1 : max(brace + 1, cursor - 1)]
        index = cursor
        if not prelude:
            continue
        if prelude.startswith("@"):
            for at in split_top(prelude, ";") if ";" in prelude else [prelude]:
                name = at.strip().split(maxsplit=1)[0] if at.strip() else ""
                if name:
                    warnings.append(f"忽略 @ 规则：{name}")
            rest = split_top(prelude, ";")[-1].strip() if ";" in prelude else ""
            if not rest:
                continue
            prelude = rest
        decls = parse_decls(body)
        for part in split_top(prelude, ","):
            parsed = parse_selector(part)
            if parsed is None:
                warnings.append(f"忽略不支持的选择器：{part.strip()}")
                continue
            if parsed.root:
                for prop, value in decls:
                    if prop.startswith("--"):
                        variables[prop] = value
                continue
            if decls:
                rules.append(Rule(False, parsed.parts, decls, len(rules), parsed.spec))
    return variables, rules, warnings


# ── DOM ─────────────────────────────────────────────────────────────


@dataclass
class Node:
    tag: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text: str | None = None


def parse_html(html: str) -> Node:
    root = Node(tag="#root")
    stack = [root]
    tag_re = re.compile(r'<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s+[a-zA-Z_:][\w:-]*="[^"]*")*)\s*(\/?)>')
    last = 0
    for match in tag_re.finditer(html):
        if match.start() > last:
            text = html[last : match.start()]
            if text:
                stack[-1].children.append(Node(text=text))
        last = match.end()
        closing, tag, attr_text, self_close = match.groups()
        lower = tag.lower()
        if closing:
            for index in range(len(stack) - 1, 0, -1):
                if stack[index].tag == lower:
                    del stack[index:]
                    break
            continue
        attrs = {a.group(1).lower(): a.group(2) for a in re.finditer(r'([a-zA-Z_:][\w:-]*)="([^"]*)"', attr_text)}
        node = Node(tag=lower, attrs=attrs)
        stack[-1].children.append(node)
        if lower not in VOID and not self_close:
            stack.append(node)
    if last < len(html):
        text = html[last:]
        if text:
            root.children.append(Node(text=text))
    return root


def _attr_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def serialize(node: Node) -> str:
    if node.tag is None:
        return node.text or ""
    if node.tag == "#root":
        return "".join(serialize(child) for child in node.children)
    attrs = "".join(
        f' {key}="{_attr_escape(value)}"' for key, value in node.attrs.items() if key != "class"
    )
    head = f"<{node.tag}{attrs}>"
    if node.tag in VOID:
        return head
    return head + "".join(serialize(child) for child in node.children) + f"</{node.tag}>"


def _sibling_index(node: Node, parent: Node | None) -> tuple[int, int]:
    if parent is None:
        return 1, 1
    index = 0
    same_tag = 0
    for child in parent.children:
        if child.tag is None:
            continue
        index += 1
        if child.tag == node.tag:
            same_tag += 1
        if child is node:
            break
    return index or 1, same_tag or 1


def _nth_matches(arg: str, n: int) -> bool:
    text = re.sub(r"\s+", "", str(arg).lower())
    if text == "odd":
        return n % 2 == 1
    if text == "even":
        return n % 2 == 0
    match = re.match(r"^([+-]?\d*)n([+-]\d+)?$", text)
    if match:
        raw_a = match.group(1)
        a = 1 if raw_a in {"", "+"} else -1 if raw_a == "-" else int(raw_a)
        b = int(match.group(2)) if match.group(2) else 0
        if a == 0:
            return n == b
        return (n - b) / a >= 0 and (n - b) % a == 0
    try:
        return n == int(text)
    except ValueError:
        return False


def _compound_matches(compound: Compound, node: Node, parent: Node | None) -> bool:
    if compound.tag and compound.tag != node.tag:
        return False
    if compound.id and str(node.attrs.get("id", "")) != compound.id:
        return False
    if compound.classes:
        classes = set(str(node.attrs.get("class", "")).split())
        if not all(name in classes for name in compound.classes):
            return False
    for pseudo in compound.pseudos:
        name = pseudo["name"]
        if name == "not":
            if _compound_matches(pseudo["compound"], node, parent):
                return False
        elif name == "first-child":
            if _sibling_index(node, parent)[0] != 1:
                return False
        elif name == "last-child":
            last = next((child for child in reversed(parent.children) if child.tag is not None), None) if parent else None
            if last is not node:
                return False
        elif name == "nth-child":
            if not _nth_matches(pseudo["arg"], _sibling_index(node, parent)[0]):
                return False
        elif name == "nth-of-type":
            if not _nth_matches(pseudo["arg"], _sibling_index(node, parent)[1]):
                return False
    return True


def _matches(rule: Rule, node: Node, ancestors: list[Node]) -> bool:
    parts = rule.parts
    if not parts:
        return False

    def parent_of(index: int) -> Node | None:
        return ancestors[index] if 0 <= index < len(ancestors) else None

    if not _compound_matches(parts[-1]["compound"], node, parent_of(len(ancestors) - 1)):
        return False
    cursor = len(ancestors) - 1
    for index in range(len(parts) - 2, -1, -1):
        comb = parts[index + 1]["comb"]
        if comb == ">":
            if cursor < 0 or not _compound_matches(parts[index]["compound"], ancestors[cursor], parent_of(cursor - 1)):
                return False
            cursor -= 1
            continue
        while cursor >= 0 and not _compound_matches(parts[index]["compound"], ancestors[cursor], parent_of(cursor - 1)):
            cursor -= 1
        if cursor < 0:
            return False
        cursor -= 1
    return True


def _resolve_vars(value: str, variables: dict[str, str], depth: int = 0) -> str | None:
    if "var(" not in value:
        return value
    if depth > 8:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        name = match.group(1)
        fallback = match.group(2)
        found = variables.get(name, fallback.strip() if fallback else None)
        if found is None:
            missing = True
            return ""
        return found

    out = re.sub(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,\s*([^()]*))?\)", replace, value)
    if missing:
        return None
    return _resolve_vars(out, variables, depth + 1) if "var(" in out else out


def _parse_style_attr(value: str) -> dict[str, str]:
    return {prop: val for prop, val in parse_decls(value)}


def inline_css(html: str, css_text: str, overrides: dict[str, str] | None = None) -> tuple[str, list[str]]:
    sheet_vars, rules, warnings = parse_css(css_text)
    base_vars = dict(sheet_vars)
    for key, value in (overrides or {}).items():
        if value in (None, ""):
            continue
        base_vars[key if key.startswith("--") else f"--{key}"] = str(value)

    tree = parse_html(str(html))
    dropped_total = 0

    def walk(node: Node, ancestors: list[Node], inherited: dict[str, str]) -> None:
        nonlocal dropped_total
        if node.tag is None:
            return
        if node.tag == "#root":
            for child in node.children:
                walk(child, [], inherited)
            return
        best: dict[str, str] = {}
        local_vars = dict(inherited)
        dropped = 0
        for rule in rules:
            if not _matches(rule, node, ancestors):
                continue
            for prop, raw_value in rule.decls:
                if prop.startswith("--"):
                    local_vars[prop] = _resolve_vars(raw_value, local_vars) or raw_value
                    continue
                value = _resolve_vars(raw_value, local_vars)
                if value is None or value == "":
                    dropped += 1
                    continue
                best[prop] = value
        if dropped:
            dropped_total += dropped
        style_parts = [f"{prop}:{value};" for prop, value in best.items()]
        for prop, value in _parse_style_attr(node.attrs.get("style", "")).items():
            style_parts.append(f"{prop}:{value};")
        if style_parts:
            node.attrs["style"] = "".join(style_parts)
        else:
            node.attrs.pop("style", None)
        for child in node.children:
            walk(child, ancestors + [node], local_vars)

    walk(tree, [], base_vars)
    if dropped_total:
        warnings.append(f"有 {dropped_total} 条声明引用了未定义的变量")
    return serialize(tree), sorted(set(warnings))
