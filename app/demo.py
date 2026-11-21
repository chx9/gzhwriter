"""离线演示引擎：没有 API Key 时把整条流水线跑完。

内容只用于验证流程与排版，不代表真实模型质量。UI 会明确标识 demo 模式。
"""

from __future__ import annotations

import re
from typing import Any

from .materials import load_content
from .storage import Project, char_count, truncate


def _sources(project: Project) -> list[dict[str, Any]]:
    return [source for source in project.meta.get("sources", []) if not source.get("excluded")]


def _anchor(project: Project, index: int = 0) -> str:
    sources = _sources(project)
    if not sources:
        return "[观点]"
    source = sources[index % len(sources)]
    paragraphs = load_content(project, source, "content") or []
    number = min(len(paragraphs) or 1, 1 + index % max(1, len(paragraphs)))
    return f"[{source.get('id')}¶{number}]"


def _first_paragraphs(project: Project, limit: int = 3) -> list[str]:
    out: list[str] = []
    for source in _sources(project):
        for paragraph in (load_content(project, source, "content") or [])[:limit]:
            text = re.sub(r"\s+", " ", paragraph["text"]).strip()
            if 20 <= len(text) <= 220:
                out.append(text)
    return out


class DemoWriter:
    def brief(self, project: Project, *, topic: str, goal: str, audience: str, target_chars: int) -> str:
        sources = _sources(project)
        source_lines = "\n".join(
            f"- {source.get('id')}「{source.get('title')}」：{source.get('paragraphs')} 段，可提供事实与背景。"
            for source in sources
        ) or "- （暂无素材）"
        title = project.meta.get("title") or topic or "这篇选题"
        return f"""# Brief

- 选题：{title}
- 核心判断（暂定）：真正值得写的不是“{truncate(title, 18)}”本身，而是它背后那条常被忽略的条件链；先把条件说清楚，再给判断。
- 目标读者：{audience or '关心这个话题、愿意读长文的普通读者'}
- 篇幅：约 {target_chars} 字
- 语气：理性、克制、像做过功课的朋友；不制造焦虑，不喊口号。
- 素材：
{source_lines}

## 已知需要处理的问题
1. 不同素材的数据口径可能不一致，正文只保留能对上的数字，其余写成“约”“目前看”。
2. 素材没有覆盖的部分，不编内容；写成作者判断并标 `[观点]`。
3. 演示模式：以上内容由离线引擎生成，只用于验证流程。"""

    def clean(self, project: Project, source: dict[str, Any], content: str) -> str:
        # 演示模式不重写口语稿，只原样保留锚点，确保下游可用。
        return content

    def digest(self, project: Project, source: dict[str, Any]) -> str:
        paragraphs = load_content(project, source, "content") or []
        sid = source.get("id")
        points = []
        for paragraph in paragraphs[:4]:
            text = truncate(re.sub(r"\s+", " ", paragraph["text"]).strip(), 80)
            points.append(f"- {text} [{sid}¶{paragraph['n']}]")
        numbers = []
        for paragraph in paragraphs:
            for match in re.finditer(r"\d+(?:[.,]\d+)?\s*(?:%|％|倍|万|亿|元|年|个月)?", paragraph["text"]):
                numbers.append(f"- {match.group(0)} [{sid}¶{paragraph['n']}]")
                if len(numbers) >= 4:
                    break
            if len(numbers) >= 4:
                break
        return f"""# {sid} 摘要

> {truncate(source.get('title', ''), 40)}：演示模式摘要，用于验证素材锚点与流程。

## 核心观点
{chr(10).join(points) or '- （演示模式：素材段落较少）'}

## 数据与论据
{chr(10).join(numbers) or '- （没有抽取到数字）'}

## 当事人原话
- （演示模式不抽取引语，避免与原文不一致）

## 适合用在哪里
- 适合作为背景、事实依据和开头场景；不直接引用素材作者的说法。"""

    def outline(self, project: Project, *, brief: str, feedback: str = "") -> str:
        title = project.meta.get("title") or "这篇选题"
        anchors = [_anchor(project, index) for index in range(4)]
        return f"""# 大纲：{truncate(title, 30)}

## 01 / 默认答案依赖什么
先写大家习惯的做法，再指出它背后依赖的前提条件。用一个素材里的数字或事实做对照。{anchors[0]}

## 02 / 条件变了，判断也要变
说明变化来自哪里：收益、选择成本、时间跨度。先讲事实，再给判断，不把推测写成定论。{anchors[1]}

## 03 / 按用途把选择拆开
按时间、用途和风险承受能力分层，每一层给一个判断标准。用表格呈现可比较的数字。{anchors[2]}

## 04 / 什么情况下仍然适合原来的做法
承认边界，避免把结论说成绝对。最后回到开头的问题，给一个可以带走的判断。{anchors[3]}
"""

    def draft(self, project: Project, *, brief: str, outline: str, feedback: str = "", existing: str = "") -> str:
        title = project.meta.get("title") or "这篇选题"
        topic = project.meta.get("topic") or title
        anchor0 = _anchor(project, 0)
        anchor1 = _anchor(project, 1)
        anchor2 = _anchor(project, 2)
        anchor3 = _anchor(project, 3)
        short = truncate(title, 26)
        return f"""---
title: {short}
digest: 演示模式生成的文章摘要：先拆开默认假设，再看条件变化，最后给出按用途分层的判断。
author:
---

很多人对{topic}有一个默认答案：照着过去的经验做就不会错。但这个答案依赖一个很少被说出口的前提——条件没有变。{anchor0} 现在要问的是：这个前提还成立吗？

先把问题拆开。过去有效的做法，往往建立在三个条件上：收益足够高、选择足够简单、变化足够慢。只要其中一个条件变了，原来的答案就可能变成惯性。[观点]

## 01 / 默认答案依赖什么

先看一个具体的对照。同样的选择，在不同时间、不同用途下，结果可能完全不同。{anchor1} 这不是说原来的做法错了，而是说它只在一组条件下成立。

> 真正要问的不是“哪个更好”，而是“这笔钱、这件事，什么时候要用”。[观点]

如果只记住结论，不记住条件，经验就会变成负担。[观点]

## 02 / 条件变了，判断也要变

变化通常不是一夜发生的。它先出现在边缘：一些原本稳定的数字开始波动，一些原本简单的选择开始需要解释。{anchor2}

> [!TIP] 一个简单的检查
> 做决定前，先写下三个条件：时间、用途、最坏情况。三个都清楚，再谈“划不划算”。

这也是为什么，直接照搬别人的方案往往效果不好。别人的条件和你不一样，答案自然不一样。[观点]

| 判断维度 | 要问的问题 | 容易犯的错 |
|---|---|---|
| 时间 | 多久之后要用？ | 把短期要用的钱放进长期方案 |
| 用途 | 这笔钱承担什么任务？ | 所有钱用同一个标准 |
| 风险 | 最坏情况能不能承受？ | 只看收益，不看流动性 |

## 03 / 把选择拆成三层

更稳妥的做法，是先分层，再选工具。应急层是随时可能用到的钱，首要目标是安全和随时可用；中期层是 1–3 年内有明确用途的钱，可以在安全与收益之间做平衡；长期层是长期不用的钱，才适合讨论更长期限的安排。[观点] {anchor3}

![三层安排的示意图：先按用途分层，再决定期限和工具](ww-img:pending "google: three layer allocation diagram | 三层 配置 示意图")

分层不是让事情变复杂，而是让每个决定只回答一个问题。[观点]

## 04 / 哪些情况仍然适合原来的做法

如果三个条件都成立——时间足够长、用途明确、最坏情况能承受——原来的做法仍然有效。边界清楚，结论才有用。[观点]

![一个具体场景的照片，让抽象判断落到真实生活里](ww-img:pending "google: personal finance planning desk photo | 个人 理财 规划 桌面 照片")

回到开头的问题：默认答案不是不能信，而是要连同它的条件一起信。先问“什么时候要用”，再问“哪个更划算”，顺序反了，答案就容易错。[观点]

> 好的判断不是记住更多结论，而是知道每个结论在什么条件下成立。[观点]
"""

    def title(self, project: Project, *, article: str, brief: str) -> dict[str, Any]:
        topic = project.meta.get("topic") or project.meta.get("title") or "这个话题"
        short = truncate(topic, 18)
        return {
            "titles": [
                {"title": f"{short}：先别急着照旧做", "style": "结论型", "reason": "直接给出反常识结论。"},
                {"title": f"3 个条件，决定{short}还值不值得", "style": "数字型", "reason": "用数字降低理解成本。"},
                {"title": f"为什么{short}的默认答案开始失效", "style": "悬念/反常识型", "reason": "制造认知冲突。"},
                {"title": f"{short}，到底应该先看什么？", "style": "对话型", "reason": "模拟读者会问的问题。"},
                {"title": f"一个普通家庭的{short}决定", "style": "故事型", "reason": "从具体场景切入。"},
            ],
            "digest": f"演示模式摘要：拆开“{short}”背后的默认假设，用时间、用途、风险三个条件重新做判断。",
            "recommended": 1,
            "reason": "结论直接、字数短，适合信息流；同时没有夸张词。",
        }

    def edit(self, project: Project, *, article: str, instruction: str, selected: str = "") -> str:
        if selected and selected in article:
            replacement = selected + "（演示模式：已按意见调整，请接入真实模型后重写）"
            return article.replace(selected, replacement, 1)
        return article.rstrip() + f"\n\n<!-- 演示模式：收到改稿意见「{truncate(instruction, 60)}」，接入真实模型后会改写正文。 -->\n"
