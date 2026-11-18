import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { navigate } from '../hooks'
import type { Comment, FileEntry, ProjectDetail, ProjectSummary, RunnerState } from '../types'
import { Badge, Icon, Markdown, Spinner, formatTime, useToast } from '../ui'
import { diffBlocks, type DiffBlock } from '../diff'
import { applyCommentMarks, hitTest } from '../commentMarks'
import { Explorer } from './Explorer'

const NODE_LABELS: Record<string, string> = {
  prepare: '读取素材',
  research: '消化素材、拟大纲',
  draft: '撰写初稿',
  draft_ready: '等待过目',
  titles: '生成标题候选',
}

interface EditProposal {
  instruction: string
  selected: string
  file: string
  before: string
  after: string
  blocks: DiffBlock[]
}

export function WriteView({
  slug,
  summary,
  state,
  detail,
  onRefresh,
  onSaved,
}: {
  slug: string
  summary: ProjectSummary
  state: RunnerState | null
  detail: ProjectDetail | null
  onRefresh: () => void
  onSaved: () => void
}) {
  const { notify } = useToast()
  const [text, setText] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<{ html: string; title: string; minutes: number; at: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [instruction, setInstruction] = useState('')
  const [selection, setSelection] = useState('') // 编辑器或预览里的选区
  const [proposal, setProposal] = useState<EditProposal | null>(null)
  const [comments, setComments] = useState<Comment[]>([])
  const [showComments, setShowComments] = useState(false)
  const [commentDraft, setCommentDraft] = useState('')
  const [commenting, setCommenting] = useState(false)
  const [selPopup, setSelPopup] = useState<{ top: number; left: number } | null>(null)
  const [aiScoped, setAiScoped] = useState(false)
  const [pendingCommentIds, setPendingCommentIds] = useState<string[]>([])
  const [activeComment, setActiveComment] = useState<{ id: string; top: number; left: number; above: boolean } | null>(null)
  const selRange = useRef<Range | null>(null)
  const chatInputRef = useRef<HTMLInputElement | null>(null)
  const [sourcesOpen, setSourcesOpen] = useState(true)
  const [openFile, setOpenFile] = useState<FileEntry | null>(null)
  const [viewMode, setViewMode] = useState<'rendered' | 'source'>('rendered')
  const [fileContent, setFileContent] = useState<any>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [fileRaw, setFileRaw] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const gutterRef = useRef<HTMLDivElement>(null)
  const previewRef = useRef<HTMLDivElement | null>(null)
  const readerRef = useRef<HTMLDivElement | null>(null)
  const saveTimer = useRef<number>()
  const previewTimer = useRef<number>()

  const values = state?.values || {}
  const interrupt = state?.interrupt
  const running = state?.status === 'running'
  const stage = state?.stage || 'materials'
  const awaitingTitle = stage === 'awaiting_title'
  const done = stage === 'done'
  const openComments = comments.filter((comment) => comment.status === 'open')

  // ── 载入文章 ──────────────────────────────────────────────
  useEffect(() => {
    setLoaded(false)
    api
      .get<{ text: string }>(`/api/projects/${slug}/file?path=article.md`)
      .then((data) => {
        setText(data.text || '')
        setDirty(false)
        setLoaded(true)
      })
      .catch(() => {
        setText('')
        setLoaded(true)
      })
  }, [slug])

  // 初稿注入
  useEffect(() => {
    if (interrupt?.kind === 'draft' && interrupt.content) {
      setText(interrupt.content)
      setDirty(false)
      setProposal(null)
      refreshPreview(interrupt.content)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interrupt?.kind, interrupt?.content])

  // ── 保存 + 实时预览 ───────────────────────────────────────
  const persist = useCallback(
    async (content: string) => {
      setSaving(true)
      try {
        await api.put(`/api/projects/${slug}/file`, { path: 'article.md', text: content })
        setDirty(false)
        onSaved()
      } catch (error: any) {
        notify(error.message, 'error')
      } finally {
        setSaving(false)
      }
    },
    [slug, notify, onSaved],
  )

  const refreshPreview = useCallback(
    (content: string) => {
      window.clearTimeout(previewTimer.current)
      previewTimer.current = window.setTimeout(() => {
        api
          .post<{ html: string; title: string; minutes: number; renderedAt: string }>(`/api/projects/${slug}/preview`, { markdown: content })
          .then((data) => setPreview({ html: data.html, title: data.title, minutes: data.minutes, at: data.renderedAt }))
          .catch(() => {})
      }, 600)
    },
    [slug],
  )

  const handleChange = (value: string) => {
    setText(value)
    setDirty(true)
    refreshPreview(value)
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => persist(value), 1200)
  }

  useEffect(() => {
    if (loaded && text) refreshPreview(text)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded])

  // 把已批注的原文用荧光笔标出来；评论/渲染稿变化时重绘
  useEffect(() => {
    if (viewMode !== 'rendered' || openFile) return
    const root = readerRef.current?.querySelector('.article-reader-inner') as HTMLElement | null
    if (!root) return
    applyCommentMarks(root, comments.map((comment) => ({ id: comment.id, quote: comment.quote, status: comment.status })))
  }, [preview, comments, viewMode, openFile])

  // ── 流水线 ────────────────────────────────────────────────
  const start = async () => {
    setBusy(true)
    try {
      await api.post(`/api/projects/${slug}/start`, {})
      onRefresh()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const resume = async (action: 'approve' | 'revise') => {
    const fb = feedback.trim()
    if (action === 'revise' && !fb) {
      notify('写一句修改意见，再点「按意见重写」', 'info')
      return
    }
    setBusy(true)
    try {
      await api.post(`/api/projects/${slug}/resume`, { action, feedback: fb })
      setFeedback('')
      onRefresh()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const pickTitle = async (title: string, digest: string) => {
    setBusy(true)
    try {
      const data = await api.post<{ article: string }>(`/api/projects/${slug}/title`, { title, digest })
      setText(data.article)
      setDirty(false)
      refreshPreview(data.article)
      notify('标题已写回，排版完成', 'ok')
      onRefresh()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  // ── AI 改稿（chatbox 发送） ─────────────────────────────────
  // chatbox 目标：正文始终是 article.md；只读素材（content.md / original.* / meta.json）改其 digest.md
  const editTarget = useMemo(() => {
    if (!openFile) return 'article.md'
    if (openFile.editable) return openFile.path
    const match = openFile.path.match(/^(sources\/[^/]+)\//)
    if (match) return `${match[1]}/digest.md`
    return openFile.path
  }, [openFile])
  const targetBlocked = Boolean(openFile && !openFile.editable && !openFile.path.includes('sources/'))

  const sendInstruction = async () => {
    if (!instruction.trim()) return
    if (editTarget === 'article.md' && !text.trim()) return
    setBusy(true)
    try {
      const data = await api.post<{ before: string; article: string; file: string }>(`/api/projects/${slug}/edit`, {
        instruction: instruction.trim(),
        selected: aiScoped && selection ? selection : undefined,
        file: editTarget,
      })
      setProposal({
        instruction: instruction.trim(),
        selected: aiScoped ? selection : '',
        file: data.file || editTarget,
        before: data.before,
        after: data.article,
        blocks: diffBlocks(data.before, data.article),
      })
      setInstruction('')
      setSelection('')
      setAiScoped(false)
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const acceptProposal = async () => {
    if (!proposal) return
    const next = proposal.after
    const target = proposal.file
    setProposal(null)
    if (target === 'article.md') {
      setText(next)
      await persist(next)
      refreshPreview(next)
      if (pendingCommentIds.length) {
        for (const id of pendingCommentIds) {
          try {
            await api.patch(`/api/projects/${slug}/comments/${id}`, { status: 'sent', sentAt: new Date().toISOString() })
          } catch {
            // 批注可能已被删除
          }
        }
        setPendingCommentIds([])
        loadComments()
        notify(`已按 ${pendingCommentIds.length} 条批注改稿`, 'ok')
      }
    } else {
      try {
        await api.put(`/api/projects/${slug}/file`, { path: target, text: next })
        notify(`${target.split('/').pop()} 已更新`, 'ok')
        onRefresh()
        onSaved()
        if (openFile && openFile.path !== target) {
          await openEntry({ path: target, name: target.split('/').pop() || target, dir: '', bytes: next.length, mtime: Date.now(), category: 'intake', kind: 'text', editable: true })
        } else {
          const data = await api.get<any>(`/api/projects/${slug}/file?path=${encodeURIComponent(target)}`)
          setFileContent(data)
        }
      } catch (error: any) {
        notify(error.message, 'error')
      }
    }
  }

  // 把所有待处理批注汇总成一条改稿指令，走 diff 确认
  const submitCommentsToAI = async () => {
    const open = comments.filter((comment) => comment.status === 'open')
    if (!open.length) {
      notify('没有待处理的批注', 'info')
      return
    }
    if (editTarget !== 'article.md') {
      notify('先回到「正文」标签，再把批注交给 AI 改', 'info')
      return
    }
    const body = open
      .map((comment, index) => {
        const quote = comment.quote ? `「${comment.quote.slice(0, 160)}」` : ''
        return `${index + 1}. ${quote}${quote ? ' → ' : ''}${comment.body}`
      })
      .join('\n')
    setBusy(true)
    try {
      const data = await api.post<{ before: string; article: string; file: string }>(`/api/projects/${slug}/edit`, {
        instruction: `按下面的批注逐条修改这篇文章，只改批注涉及的地方，保留 frontmatter、溯源标记和图片占位符：\n${body}`,
        file: 'article.md',
      })
      setProposal({
        instruction: `全部批注（${open.length} 条）`,
        selected: '',
        file: 'article.md',
        before: data.before,
        after: data.article,
        blocks: diffBlocks(data.before, data.article),
      })
      setPendingCommentIds(open.map((comment) => comment.id))
      setShowComments(false)
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  // ── 批注 ─────────────────────────────────────────────────
  const loadComments = useCallback(() => {
    api
      .get<{ comments: Comment[] }>(`/api/projects/${slug}/comments`)
      .then((data) => setComments(data.comments))
      .catch(() => {})
  }, [slug])

  useEffect(() => {
    loadComments()
  }, [loadComments])

  const addComment = async () => {
    if (!commentDraft.trim() || !selection) return
    try {
      await api.post(`/api/projects/${slug}/comments`, {
        id: `c_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        path: 'article.md',
        body: commentDraft.trim(),
        quote: selection,
      })
      setCommentDraft('')
      setCommenting(false)
      setSelection('')
      setAiScoped(false)
      setSelPopup(null)
      selRange.current = null
      window.getSelection()?.removeAllRanges()
      loadComments()
      notify('批注已保存', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    }
  }

  const removeComment = async (id: string) => {
    try {
      await api.del(`/api/projects/${slug}/comments/${id}`)
      loadComments()
    } catch (error: any) {
      notify(error.message, 'error')
    }
  }

  // 打开其他文件（只读标签页）
  const openEntry = async (entry: FileEntry) => {
    setOpenFile(entry)
    setFileRaw(false)
    setFileLoading(true)
    try {
      const data = await api.get<any>(`/api/projects/${slug}/file?path=${encodeURIComponent(entry.path)}`)
      setFileContent(data)
    } catch (error: any) {
      notify(error.message, 'error')
      setOpenFile(null)
    } finally {
      setFileLoading(false)
    }
  }

  const openArticle = () => setOpenFile(null)

  // 编辑器选区
  const trackEditorSelection = () => {
    const textarea = textareaRef.current
    if (!textarea) return
    const selected = textarea.value.slice(textarea.selectionStart, textarea.selectionEnd).trim()
    selRange.current = null
    setSelPopup(null)
    setSelection(selected.slice(0, 2000))
    setAiScoped(true)
    setCommenting(false)
  }

  // 滚动同步：scrollHeight 按内容版本缓存，滚动帧只做 O(1) 读写，ε 判等防振荡
  const rafPending = useRef(false)
  const editorMax = useRef({ value: 0, at: 0 })
  const previewMax = useRef({ value: 0, at: 0 })
  const applyUntil = useRef(0)

  useEffect(() => {
    editorMax.current = { value: 0, at: 0 }
    previewMax.current = { value: 0, at: 0 }
  }, [text, preview, viewMode, openFile])

  useEffect(() => {
    const onResize = () => {
      editorMax.current = { value: 0, at: 0 }
      previewMax.current = { value: 0, at: 0 }
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const syncReaderToPreview = (reader: HTMLElement) => {
    if (rafPending.current) return
    rafPending.current = true
    window.requestAnimationFrame(() => {
      rafPending.current = false
      const pv = previewRef.current
      if (!pv) return
      if (Date.now() < applyUntil.current) return
      const now = Date.now()
      if (!editorMax.current.value || now - editorMax.current.at > 2000) {
        editorMax.current = { value: reader.scrollHeight - reader.clientHeight, at: now }
      }
      if (!previewMax.current.value || now - previewMax.current.at > 2000) {
        previewMax.current = { value: pv.scrollHeight - pv.clientHeight, at: now }
      }
      const maxR = editorMax.current.value
      const maxP = previewMax.current.value
      if (maxR <= 0 || maxP <= 0) return
      const target = (reader.scrollTop / maxR) * maxP
      if (Math.abs(target - pv.scrollTop) < 2) return
      applyUntil.current = now + 40
      pv.scrollTop = target
    })
  }

  const handlePreviewScroll = () => {
    if (rafPending.current) return
    rafPending.current = true
    window.requestAnimationFrame(() => {
      rafPending.current = false
      const pv = previewRef.current
      const reader = readerRef.current
      if (!pv || !reader) return
      if (Date.now() < applyUntil.current) return
      const now = Date.now()
      if (!editorMax.current.value || now - editorMax.current.at > 2000) {
        editorMax.current = { value: reader.scrollHeight - reader.clientHeight, at: now }
      }
      if (!previewMax.current.value || now - previewMax.current.at > 2000) {
        previewMax.current = { value: pv.scrollHeight - pv.clientHeight, at: now }
      }
      const maxP = previewMax.current.value
      const maxR = editorMax.current.value
      if (maxP <= 0 || maxR <= 0) return
      const target = (pv.scrollTop / maxP) * maxR
      if (Math.abs(target - reader.scrollTop) < 2) return
      applyUntil.current = now + 40
      reader.scrollTop = target
    })
  }

  // 预览内选区跟踪
  const placePopup = () => {
    const range = selRange.current
    if (!range) {
      setSelPopup(null)
      return
    }
    const rect = range.getBoundingClientRect()
    if (!rect || (rect.width === 0 && rect.height === 0)) {
      setSelPopup(null)
      return
    }
    // 放在选区的右下角；右侧/下方空间不够时再回退到左侧/上方
    const width = commenting ? 286 : 176
    const height = commenting ? 168 : 42
    const gap = 8
    // 约束在正文面板内，避免压到右侧手机预览
    const pane = readerRef.current?.closest('.editor-pane')?.getBoundingClientRect()
    const minLeft = (pane ? pane.left : 0) + 10
    const maxLeft = (pane ? pane.right : window.innerWidth) - width - 10
    let left = rect.right + gap
    if (left + width > maxLeft) left = maxLeft
    if (left < minLeft) left = Math.max(12, Math.min(minLeft, window.innerWidth - width - 12))
    let top = rect.bottom + gap
    if (top + height > window.innerHeight - 12) {
      top = Math.max(60, rect.top - height - gap)
    }
    setSelPopup({ top, left })
  }

  // 工具条/批注气泡尺寸变化时重新定位
  useEffect(() => {
    if (!selection || !selRange.current) return
    const id = window.requestAnimationFrame(placePopup)
    return () => window.cancelAnimationFrame(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commenting, selection])

  // 选区消失（点空白、取消选择）时收起浮动工具条
  useEffect(() => {
    const onSelectionChange = () => {
      if (commenting || aiScoped) return
      const sel = window.getSelection()
      const empty = !sel || sel.isCollapsed || !String(sel.toString()).trim()
      if (empty && selRange.current) {
        selRange.current = null
        setSelection('')
        setSelPopup(null)
      }
    }
    document.addEventListener('selectionchange', onSelectionChange)
    return () => document.removeEventListener('selectionchange', onSelectionChange)
  }, [commenting, aiScoped])

  const handleReaderClick = (event: React.MouseEvent) => {
    const hit = hitTest(event.nativeEvent)
    if (!hit) {
      // 点空白处关掉批注浮层
      if (activeComment) setActiveComment(null)
      return
    }
    const width = 280
    const left = Math.min(Math.max(12, hit.rect.left + hit.rect.width / 2 - width / 2), window.innerWidth - width - 12)
    const above = hit.rect.top > 140
    const top = above ? hit.rect.top - 8 : hit.rect.bottom + 8
    setActiveComment({ id: hit.id, top, left, above })
  }

  const trackPreviewSelection = () => {
    const pv = readerRef.current || previewRef.current
    if (!pv) return
    const sel = window.getSelection()
    const selected = String(sel?.toString() || '').trim()
    if (!selected || !sel || sel.rangeCount === 0) return
    if (!pv.contains(sel.anchorNode)) return
    selRange.current = sel.getRangeAt(0).cloneRange()
    setSelection(selected.slice(0, 2000))
    setCommenting(false)
    setAiScoped(false)
    window.requestAnimationFrame(placePopup)
  }

  // 滚动/缩放时让浮层跟随选区
  useEffect(() => {
    if (!selection) return
    let pending = false
    const onScroll = () => {
      if (pending) return
      pending = true
      window.requestAnimationFrame(() => {
        pending = false
        placePopup()
      })
    }
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, commenting])


  const lineCount = useMemo(() => text.split('\n').length, [text])
  const articleExists = loaded && text.trim().length > 0
  const titleOptions = (values.title_options as any) || null

  return (
    <div className="write-view">
      <div className="write-head">
        <div className="write-head-main">
          <p className="eyebrow">{summary.slug}</p>
          <h1>{summary.title}</h1>
        </div>
        <div className="write-head-actions">
          {dirty ? <Badge tone="warn">未保存</Badge> : saving ? <Badge>保存中…</Badge> : null}
          {running ? <Badge tone="accent">{NODE_LABELS[state?.node || ''] || '运行中'}</Badge> : null}
          {interrupt?.kind === 'draft' ? <Badge tone="warn">初稿待过目</Badge> : null}
          {awaitingTitle ? <Badge tone="accent">待选标题</Badge> : null}
          {done ? <Badge tone="ok">可发布</Badge> : null}
          {!articleExists && !running ? (
            <button className="button button-primary button-small" onClick={start} disabled={busy || summary.sourceCount === 0}>
              <Icon name="play" size={15} /> 开始写作
            </button>
          ) : null}
        </div>
      </div>

      {running ? (
        <div className="run-strip">
          <span className="spinner" />
          <span>{NODE_LABELS[state?.node || ''] || '运行中'}</span>
          <em>后台运行，可以去别的页面，完成会通知你</em>
        </div>
      ) : null}

      {interrupt?.kind === 'draft' ? (
        <div className="draft-banner">
          <div className="draft-banner-text">
            <strong>初稿已完成</strong>
            <span>{interrupt.hint}</span>
          </div>
          <input
            className="input"
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="修改意见（可选）：例如 第 2 节太抽象，开头加个场景…"
          />
          <button className="button button-primary button-small" onClick={() => resume('approve')} disabled={busy}>
            定稿，生成标题
          </button>
          <button className="button button-ghost button-small" onClick={() => resume('revise')} disabled={busy || !feedback.trim()}>
            按意见重写
          </button>
        </div>
      ) : null}

      {awaitingTitle && titleOptions ? (
        <div className="title-strip">
          <div className="title-strip-head">
            <strong>选一个标题</strong>
            <span className="muted">{titleOptions.reason}</span>
          </div>
          <div className="title-strip-items">
            {(titleOptions.titles || []).map((option: any, index: number) => (
              <button
                key={index}
                className="title-choice"
                onClick={() => pickTitle(option.title, titleOptions.digest || '')}
                disabled={busy}
                title={option.reason || option.style}
              >
                <span className="title-choice-style">{option.style || `候选 ${index + 1}`}</span>
                {option.title}
                {index + 1 === titleOptions.recommended ? <Badge tone="ok">推荐</Badge> : null}
              </button>
            ))}
          </div>
          <div className="title-strip-digest muted">摘要：{titleOptions.digest}</div>
        </div>
      ) : null}

      <div className={`write-split ${sourcesOpen ? 'with-sources' : ''}`}>
        {/* 资源管理器 */}
        <div className={`sources-drawer ${sourcesOpen ? 'open' : ''}`}>
          <button className="sources-toggle" onClick={() => setSourcesOpen((value) => !value)} title={sourcesOpen ? '收起文件' : '展开文件'}>
            <Icon name="chevron" size={13} />
            <span>文件</span>
            <em>{summary.sourceCount}</em>
          </button>
          {sourcesOpen ? (
            <Explorer
              slug={slug}
              sourceCount={summary.sourceCount}
              articleDirty={dirty}
              activePath={openFile?.path || null}
              onOpenArticle={openArticle}
              onOpenFile={openEntry}
              onSourcesChanged={() => { onRefresh(); onSaved() }}
            />
          ) : null}
        </div>

        {/* 编辑器（正文 / 只读文件标签页） */}
        <section className="editor-pane">
          <div className="pane-head">
            <div className="editor-tabs">
              <button className={`editor-tab ${openFile ? '' : 'active'}`} onClick={openArticle}>
                正文
                {dirty && !openFile ? <i className="dirty-dot" /> : null}
              </button>
              {openFile ? (
                <button className="editor-tab active" onClick={() => {}}>
                  {openFile.path.split('/').pop()}
                  <span className="tab-close" onClick={(event) => { event.stopPropagation(); openArticle() }}>×</span>
                </button>
              ) : null}
            </div>
            <div className="pane-head-actions">
              {openFile ? (
                openFile.path.endsWith('.md') ? (
                  <button className="button button-ghost button-small" onClick={() => setFileRaw((value) => !value)}>
                    {fileRaw ? '看渲染' : '看源码'}
                  </button>
                ) : null
              ) : (
                <>
                  <div className="view-toggle">
                    <button className={viewMode === 'rendered' ? 'active' : ''} onClick={() => { if (dirty) persist(text); setViewMode('rendered') }}>阅读</button>
                    <button className={viewMode === 'source' ? 'active' : ''} onClick={() => setViewMode('source')}>源码</button>
                  </div>
                  <button
                    className={`button button-ghost button-small comment-btn ${showComments ? 'active' : ''}`}
                    onClick={() => setShowComments((value) => !value)}
                    title="批注"
                  >
                    <Icon name="file" size={14} /> 批注
                    {openComments.length ? <span className="comment-count inline">{openComments.length}</span> : null}
                  </button>
                </>
              )}
            </div>
          </div>
          {openFile ? (
            fileLoading ? (
              <div className="editor-empty"><Spinner label="读取文件" /></div>
            ) : (
              <FileViewer slug={slug} entry={openFile} content={fileContent} raw={fileRaw} />
            )
          ) : loaded && articleExists && viewMode === 'rendered' ? (
            <div
              className="article-reader"
              ref={readerRef}
              onScroll={(event) => {
                if (activeComment) setActiveComment(null)
                syncReaderToPreview(event.currentTarget)
              }}
              onClick={handleReaderClick}
              onMouseUp={trackPreviewSelection}
              onKeyUp={trackPreviewSelection}
            >
              {preview ? (
                <div className="article-reader-inner" dangerouslySetInnerHTML={{ __html: preview.html }} />
              ) : (
                <div className="preview-placeholder muted">渲染中…</div>
              )}
            </div>
          ) : loaded && articleExists ? (
            <div className="editor-shell write-editor">
              <div className="line-gutter" aria-hidden="true">
                <div className="line-gutter-inner" ref={gutterRef}>
                  {Array.from({ length: lineCount }, (_, index) => (
                    <button key={index}>{index + 1}</button>
                  ))}
                </div>
              </div>
              <textarea
                ref={textareaRef}
                className="code-editor"
                value={text}
                onChange={(event) => handleChange(event.target.value)}
                onSelect={trackEditorSelection}
                onScroll={(event) => {
                  const ta = event.currentTarget
                  if (gutterRef.current) gutterRef.current.style.transform = `translateY(${-ta.scrollTop}px)`
                }}
                spellCheck={false}
              />
            </div>
          ) : (
            <div className="editor-empty">
              {running ? (
                <Spinner label="初稿撰写中，写完会停在这里等你过目" />
              ) : (
                <>
                  <div className="empty-mark">稿</div>
                  <h3>{summary.sourceCount ? '还没有正文' : '先加几份素材'}</h3>
                  <p>
                    {summary.sourceCount
                      ? '点「开始写作」，AI 会根据素材直接在后台写出初稿。'
                      : '在左侧素材面板拖入文件、粘贴文字或贴链接，然后点「开始写作」。'}
                  </p>
                  {summary.sourceCount ? (
                    <button className="button button-primary" onClick={start} disabled={busy}>
                      <Icon name="play" size={16} /> 开始写作
                    </button>
                  ) : null}
                </>
              )}
            </div>
          )}

          {/* AI 改稿 diff */}
          {proposal ? (
            <div className="diff-card">
              <div className="diff-head">
                <strong>AI 修改建议 · {proposal.file.split('/').pop()}</strong>
                <span className="muted">“{proposal.instruction}”{proposal.selected ? ' · 仅选中部分' : ''}</span>
              </div>
              <div className="diff-body">
                {proposal.blocks.map((block, index) => (
                  <div key={index} className={`diff-block diff-${block.type}`}>
                    {block.type === 'removed' ? <em>删</em> : block.type === 'added' ? <em>增</em> : null}
                    <span>{block.text}</span>
                  </div>
                ))}
              </div>
              <div className="diff-actions">
                <button className="button button-primary button-small" onClick={acceptProposal}>
                  <Icon name="check" size={14} /> 接受改动
                </button>
                <button className="button button-ghost button-small" onClick={() => { setProposal(null); setPendingCommentIds([]) }}>放弃</button>
              </div>
            </div>
          ) : null}

        {/* 点击荧光笔高亮 → 浮出批注内容 */}
      {activeComment ? (() => {
        const comment = comments.find((item) => item.id === activeComment.id)
        if (!comment) return null
        return (
          <div
            className="comment-hit"
            style={{
              top: activeComment.top,
              left: activeComment.left,
              transform: activeComment.above ? 'translateY(-100%)' : undefined,
            }}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="comment-hit-head">
              <Badge tone={comment.status === 'sent' ? 'ok' : 'warn'}>{comment.status === 'sent' ? '已发送' : '待处理'}</Badge>
              <span className="muted">{formatTime(comment.createdAt)}</span>
              <button className="icon-button tiny" onClick={() => { removeComment(comment.id); setActiveComment(null) }} title="删除批注">
                <Icon name="trash" size={12} />
              </button>
              <button className="icon-button tiny" onClick={() => setActiveComment(null)}>×</button>
            </div>
            {comment.quote ? <blockquote>{comment.quote}</blockquote> : null}
            <p>{comment.body}</p>
          </div>
        )
      })() : null}

      {/* 批注列表 */}
        {showComments ? (
          <div className="comments-pop">
            <div className="comments-pop-head">
              <strong>批注 {openComments.length ? `· ${openComments.length} 条待处理` : ''}</strong>
              <div className="comments-pop-actions">
                <button
                  className="button button-primary button-small"
                  onClick={submitCommentsToAI}
                  disabled={busy || !openComments.length}
                  title="把所有待处理批注汇总成一次改稿，diff 确认后生效"
                >
                  <Icon name="spark" size={13} /> 全部交给 AI 改
                </button>
                <button className="icon-button tiny" onClick={() => setShowComments(false)}>×</button>
              </div>
            </div>
            {comments.length === 0 ? <p className="muted">在正文里选中文字，点「加批注」。</p> : null}
            {comments.map((comment) => (
              <div key={comment.id} className={`comment-item ${comment.status}`}>
                <div className="comment-meta">
                  <Badge tone={comment.status === 'sent' ? 'ok' : 'warn'}>{comment.status === 'sent' ? '已发送' : '待处理'}</Badge>
                  <span>{formatTime(comment.createdAt)}</span>
                  <button className="icon-button tiny" onClick={() => removeComment(comment.id)}><Icon name="trash" size={12} /></button>
                </div>
                {comment.quote ? <blockquote>{comment.quote.length > 120 ? comment.quote.slice(0, 120) + '…' : comment.quote}</blockquote> : null}
                <p>{comment.body}</p>
              </div>
            ))}
          </div>
        ) : null}
        </section>

        {/* 实时预览 */}
        <aside className="preview-pane">
          <div className="pane-head">
            <span className="eyebrow">LIVE PREVIEW · 375px</span>
            <button className="button button-ghost button-small" onClick={() => navigate(slug, 'publish')}>
              去发布 <Icon name="arrow" size={13} />
            </button>
          </div>

          <div className="preview-frame" ref={previewRef} onScroll={handlePreviewScroll}>
            {preview ? (
              <div className="preview-card">
                <h1 className="preview-title">{preview.title}</h1>
                <div className="preview-meta">
                  <span>{preview.at ? preview.at.slice(0, 10) : ''}</span>
                  <span>·</span>
                  <span>阅读约 {preview.minutes} 分钟</span>
                </div>
                <div className="preview-body" dangerouslySetInnerHTML={{ __html: preview.html }} />
              </div>
            ) : (
              <div className="preview-placeholder muted">{articleExists ? '预览生成中…' : '初稿出现后，这里显示手机上的效果（只读）'}</div>
            )}
          </div>
        </aside>
      </div>

      {/* chatbox：对当前文件说话（正文或打开的文件） */}
      <div className="chatbox-wrap">
        {selection && aiScoped ? (
          <div className="chatbox-context">
            <span>仅修改选中：{selection.length > 60 ? selection.slice(0, 60) + '…' : selection}</span>
            <button onClick={() => { setSelection(''); setAiScoped(false) }}>×</button>
          </div>
        ) : null}
        <div className="chatbox">
          <Icon name="spark" size={17} />
          <input
            ref={chatInputRef}
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && sendInstruction()}
            placeholder={
              targetBlocked
                ? `${openFile?.path.split('/').pop()} 是只读产物`
                : selection
                  ? '对正文中选中的这段提要求……'
                  : openFile
                    ? openFile.editable
                      ? `对 ${openFile.path.split('/').pop()} 提要求`
                      : `为这份素材写 digest（保存到 ${editTarget.split('/').pop()}）`
                    : '对这篇文章提要求：例如 把结尾改得更有力一点'
            }
            disabled={targetBlocked || (editTarget === 'article.md' && !articleExists) || running}
          />
          <button
            className="chatbox-send"
            onClick={sendInstruction}
            disabled={targetBlocked || busy || !instruction.trim() || (editTarget === 'article.md' && !articleExists)}
            title="发送"
          >
            <Icon name="arrow" size={16} />
          </button>
        </div>
        <p className="chatbox-hint">
          {targetBlocked
            ? '这个文件不可修改'
            : openFile
              ? openFile.editable
                ? `修改会写回 ${editTarget}`
                : `原件只读，AI 结果保存到 ${editTarget}`
              : 'Enter 发送 · 在正文里选中文字，可限定只改那一部分'}
        </p>
      </div>

      {/* 选区浮动工具条 / 批注气泡：跟在选中的文字旁边 */}
      {selPopup && selection && !openFile ? (
        <div
          className={`selection-float ${commenting ? 'commenting' : ''}`}
          style={{ top: selPopup.top, left: selPopup.left }}
          onMouseDown={(event) => event.preventDefault()}
        >
          {commenting ? (
            <div className="comment-pop">
              <div className="comment-pop-quote">「{selection.length > 50 ? selection.slice(0, 50) + '…' : selection}」</div>
              <textarea
                autoFocus
                rows={3}
                value={commentDraft}
                onChange={(event) => setCommentDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) addComment()
                }}
                placeholder="写批注……（⌘/Ctrl+Enter 提交）"
              />
              <div className="comment-pop-actions">
                <button className="button button-primary button-small" onClick={addComment} disabled={!commentDraft.trim()}>
                  提交批注
                </button>
                <button
                  className="button button-ghost button-small"
                  onClick={() => {
                    setCommenting(false)
                    setCommentDraft('')
                  }}
                >
                  取消
                </button>
              </div>
            </div>
          ) : (
            <>
              <button
                className="button button-secondary button-small"
                onClick={() => {
                  setCommenting(true)
                  setCommentDraft('')
                }}
              >
                加批注
              </button>
              <button
                className="icon-button tiny"
                title="取消"
                onClick={() => {
                  selRange.current = null
                  setSelPopup(null)
                  setSelection('')
                  window.getSelection()?.removeAllRanges()
                }}
              >
                ×
              </button>
            </>
          )}
        </div>
      ) : null}

    </div>
  )
}

/** 只读文件查看器：Markdown 渲染 / 源码、HTML 沙箱、图片、纯文本。 */
function FileViewer({ slug, entry, content, raw }: { slug: string; entry: FileEntry; content: any; raw: boolean }) {
  if (!content) return null
  const rewrite = (text: string) =>
    String(text || '').replace(/\]\((images\/[^)]+)\)/g, (_, src) => `](/api/projects/${slug}/raw/${src})`)
  if (entry.kind === 'image') {
    return (
      <div className="image-preview">
        <img src={`/api/projects/${slug}/raw/${entry.path}`} alt={entry.path} />
      </div>
    )
  }
  if (entry.kind === 'html') {
    return <iframe title={entry.path} className="html-preview" srcDoc={content.text || ''} />
  }
  if (entry.path.endsWith('.md') && !raw) {
    return (
      <div className="markdown-file">
        <Markdown text={rewrite(content.text || '')} />
      </div>
    )
  }
  return <pre className="code-readonly file-viewer-pre">{content.text || '（空文件）'}</pre>
}
