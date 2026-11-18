"""所有 prompt 集中在这里，便于调整。"""

from __future__ import annotations

from typing import Any

from .storage import char_count


def system_prompt(*, writing_style: str, layout_style: str, theme_name: str, theme_guide: str, original_mode: bool = True, max_rounds: int = 3) -> str:
    original = (
        """
## 原创模式（默认开启）
- 成稿里不能出现任何“参考了材料”的痕迹：不写“素材显示/视频里说/博主认为/原作者指出/本文基于……整理/据资料/有分析认为”。
- 文末不放参考来源、不加脚注（citationMode=strip）。
- article.md 里仍保留内部溯源标记：[A01¶3]、[A01¶3-5]、[S01@12:30]、[观点]。这些标记只在 article.md 里；排版时会全部剥掉。
- 直接引语必须和素材原文一致；其余用自己的话写，不照搬素材句子和段落顺序。
"""
        if original_mode
        else ""
    )
    return f"""# 公众号写作工作流

你在帮用户以他上传的素材为研究资料，写一篇原创的微信公众号文章。素材是功课：事实和数据从素材来；角度、结构、论证和每一句话由你写。素材之外的内容只能是作者判断（[观点]），或用户明确补充的信息。
{original}
## 配图
- 你不搜索、不下载、不生成图片。只在正文单独一行写占位符：
  `![图注](ww-img:pending "google: English keywords | 中文关键词")`
- 图注是读者看到的说明，写清图里是什么，不写“图片来源/来自视频截图”。
- 关键词：先写 2–5 个词的英文短语，再用 `|` 给中文备选；要具体到能搜到。
- 约每 800–1000 字一张，全文 3–5 张；放在完整论述之后，不贴小节标题，不连续两张。
- 用户已补的图 `![图注](images/fig-01.jpg)` 不要改动或删除。

## 素材锚点
- 书面素材 A01、A02…；口语/字幕 S01、S02…。
- 每段一个锚点：书面 `[¶3]`，口语 `[¶3 @12:30]`。
- 正文每个事实、数据、引语后带内部标记：`[A01¶3]`、`[A01¶3-5]`、`[S01@12:30]`；多个出处写成 `[A01¶3；S02@05:10]`。
- 作者自己的判断标 `[观点]`。标记只供核对，排版时自动去掉，正文文字本身不提素材。
- 素材空白：不编内容。能写成作者观点就标 [观点]；不能就保留为空白，在大纲/评审里明确列出。
- 原始素材不改；口语稿整理只写 clean.md，不覆盖 content.md。

## 流程纪律
- 初稿完成必须停下来等用户过目；用户提的意见要逐条落实后再交付。
- 每步产物落盘；每步结束用一两句话汇报做了什么、产出哪个文件、下一步是什么。
- 用户驳回过的意见不要再提；用户锁定段落一字不改。
- 省上下文：先读 digest 和大纲锚点，只读当前需要的素材片段。

## 当前排版主题：{theme_name}
{theme_guide}

## 文笔风格
{writing_style}

## 排版补充说明
{layout_style}
"""


def brief_prompt(*, title: str, topic: str, goal: str, audience: str, target_chars: int, sources: str, feedback: str = "") -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""为下面这篇文章写选题简报（brief.md），供用户确认。

标题/选题：{title or topic}
用户目标：{goal or '（未填写）'}
目标读者：{audience or '（未填写）'}
目标篇幅：约 {target_chars} 字

素材索引与摘要：
{sources or '（还没有素材摘要；请先根据素材索引判断缺口）'}

请输出 Markdown，结构：
# Brief
- 选题：
- 核心判断（暂定）：
- 目标读者：
- 篇幅：
- 语气：
- 素材：每份素材一句话，说明它能支撑什么
- 已知需要处理的问题：数据口径、冲突、空白

要求：核心判断必须是一个可被反驳的观点，不要写成“介绍某某”；不要编造素材里没有的事实。
{f'用户修改意见：{feedback}' if feedback else ''}""",
        }
    ]


def clean_prompt(source: dict[str, Any], content: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""把这份口语稿整理成书面稿 clean.md。

素材：{source.get('id')} {source.get('title')}
要求：
- 格式必须与 content.md 相同：标题行 + 每段一行 `[¶n @mm:ss] 文本`；段号和时间锚点原样保留、一一对应，不合并、不拆分、不重新编号。
- 只做书面化：去口头语和重复；修正明显的识别错误；补标点。
- 只改表达，不增删观点。拿不准的识别错误保留原样，在文末用 `<!-- 存疑：… -->` 列出。

原文：
{content}""",
        }
    ]


def digest_prompt(source: dict[str, Any], content: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""为素材 {source.get('id')}「{source.get('title')}」写 digest.md。

结构：
# {source.get('id')} 摘要

> 一句话摘要（这一行会进入素材目录）

## 核心观点
- 观点…… [A01¶3-5]

## 数据与论据
- 2023 年留存率 42% [A01¶7]

## 当事人原话（只收有公共记录的讲话、判决、采访；不收素材作者自己的话）
- “原话一字不差” [A01¶12]

## 适合用在哪里
- 适合做开头的故事 / 适合支撑某个论点……

规则：每一条都带锚点；原话逐字复制；数据保留原始单位和口径。digest 是研究笔记，不是成稿。
原文：
{content}""",
        }
    ]


def compare_prompt(digests: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""把下面所有素材 digest 做跨素材对比，输出 sources/compare.md。

必须包含：
- 共识：几份素材都支持的观点（列锚点）
- 冲突：说法不一致的地方（列双方锚点）
- 空白：brief 里想写、但素材里没有支撑的内容
- 主素材建议：哪些素材信息量最大

素材 digest：
{digests}""",
        }
    ]


def outline_prompt(*, brief: str, material_context: str, feedback: str = "") -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""根据 brief 和素材，写 outline.md。

要求：
- 角度必须是你自己的，不要沿用某份素材的标题、开头和章节顺序。
- 每个要点都标注素材锚点，精确到段落或时间段。
- 每节写字数预算，总和接近 brief 的篇幅。
- 配图计划：约每 800–1000 字一张、全文 3–5 张；写图要表达什么 + 搜图关键词（英文在前，中文在后，用 | 隔开）。
- 文末列出：未用到的素材及原因、冲突及处理、空白。

格式：
# 大纲：<暂定标题>
- 角度：
- 目标读者 / 篇幅：
- 开头钩子：

## 01 <小节标题>（约 600 字）
- 要点：…… [A01¶3-5]
- 金句候选：“……” [S01@13:05]
- 配图：…… —— google: English keywords | 中文关键词

brief：
{brief}

素材上下文：
{material_context}
{f'用户修改意见：{feedback}' if feedback else ''}""",
        }
    ]


def draft_prompt(*, brief: str, outline: str, snippets: str, feedback: str = "", existing: str = "") -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""按确认的大纲写 article.md。

要求：
- frontmatter 先写 title（暂定）、digest（可留空）、author（可留空）。
- 小节用 `## 01 / 标题`，不要用一级标题。
- 事实、数据、引语后带内部锚点；作者判断标 `[观点]`。
- 原创模式：正文不出现“素材/视频/博主/原作者/本文整理自”；不照搬素材句子。
- 按大纲的配图计划放 `![图注](ww-img:pending "google: …")` 占位符。
- 按当前主题的 markdown 组件用量写：引用块、提示框、数据卡片、表格、强调。
- 目标篇幅按 brief 里的字数写；写完整篇文章，不要只写提纲。
- 直接输出 Markdown 全文：不要用 ``` 围栏包住，不要写“已按大纲写完”“约 N 字”之类的说明文字。

大纲：
{outline}

brief：
{brief}

可用素材片段：
{snippets or '（按大纲锚点读取；如果为空，请只写作者判断并标 [观点]）'}

{f'已有正文（请在此基础上修改）：{existing}' if existing else ''}
{f'用户修改意见：{feedback}' if feedback else ''}""",
        }
    ]


REVIEW_ROLES = [
    ("事实编辑", "论断与素材是否一致；曲解原意；把“可能”写成“一定”；冲突素材的处理是否公允。"),
    ("目标读者", "以 brief 里的读者身份阅读：哪里看不懂、想跳过、没被说服。"),
    ("结构编辑", "开头三行是否抓人；节与节的逻辑衔接；节奏；篇幅分配；结尾。"),
    ("文字编辑", "AI 腔、套话、长句、重复用词、啰嗦。"),
    ("合规审查", "广告法极限词、敏感表达、直接引用比例与版权、未经证实的负面表述。"),
    ("原创审查", "正文是否出现交代素材来源的话；是否有照搬素材的句子或行文顺序。"),
    ("配图审查", "占位符数量与位置；图注是否说清图里是什么；搜关键词是否具体。只评占位符。"),
    ("风格审查", "是否符合文笔风格和当前排版主题；引用块、提示框、卡片、强调的用量。"),
]


def review_prompt(*, article: str, brief: str, role: str, focus: str, style: str, theme: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""你是「{role}」。请评审下面这篇文章，只输出问题，不改文章。

关注点：{focus}

要求：
- 每条必须从 article.md 逐字复制原句。
- 级别只能是：必改 / 建议 / 可选。
- 每条给：原句、级别、问题、建议改法、依据锚点（如果有）。
- 每个角色不超过 8 条；“必改”只用于事实错误、读者明显看不懂、合规风险、正文提到素材来源。
- 如果原句在文章里找不到，不要提。

文笔风格：
{style}

当前主题要求：
{theme}

brief：
{brief}

article.md：
{article}""",
        }
    ]


def merge_review_prompt(reviews: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""把下面多角色评审意见合并成一张 reviews.md 表格。

规则：
- 用原句在文章里搜一下，找不到的意见丢掉。
- 同一句话上的多条意见合并成一条，角色并列，级别取最严重的。
- 按 必改 → 建议 → 可选 排序，编号 R1-01、R1-02…
- 表格列：编号 | 级别 | 角色 | 原句 | 问题 / 建议 | 裁决 | 说明 | 改动。裁决列先空着。
- 表格前写一句概览：必改几条、建议几条、可选几条。

评审意见：
{reviews}""",
        }
    ]


def title_prompt(*, article: str, brief: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""为这篇定稿生成 5 个标题候选和 1 条摘要。

要求：
- 5 个候选风格明显不同：结论型、数字型、悬念/反常识型、对话型、故事型。
- 标题不超过 64 字，建议 30 字以内；不用广告法极限词；不做标题党；数字和引语必须与正文一致。
- 标题和摘要都是原创口吻：不提“视频/博主/整理/盘点 N 份资料”，不照搬素材标题。
- 摘要 ≤120 字，建议 50 字左右。

只输出 JSON：
{{
  "titles": [
    {{"title": "…", "style": "结论型", "reason": "…"}}
  ],
  "digest": "…",
  "recommended": 1,
  "reason": "推荐第 1 个的理由"
}}

brief：
{brief}

article.md：
{article}""",
        }
    ]


def edit_prompt(*, article: str, instruction: str, selected: str = "", file_label: str = "article.md") -> list[dict[str, str]]:
    target = f"只修改下面这段选中文字：\n\n{selected}" if selected else "修改整篇内容。"
    return [
        {
            "role": "user",
            "content": f"""按用户要求修改 {file_label}。{target}

要求：
- 保留 frontmatter（如有）、溯源标记（[A01¶3]、[观点]）、图片占位符和已补图片。
- 只改需要改的地方，不要重写无关部分。
- 原创模式：不要出现“素材/视频/博主/本文整理自”。
- 直接输出修改后的完整内容，不要解释。

用户要求：
{instruction}

当前 article.md：
{article}""",
        }
    ]
