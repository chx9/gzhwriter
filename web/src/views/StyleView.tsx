import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { StyleOverview } from '../types'
import { Badge, Icon, Spinner, useToast } from '../ui'

const HINT = '决定 AI 写作时的语气、结构、句子和用词。保存后再写、再改、再评审，都按这份要求来。'

export function StyleView() {
  const { notify } = useToast()
  const [overview, setOverview] = useState<StyleOverview | null>(null)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState<string | null>(null)

  const current = overview?.files[0] || null
  const dirty = Boolean(current && draft !== current.text)
  const count = useMemo(() => [...draft.replace(/\s+/g, '')].length, [draft])
  const maxChars = overview?.maxChars || 12000
  const projects = overview?.projects || []
  const customized = projects.filter((project) => project.changed.length > 0)

  const apply = useCallback((next: StyleOverview) => {
    setOverview(next)
    setDraft(next.files[0]?.text ?? '')
  }, [])

  const load = useCallback(() => {
    api
      .get<StyleOverview>('/api/style')
      .then(apply)
      .catch((error: any) => notify(error.message, 'error'))
  }, [apply, notify])

  useEffect(() => {
    load()
  }, [load])

  const save = useCallback(
    async (overwriteCustom = false) => {
      if (!current || !dirty) return
      setSaving(true)
      try {
        const next = await api.put<StyleOverview>('/api/style', {
          kind: current.kind,
          text: draft,
          sync: true,
          overwriteCustom,
        })
        apply(next)
        const result = next.result
        if (result?.skipped.length) {
          notify(`已保存，同步 ${result.synced} 篇；${result.skipped.length} 篇有单独改动，未覆盖`, 'ok')
        } else {
          notify(`已保存并同步到 ${result?.synced ?? 0} 篇文章`, 'ok')
        }
      } catch (error: any) {
        notify(error.message, 'error')
      } finally {
        setSaving(false)
      }
    },
    [apply, current, dirty, draft, notify],
  )

  const sync = async (target?: string) => {
    if (!current) return
    setSyncing(target || 'all')
    try {
      const next = await api.post<StyleOverview>('/api/style/sync', {
        kind: current.kind,
        slug: target,
        overwriteCustom: true,
      })
      apply(next)
      notify(target ? '已覆盖到这篇' : '已覆盖到所有文章', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setSyncing(null)
    }
  }

  const reset = async () => {
    if (!current) return
    if (!window.confirm('把文笔风格恢复为默认内容？所有文章的单独改动都会被覆盖。')) return
    setSaving(true)
    try {
      const next = await api.post<StyleOverview>('/api/style/reset', { kind: current.kind, sync: true })
      apply(next)
      notify('已恢复默认并同步到所有文章', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setSaving(false)
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
        <div>
          <p className="eyebrow">GLOBAL STYLE · 所有文章共用</p>
          <h1>文章风格</h1>
          <p className="muted">在这里定义一次，之后每篇文章的写作、改稿和评审都按它来。保存时会同步到所有文章。</p>
        </div>
        <div className="write-head-actions">
          {dirty ? <Badge tone="warn">未保存</Badge> : <Badge tone="ok">已同步</Badge>}
          <button className="button button-ghost button-small" onClick={reset} disabled={saving || !overview}>
            恢复默认
          </button>
          <button className="button button-primary button-small" onClick={() => save()} disabled={saving || !dirty}>
            {saving ? '保存中…' : '保存并应用'}
          </button>
        </div>
      </header>

      <div className="style-split">
        <aside className="style-side">
          <div className="style-files">
            <div className="style-sync-head">
              <p className="eyebrow">应用状态</p>
              <span className="muted">{projects.length} 篇</span>
            </div>
            {projects.length === 0 ? (
              <p className="muted style-empty">还没有文章。</p>
            ) : (
              <div className="style-projects">
                {projects.map((project) => {
                  const changedHere = project.changed.length > 0
                  return (
                    <div key={project.slug} className="style-project">
                      <span className="style-project-title" title={project.title}>
                        {project.title}
                      </span>
                      {changedHere ? (
                        <button
                          className="button button-ghost button-small"
                          onClick={() => sync(project.slug)}
                          disabled={Boolean(syncing)}
                          title="用全局文笔风格覆盖这篇的单独改动"
                        >
                          {syncing === project.slug ? '覆盖中…' : '覆盖'}
                        </button>
                      ) : (
                        <span className="badge badge-ok">跟随</span>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
            {customized.length > 0 ? (
              <button className="button button-secondary button-small" onClick={() => sync()} disabled={Boolean(syncing)}>
                {syncing === 'all' ? '覆盖中…' : `全部覆盖（${customized.length} 篇）`}
              </button>
            ) : null}
          </div>

          <div className="style-side-note">
            <Icon name="spark" size={14} />
            <p>文笔风格管「怎么写」。版面样式仍然由每篇文章选的「排版主题」决定。</p>
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
              <textarea
                className="style-editor"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                spellCheck={false}
              />
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
