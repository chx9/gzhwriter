"""首次启动的示例项目。"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .demo import DemoWriter
from .materials import import_materials
from .render_service import render_project
from .storage import ProjectStore, parse_frontmatter, set_frontmatter_value, write_text
from .styles import StyleStore
from .render.themes import ThemeLibrary


SAMPLE_SOURCES = [
    (
        "利率变化笔记",
        """# 利率变化笔记

[¶1] 过去很多家庭习惯把闲钱一次性存成三年定期，因为期限越长，利率通常越高。这个经验成立的前提，是长期限的利率溢价足够明显。

[¶2] 近两年，部分期限的利率出现了倒挂：五年期不一定比三年期高，个别银行的长期限产品甚至低于短期限。不同银行、不同产品的挂牌利率差别也很大。

[¶3] 对普通家庭来说，真正需要先回答的不是“哪家利率高”，而是“这笔钱什么时候要用”。应急金、一两年内有明确用途的钱、长期不用的钱，本来就不该用同一个期限。

[¶4] 提前支取通常按活期计息或按产品条款重算，具体规则要看合同。把短期要用的钱锁进长期限，遇到急用钱时，利息上的那点优势很容易被抹掉。

[¶5] 一个可操作的做法是分层：应急层保持流动，中期层承担明确计划，长期层再考虑更长期限的安排。比例没有标准答案，要看家庭现金流和风险承受能力。
""",
    ),
    (
        "家庭现金管理清单",
        """# 家庭现金管理清单

[¶1] 先盘点未来 12 个月确定会发生的支出：房租、房贷、学费、保险、医疗预留。把这些钱单独放，不和长期储蓄混在一起。

[¶2] 再看收入的稳定性。收入波动大的家庭，应急层要更厚；收入稳定、负债低的家庭，可以把更多钱放进中期层。

[¶3] 期限安排可以和支出日期对齐。比如明年要交的学费，就不适合放进三年期；三年内确定不用的钱，才适合讨论更长期限。

[¶4] 比较不同方案时，除了利率，还要看流动性、提前支取规则、是否保本、是否有额度或渠道限制。只看收益率容易忽略真正的约束。

[¶5] 最后留一个复查时间点。利率、收入和家庭计划都会变，分层不是做一次就结束，半年或一年检查一次就够了。
""",
    ),
]


def seed_sample(settings: Settings, store: ProjectStore, styles: StyleStore, themes: ThemeLibrary) -> str | None:
    if not settings.seed or store.list():
        return None
    project = store.create(
        title="低利率下，存款该怎么分层",
        slug="sample-deposit-ladder",
        topic="低利率环境下家庭存款的期限与用途分层",
        goal="给普通家庭一个可执行的存款分层方法",
        audience="手里有几万到几十万存款、习惯存定期的普通家庭",
        target_chars=2200,
        theme="business",
    )
    styles.ensure_session(project.slug)
    themes.set_session_theme(project.slug, "business")
    project.meta["sample"] = True
    project.meta["theme"] = "business"
    store.save(project)
    for title, text in SAMPLE_SOURCES:
        import_materials(store, project, [{"text": text, "title": title}])
    demo = DemoWriter()
    brief = demo.brief(
        project,
        topic=project.meta["topic"],
        goal=project.meta["goal"],
        audience=project.meta["audience"],
        target_chars=project.meta["targetChars"],
    )
    write_text(project.dir / "brief.md", brief + "\n")
    digests = []
    for source in project.meta.get("sources", []):
        digest = demo.digest(project, source)
        write_text(project.dir / str(source["dir"]) / "digest.md", digest + "\n")
        digests.append(digest)
    compare = demo.compare(project)
    write_text(project.dir / "sources/compare.md", compare + "\n")
    write_text(project.dir / "digest.md", ("# 素材消化\n\n" + "\n\n---\n\n".join(digests) + "\n\n---\n\n" + compare) + "\n")
    outline = demo.outline(project, brief=brief)
    write_text(project.dir / "outline.md", outline + "\n")
    article = demo.draft(project, brief=brief, outline=outline)
    options = demo.title(project, article=article, brief=brief)
    recommended = max(1, min(5, int(options.get("recommended") or 1))) - 1
    title = options["titles"][recommended]["title"]
    article = set_frontmatter_value(article, "title", title)
    article = set_frontmatter_value(article, "digest", options.get("digest", ""))
    write_text(project.dir / "article.md", article + "\n")
    review = demo.review(project, article=article)
    write_text(project.dir / "reviews.md", review + "\n")
    write_text(
        project.dir / "progress.md",
        f"""# 进度：{project.meta['title']}

## 当前阶段
示例项目：已完成排版，可直接看「发布」页

## 已确认
- 示例数据：brief / 大纲 / 正文 / 评审 / 标题均为离线演示引擎生成
- 标题：{title}

## 待办
- [ ] 补两张配图后重新排版
- [ ] 复制到公众号预览
""",
    )
    project.meta["workflow"] = {"stage": "done", "status": "done", "reviewRound": 1}
    store.save(project)
    try:
        render_project(project, settings=settings, styles=styles, themes=themes)
    except Exception:
        pass
    return project.slug
