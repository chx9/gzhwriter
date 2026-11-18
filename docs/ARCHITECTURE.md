# 架构说明（v2 · 文档为中心）

## 总览

```text
Browser (React + Vite)
   │  REST + SSE
   ▼
FastAPI (app/main.py)
   ├── GraphRunner          LangGraph + SqliteSaver，后台线程，一次 interrupt
   ├── WriterEngine         OpenAI-compatible / 演示引擎（brief·digest·outline·draft·title·edit·review）
   ├── Renderer             Markdown → 语义 HTML → 行内样式（不落盘实时预览 + dist 落盘）
   ├── Materials / Lint / SourceCheck / Images / Comments / Styles / Themes
   └── ProjectStore         workspace/wechat-projects/<slug>/…
```

## 图

```text
START → prepare → research → draft → draft_ready(◆interrupt) → titles → END
                        ▲                │
                        └──── revise ────┘
```

- **research**：简报 + 素材消化 + 大纲，连续执行；产物写 brief.md / digest.md / outline.md，但对用户不可见。
- **draft**：写 article.md（含 frontmatter 归一化）。
- **draft_ready**：全图唯一 interrupt。payload：`{kind: "draft", content: <article.md>}`。恢复：`{action: "approve"}` 进 titles；`{action: "revise", feedback}` 回 draft 重写。
- **titles**：生成 5 个标题候选 + 摘要 → title-options.json，stage=awaiting_title，图结束。
- 之后用户在候选条点选 → `POST /title`（普通 API）：写回 frontmatter、渲染 dist、lint、标记 done。

### 为什么只剩一次 interrupt

界面以文档为中心：用户在编辑器里直接改、划选让 AI 改（diff 确认）。审批 brief/大纲/评审是把 agent 的内部工序甩给用户。保留初稿这一次暂停，是因为初稿是 AI 全文创作与人工接管的分界线。

### 按需 API（不是图节点）

| API | 说明 |
|---|---|
| `POST /edit` | AI 改稿（instruction + 可选 selected）。返回 before/after；前端 diff 后用户接受才落盘。 |
| `POST /review` | 多角色评审（8 角色并行 → 合并），写 reviews.md，返回 markdown。 |
| `POST /preview` | 实时预览：传 markdown（或读文件）→ 整页预览 HTML，图片内联，**不写 dist**。 |

## 写作页数据流

```text
textarea ──(防抖 1.2s)──► PUT /file (article.md)
        └──(防抖 0.6s)──► POST /preview ──► iframe srcDoc 刷新
划选/全局输入 ──► POST /edit ──► diff 卡片 ──接受──► 写回 + 预览刷新
行号点击 ──► 行批注（.materials-comments.json）
（评审保留为后端 API：POST /review，界面暂未展示）
```

## 运行与恢复

- `POST /start|resume|continue` 只启动后台线程；每项目一把运行锁。
- `graph.stream(stream_mode="updates")` 发布 node/interrupt/done/error 事件；状态以 SQLite 为准，SSE 只是通知。
- 状态映射：`interrupt` → interrupted（初稿待过目）；图走完 → done（含 awaiting_title，等待选标题）；有 next 无 interrupt → resumable。
- 重启后从 checkpoint 恢复；删除项目清理目录 + checkpoint 表。
- stage 以 `project.workflow.stage` 为准（节点和 /title 都会写它），checkpoint values 为辅。

## 渲染管线（保留原实现）

```text
article.md → frontmatter → citation(strip) → H1 去重
  → 语义 HTML（.wx-* / #moxia 骨架）
  → 受限 CSS 引擎（选择器匹配 + var() 链 + 源码序覆盖）
  → 删 class、写 style → dist/article.html
  → 预览版：本地图片内联 data URL → dist/preview.html / /preview API
```

CSS 引擎为受限子集（标签/类/id/复合/后代/子代/逗号/:root/var()/:not/:nth-child），`@media` 与伪类跳过并 warning；同名属性按源码顺序覆盖（与 Moxia/原插件逐字一致，golden 在 `tests/golden/`）。

## 数据契约

关键文件（不变）：

- `project.json`：素材索引 + `workflow: {stage, status, ...}`。
- `article.md`：frontmatter（title/digest/author）+ 正文 + 溯源标记 `[A01¶3]` / `[观点]`。
- `title-options.json`：`{titles:[{title,style,reason}], digest, recommended, reason}`。
- `checks.json`：lint + sourceCheck 汇总。
- `.materials-comments.json`：行批注。
- `sources/<id>/`：content.md（锚点段落）、digest.md、clean.md、meta.json、original.*。

## 安全

- 所有项目路径 `safe_join` 防逃逸；编辑仅限白名单（article.md 等）。
- 上传校验扩展名/大小；图片按魔数校验，≤10MB。
- 批注 id/长度/行号服务端校验；API Key 只在服务端 `.env`。
- 前端 Markdown 走 DOMPurify；预览 iframe sandbox。

## 并发

- 同项目串行运行；填图/清图项目级锁 + 行号原文双校验。
- SQLite WAL；读写连接分离。
