# 移植说明

新项目是独立应用，不依赖 DSH runtime、cordis、`ctx`、`connection`、`sessions`，也不读写 `~/.dsh` 或 `dsh/wechat-projects/`。原插件只作为行为契约（素材锚点、原创模式、渲染器、图位机制）和渲染正确性参照。

## v1 → v2：推翻的是什么

v1 把原插件的 agent 工序原样搬成了 UI：四道确认点（简报/大纲/评审/标题）、7 阶段 stepper、文件树式「资料」面板。v2 认识到这些是 **AI 的内部脚手架，不是用户界面**，于是：

| v1（插件思维） | v2（文档为中心） |
|---|---|
| 四道 interrupt 确认点 | 全图只停一次：初稿完成 |
| 简报/消化/大纲单独展示确认 | research 合并为一个后台节点，产物落盘但不对用户展示 |
| 评审是一个流水线阶段 | `POST /review` 按需触发，结果当建议看 |
| 改稿 = 把 Markdown 贴进 textarea 等重写 | `POST /edit` 返回 before/after，前端 diff 卡片接受/拒绝 |
| 标题是一个确认页 | 候选条内联在编辑器上方，点选即写回（`POST /title`） |
| 成稿/文章资料/配图/风格四个子 tab | 顶部只有 写作 / 素材 / 发布 |
| 文件树导航 | 素材卡片列表；内部文件不再对用户展示 |

**保留不动的核心资产**：渲染引擎（Markdown→语义 HTML→行内样式）、11 套主题、素材锚点/原创检查体系、图位占位机制、SQLite 持久化、demo 模式、素材导入管线（md/txt/html/srt/vtt/docx/url）。golden 渲染对比依然 11/11 一致。

## 保留的契约细节

### 素材与锚点
- 书面 `A01`、口语/字幕 `S01`；每段 `[¶n]` / `[¶n @mm:ss]`。
- SRT/VTT 解析、cue 合并；HTML 取 `#js_content`/article/body；重复 hash 跳过；乱码/截断/无时间戳 warning。
- `A01¶3`、`A01¶3-5`、`S01@12:30`、`S01@12:30-15:10` 读取；单次约 12000 字上限。

### 原创模式
- 默认 citationMode=strip：成稿不提素材/视频/博主，文末无参考来源。
- `[A01¶3]`/`[观点]` 只在 article.md；渲染全部剥掉；H1 与 frontmatter title 相同则去掉。
- lint：`source-mention`（error）、`verbatim-copy`（warning，≥24 字）、极限词、标题/摘要长度、待补图。

### 渲染器
- 两步：语义 HTML（`.wx-*`，Moxia 骨架兼容）→ 受限 CSS 引擎算行内样式、删 class。
- `:root` 变量、`var()` 链、`:not`、`:nth-child`、后代/子代/逗号分组；同名属性按源码顺序覆盖。
- 11 套主题：4 套来自 `lib/theme-css.js`，7 套 Moxia 主题作为 CSS 资产 vendor（`moxia-base.css` 是基样式不是主题）。
- `dist/article.html` 无 `<style>`/`class=`/`<script>`；预览图片内联 data URL。

### 图位
- 占位符 `![图注](ww-img:pending "google: en | zh")`；待补渲染为虚线框，绝不生成 `<img>`。
- 行号 + 原文双校验；图片魔数校验 ≤10MB；填充/清除后自动重排。

## 有意偏离（v2 新增）

1. **评审多角色结果**直接展示为 markdown 建议清单，不做逐条采纳/驳回的裁决 UI。
2. **正文批注**替代原评论系统：在渲染稿里选中文字加批注，批注可一键全部交给 AI 改；预览只读。
3. **评审与改稿**与流水线解耦：评审 API 保留，界面改稿以 chatbox / 批注 → diff 确认为主。
4. **/preview 不落盘**：写作页实时预览走内存渲染，只有「重新排版」/定稿/补图才写 dist。
5. **stage 以 project.workflow 为准**：图外的用户操作（选标题）能立刻反映到状态。

## 仍没有移植的

- DSH 工具协议（material_import 等）→ 由图节点和 API 直接承担。
- `.sessions.json` 会话绑定 → 一个项目 = 一篇文章 = 一份风格副本。
- 公众号草稿推送、图片搜索/下载/生成、PDF/音视频解析、多用户/协作/云端。
- 原面板的「成稿选中文字浮层评论」→ 改为写作页划选「AI 改这段」+ 编辑器行批注。
