import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { FileEntry } from '../types'
import { Icon, Spinner, useToast } from '../ui'

const CATEGORY_LABELS: Record<string, string> = {
  source: '素材原件',
  intake: '入库与消化',
  draft: '过程稿',
  output: '排版交付',
}
const CATEGORY_ORDER = ['source', 'intake', 'draft', 'output']

/** VS Code 式资源管理器：正文置顶，其余文件分组树。 */
export function Explorer({
  slug,
  sourceCount,
  articleDirty,
  activePath,
  onOpenArticle,
  onOpenFile,
  onSourcesChanged,
}: {
  slug: string
  sourceCount: number
  articleDirty: boolean
  activePath: string | null // null = 正文
  onOpenArticle: () => void
  onOpenFile: (entry: FileEntry) => void
  onSourcesChanged: () => void
}) {
  const { notify } = useToast()
  const [files, setFiles] = useState<FileEntry[]>([])
  const [addOpen, setAddOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ files: FileEntry[] }>(`/api/projects/${slug}/files`)
      setFiles(data.files.filter((entry) => entry.path !== 'article.md'))
    } catch (error: any) {
      notify(error.message, 'error')
    }
  }, [slug, notify])

  useEffect(() => {
    load()
  }, [load, sourceCount])

  const changed = async () => {
    await load()
    onSourcesChanged()
  }

  // 素材按目录聚合成文件夹
  const groups = useMemo(() => {
    const byCategory: Record<string, (FileEntry | { dirKey: string; entries: FileEntry[] })[]> = {}
    const push = (category: string, item: FileEntry | { dirKey: string; entries: FileEntry[] }) => {
      byCategory[category] = [...(byCategory[category] || []), item]
    }
    const sourceDirs = new Map<string, FileEntry[]>()
    for (const entry of files) {
      if (entry.path.startsWith('sources/') && entry.path.split('/').length > 2) {
        const dirKey = entry.path.split('/').slice(0, 2).join('/')
        sourceDirs.set(dirKey, [...(sourceDirs.get(dirKey) || []), entry])
      } else {
        push(entry.category, entry)
      }
    }
    for (const [dirKey, entries] of sourceDirs) {
      push('source', { dirKey, entries: entries.sort((a, b) => a.path.localeCompare(b.path)) })
    }
    return byCategory
  }, [files])

  return (
    <div className="explorer">
      <div className="explorer-head">
        <span className="eyebrow">EXPLORER</span>
        <button className={`icon-button tiny ${addOpen ? 'active' : ''}`} onClick={() => setAddOpen((value) => !value)} title="添加素材">
          <Icon name="plus" size={14} />
        </button>
      </div>

      {addOpen ? <AddSourceBox slug={slug} onDone={async () => { setAddOpen(false); await changed() }} onCancel={() => setAddOpen(false)} /> : null}

      <button className={`explorer-article ${activePath === null ? 'active' : ''}`} onClick={onOpenArticle}>
        <Icon name="file" size={14} />
        <span>正文</span>
        <em>article.md</em>
        {articleDirty ? <i className="dirty-dot" title="未保存" /> : null}
      </button>

      <div className="explorer-tree">
        {CATEGORY_ORDER.map((category) => {
          const items = groups[category] || []
          if (!items.length) return null
          return (
            <div key={category} className="explorer-group">
              <div className="explorer-group-title">{CATEGORY_LABELS[category]}</div>
              {items.map((item, index) =>
                'dirKey' in item ? (
                  <SourceFolder key={item.dirKey} slug={slug} dirKey={item.dirKey} entries={item.entries} onOpenFile={onOpenFile} />
                ) : (
                  <button
                    key={item.path}
                    className={`explorer-file ${activePath === item.path ? 'active' : ''}`}
                    onClick={() => onOpenFile(item)}
                    title={item.path}
                  >
                    <Icon name={item.kind === 'image' ? 'image' : 'file'} size={13} />
                    <span>{item.path.split('/').pop()}</span>
                    <em>{item.path}</em>
                  </button>
                ),
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SourceFolder({
  slug,
  dirKey,
  entries,
  onOpenFile,
}: {
  slug: string
  dirKey: string
  entries: FileEntry[]
  onOpenFile: (entry: FileEntry) => void
}) {
  const [open, setOpen] = useState(false)
  const label = dirKey.split('/')[1] || dirKey
  return (
    <div className="explorer-folder">
      <button className="explorer-file folder" onClick={() => setOpen((value) => !value)}>
        <Icon name="chevron" size={12} />
        <span>{label}</span>
      </button>
      {open
        ? entries.map((entry) => (
            <button
              key={entry.path}
              className="explorer-file nested"
              onClick={() => onOpenFile(entry)}
              title={entry.path}
            >
              <Icon name={entry.kind === 'image' ? 'image' : 'file'} size={12} />
              <span>{entry.path.split('/').pop()}</span>
            </button>
          ))
        : null}
    </div>
  )
}

function AddSourceBox({ slug, onDone, onCancel }: { slug: string; onDone: () => Promise<void>; onCancel: () => void }) {
  const { notify } = useToast()
  const [tab, setTab] = useState<'file' | 'text' | 'url' | 'bili'>('file')
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [biliUrl, setBiliUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [busyLabel, setBusyLabel] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    try {
      const form = new FormData()
      for (const file of Array.from(files)) form.append('files', file)
      const result = await api.upload<{ results: any[] }>(`/api/projects/${slug}/materials/upload`, form)
      const ok = result.results.filter((item) => item.status === 'imported').length
      notify(`已入库 ${ok} 份`, 'ok')
      await onDone()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const addText = async () => {
    if (!text.trim()) return
    setBusy(true)
    try {
      const result = await api.post<{ results: any[] }>(`/api/projects/${slug}/materials/text`, { text, title: title || undefined })
      const item = result.results[0]
      if (item?.status === 'error') throw new Error(item.error)
      notify(item?.status === 'duplicate' ? `与 ${item.id} 重复` : `已入库 ${item?.id || ''}`, 'ok')
      await onDone()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const addBili = async () => {
    if (!biliUrl.trim()) return
    setBusy(true)
    setBusyLabel('提取字幕中，可能需要十几秒…')
    try {
      const result = await api.post<{ results: any[] }>(`/api/projects/${slug}/materials/bilibili`, { url: biliUrl.trim() })
      const item = result.results[0]
      if (item?.status === 'error') throw new Error(item.error)
      notify(`字幕已入库 ${item?.id || ''}（口语稿）`, 'ok')
      await onDone()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
      setBusyLabel('')
    }
  }

  const addUrl = async () => {
    if (!url.trim()) return
    setBusy(true)
    try {
      const result = await api.post<{ results: any[] }>(`/api/projects/${slug}/materials/url`, { url, title: title || undefined })
      const item = result.results[0]
      if (item?.status === 'error') throw new Error(item.error)
      notify(`已入库 ${item?.id || ''}`, 'ok')
      await onDone()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="explorer-add">
      <div className="segmented small">
        <button className={tab === 'file' ? 'active' : ''} onClick={() => setTab('file')}>文件</button>
        <button className={tab === 'text' ? 'active' : ''} onClick={() => setTab('text')}>粘贴</button>
        <button className={tab === 'url' ? 'active' : ''} onClick={() => setTab('url')}>链接</button>
        <button className={tab === 'bili' ? 'active' : ''} onClick={() => setTab('bili')}>B站</button>
        <button className="seg-cancel" onClick={onCancel}>×</button>
      </div>
      {tab === 'file' ? (
        <div className="dropzone small" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); upload(event.dataTransfer.files) }} onClick={() => fileRef.current?.click()}>
          <span>{busy ? '上传中…' : '拖入或点击选择'}</span>
          <input ref={fileRef} type="file" multiple hidden accept=".md,.markdown,.txt,.html,.htm,.srt,.vtt,.docx" onChange={(event) => upload(event.target.files)} />
        </div>
      ) : tab === 'text' ? (
        <div className="source-add-form">
          <textarea className="textarea" rows={3} value={text} onChange={(event) => setText(event.target.value)} placeholder="粘贴文字、字幕或 Markdown……" />
          <div className="source-add-row">
            <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="标题（可选）" />
            <button className="button button-secondary button-small" onClick={addText} disabled={busy || !text.trim()}>入库</button>
          </div>
        </div>
      ) : tab === 'url' ? (
        <div className="source-add-form">
          <input className="input" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://…" />
          <div className="source-add-row">
            <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="标题（可选）" />
            <button className="button button-secondary button-small" onClick={addUrl} disabled={busy || !url.trim()}>抓取</button>
          </div>
        </div>
      ) : (
        <div className="source-add-form">
          <input
            className="input"
            value={biliUrl}
            onChange={(event) => setBiliUrl(event.target.value)}
            placeholder="https://www.bilibili.com/video/BV… 或 https://b23.tv/…"
          />
          <span className="muted small-note">
            {busy
              ? busyLabel
              : '取播放器已有字幕（含 AI 字幕）。需要浏览器里登录过 B 站；读 Chrome Cookie 前先退出 Chrome'}
          </span>
          <button className="button button-secondary button-small bili-submit" onClick={addBili} disabled={busy || !biliUrl.trim()}>
            {busy ? '提取中…' : '取字幕入库'}
          </button>
        </div>
      )}
    </div>
  )
}
