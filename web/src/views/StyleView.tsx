import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { StyleFile, StyleOverview } from '../types'
import { Badge, Icon, Markdown, Spinner, useToast } from '../ui'

const HINT = '决定 AI 写作时的语气、结构、句子和用词。保存后再写、再改、再评审，都按这份要求来。'

export function StyleView() {
  const { notify } = useToast()
  const [overview, setOverview] = useState<StyleOverview | null>(null)
  const [selected, setSelected] = useState('')
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const newTitleRef = useRef<HTMLInputElement>(null)

  const files = overview?.files || []
  const current = files.find((file) => file.filename === selected) || files[0] || null
  const dirty = Boolean(current && draft !== current.text)
  const count = useMemo(() => [...draft.replace(/\s+/g, '')].length, [draft])
  const maxChars = overview?.maxChars || 12000

  const apply = useCallback((next: StyleOverview, target?: string) => {
    setOverview(next)
    const name =
      target && next.files.some((file) => file.filename === target)
        ? target
        : next.files[0]?.filename || ''
    setSelected(name)
    setDraft(next.files.find((file) => file.filename === name)?.text ?? '')
  }, [])

  const load = useCallback(() => {
    api
      .get<StyleOverview>('/api/style')
      .then((next) => apply(next))
      .catch((error: any) => notify(error.message, 'error'))
  }, [apply, notify])

  useEffect(() => {
    load()
  }, [load])

  const pick = (file: StyleFile) => {
    if (file.filename === current?.filename) return
    if (dirty && !window.confirm('当前文件还没保存，切换会丢掉修改。继续？')) return
    setSelected(file.filename)
    setDraft(file.text)
  }

  const save = useCallback(async () => {
    if (!current || !dirty) return
    setSaving(true)
    try {
      const next = await api.put<StyleOverview>(`/api/style/files/${encodeURIComponent(current.filename)}`, {
        text: draft,
        sync: true,
      })
      apply(next, current.filename)
      notify('已保存并同步到所有文章', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setSaving(false)
    }
  }, [apply, current, dirty, draft, notify])

  const create = async () => {
    const title = newTitle.trim()
    if (!title || createBusy) return
    setCreateBusy(true)
    try {
      const next = await api.post<StyleOverview & { created?: { filename: string } }>('/api/style/files', { title })
      apply(next, next.created?.filename)
      setCreating(false)
      setNewTitle('')
      notify('已新建规则文件', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setCreateBusy(false)
    }
  }

  useEffect(() => {
    if (creating) newTitleRef.current?.focus()
  }, [creating])

  const remove = async (file: StyleFile) => {
    if (dirty && file.filename === current?.filename && !window.confirm('当前文件有未保存修改，确定删除？')) return
    if (!window.confirm(`删除「${file.title || file.filename}」？所有文章里的这份副本也会删除。`)) return
    try {
      const next = await api.del<StyleOverview>(`/api/style/files/${encodeURIComponent(file.filename)}`)
      apply(next)
      notify('已删除规则文件', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    }
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (dirty && !saving) save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dirty, saving, save])

  return (
    <div className="style-view">
      <header className="style-head">
        <div className="write-head-actions">
          {dirty ? <Badge tone="warn">未保存</Badge> : <Badge tone="ok">已保存</Badge>}
          <button className="button button-primary button-small" onClick={() => save()} disabled={saving || !dirty}>
            {saving ? '保存中…' : '保存并应用'}
          </button>
        </div>
      </header>

      <div className="style-split">
        <aside className="style-side">
          <div className="style-docs">
            {files.map((file) => (
              <div key={file.filename} className={`style-doc ${file.filename === current?.filename ? 'active' : ''}`}>
                <button className="style-doc-main" onClick={() => pick(file)} title={file.filename}>
                  <span className="style-doc-title">{file.title || file.filename}</span>
                  <small>{file.bytes} B</small>
                </button>
                {files.length > 1 ? (
                  <button
                    className="style-doc-del"
                    onClick={() => remove(file)}
                    title="删除这份规则"
                    aria-label={`删除 ${file.title || file.filename}`}
                  >
                    <Icon name="trash" size={12} />
                  </button>
                ) : null}
              </div>
            ))}
            {creating ? (
              <div className="style-doc-new">
                <input
                  ref={newTitleRef}
                  className="style-doc-new-input"
                  value={newTitle}
                  placeholder="规则名称，如：标题规则"
                  maxLength={60}
                  onChange={(event) => setNewTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') create()
                    if (event.key === 'Escape') {
                      setCreating(false)
                      setNewTitle('')
                    }
                  }}
                />
                <div className="style-doc-new-actions">
                  <button className="button button-primary button-small" onClick={create} disabled={!newTitle.trim() || createBusy}>
                    {createBusy ? '新建中…' : '新建'}
                  </button>
                  <button
                    className="button button-ghost button-small"
                    onClick={() => {
                      setCreating(false)
                      setNewTitle('')
                    }}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="style-doc-add"
                onClick={() => {
                  setNewTitle('')
                  setCreating(true)
                }}
              >
                ＋ 新建规则
              </button>
            )}
          </div>
        </aside>

        <section className="style-editor-pane">
          <div className="pane-head">
            <span className="eyebrow">{current?.filename || 'writing-style.md'}</span>
            <span className="muted">
              {count} 字符 / 上限 {maxChars}
            </span>
          </div>
          {current ? (
            <>
              <div className="style-editor-split">
                <textarea
                  className="style-editor"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  spellCheck={false}
                />
                <div className="style-preview">
                  <Markdown text={draft} />
                </div>
              </div>
              <div className="style-editor-foot">
                <span className="muted">{HINT}</span>
                <span className="muted">⌘S 保存</span>
              </div>
            </>
          ) : (
            <div className="editor-empty">
              <Spinner label="读取风格" />
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
