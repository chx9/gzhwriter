# 主题资产

- `wechat.css`、`business.css`、`swiss.css`、`manfu.css`：从 DSH `gzh-writer/lib/theme-css.js` 的 `BASE_CSS` / `BUILTIN_THEMES` 移植。
- `gelv.css`、`jiaotang.css`、`moyun.css`、`qingkong.css`、`shiping.css`、`shiguang.css`、`sujian.css`：原插件 README 中的 7 套 Moxia 移植主题；canonical `theme-css.js` 未内联它们，这里作为数据资产 vendor 进来。
- `moxia-base.css`：Moxia 主题共享基样式，不是可选主题。
- `base.css`：非 Moxia 主题共享的结构骨架。

运行时会把它们种到 `workspace/styles/themes/`；之后主题库以 `workspace/styles/themes/` 为准。新项目不会读取 `~/.dsh/wechat-writer/`。
