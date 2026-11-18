import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { navigate } from '../hooks'
import type { Deliverable, ProjectSummary, Slot, ThemeInfo } from '../types'
import { Badge, Empty, Icon, Spinner, formatTime, useToast } from '../ui'

export function PublishView({
  slug,
  summary,
  onRefresh,
}: {
  slug: string
  summary: ProjectSummary
  onRefresh: () => void
}) {
  const { notify } = useToast()
  const [deliverable, setDeliverable] = useState<Deliverable | null>(null)
  const [missing, setMissing] = useState(false)
  const [slots, setSlots] = useState<Slot[]>([])
  const [themes, setThemes] = useState<ThemeInfo[]>([])
  const [activeTheme, setActiveTheme] = useState(summary.theme || 'wechat')
  const [previewHtml, setPreviewHtml] = useState<{ html: string; title: string; minutes: number; at: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const inputs = useRef<Record<number, HTMLInputElement | null>>({})

  const load = useCallback(async () => {
    try {
      const data = await api.get<Deliverable>(`/api/projects/${slug}/deliverable`)
      setDeliverable(data)
      setMissing(false)
      setActiveTheme(data.theme)
    } catch (error: any) {
      if (error.status === 404) {
        setDeliverable(null)
        setMissing(true)
      } else {
        notify(error.message, 'error')
      }
    }
    try {
      const slotData = await api.get<{ slots: Slot[] }>(`/api/projects/${slug}/slots`)
      setSlots(slotData.slots)
    } catch {
      setSlots([])
    }
    try {
      const themeData = await api.get<{ themes: ThemeInfo[] }>('/api/themes')
      setThemes(themeData.themes)
    } catch {
      setThemes([])
    }
  }, [slug, notify])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!deliverable) return
    api
      .post<{ html: string; title: string; minutes: number; renderedAt: string }>(`/api/projects/${slug}/preview`, {})
      .then((data) => setPreviewHtml({ html: data.html, title: data.title, minutes: data.minutes, at: data.renderedAt }))
      .catch(() => {})
  }, [slug, deliverable?.renderedAt])

  const copy = async () => {
    if (!deliverable) return
    try {
      const text = deliverable.html.replace(/<[^>]+>/g, '\n').replace(/\n{2,}/g, '\n').trim()
      if (navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/html': new Blob([deliverable.copyHtml], { type: 'text/html' }),
            'text/plain': new Blob([text], { type: 'text/plain' }),
          }),
        ])
      } else {
        await navigator.clipboard.writeText(deliverable.copyHtml)
      }
      notify('已复制，到公众号编辑器里直接粘贴', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    }
  }

  const rerender = async () => {
    setBusy(true)
    try {
      await api.post(`/api/projects/${slug}/render`)
      await load()
      notify('已重新排版', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const chooseTheme = async (theme: string) => {
    setBusy(true)
    try {
      await api.post(`/api/projects/${slug}/theme`, { theme, rerender: true })
      setActiveTheme(theme)
      await load()
      notify('主题已切换', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const fill = async (slot: Slot, file: File) => {
    setBusy(true)
    try {
      const form = new FormData()
      form.append('line', String(slot.line))
      form.append('raw', slot.raw)
      form.append('file', file)
      const data = await api.upload<{ slots: Slot[] }>(`/api/projects/${slug}/slots/fill`, form)
      setSlots(data.slots)
      await load()
      notify('图片已填入，并自动重新排版', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const clear = async (slot: Slot) => {
    setBusy(true)
    try {
      const data = await api.post<{ slots: Slot[] }>(`/api/projects/${slug}/slots/clear`, { line: slot.line, raw: slot.raw })
      setSlots(data.slots)
      await load()
      notify('已退回占位符', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  if (missing) {
    return (
      <div className="publish-view">
        <div className="sources-head">
          <div>
            <p className="eyebrow">PUBLISH</p>
            <h1>发布</h1>
          </div>
        </div>
        <div className="panel">
          <Empty title="还没有排版产物" hint="在「写作」页完成初稿并选定标题后，这里会出现手机预览和发布检查。" />
          <div className="center-actions">
            <button className="button button-secondary" onClick={() => navigate(slug, 'write')}>回写作页</button>
          </div>
        </div>
      </div>
    )
  }

  const lint = deliverable?.checks?.lint?.summary
  const source = deliverable?.checks?.sourceCheck?.summary
  const issues = [...(deliverable?.checks?.lint?.issues || []), ...(deliverable?.checks?.sourceCheck?.issues || [])]
  const pendingSlots = slots.filter((slot) => slot.pending)

  return (
    <div className="publish-view">
      <div className="sources-head">
        <div>
          <p className="eyebrow">PUBLISH</p>
          <h1>发布</h1>
          <p className="muted">发之前的一切都在这里：预览、补图、检查、换主题、复制。</p>
        </div>
        <Badge tone={lint?.errors ? 'error' : 'ok'}>{lint ? (lint.errors ? `${lint.errors} 个 error` : '可交付') : '未检查'}</Badge>
      </div>

      {deliverable?.stale ? (
        <div className="stale-banner">
          <Icon name="clock" size={16} />
          <div>
            <strong>正文在上次排版后改过</strong>
            <span>点「重新排版」让成稿跟上。</span>
          </div>
          <button className="button button-secondary button-small" onClick={rerender} disabled={busy}>重新排版</button>
        </div>
      ) : null}

      <div className="publish-grid">
        <div className="publish-phone">
          <div className="phone-frame">
            <div className="phone-notch" />
            {previewHtml ? (
              <div className="phone-viewport">
                <div className="preview-card">
                  <h1 className="preview-title">{previewHtml.title}</h1>
                  <div className="preview-meta">
                    <span>{previewHtml.at ? previewHtml.at.slice(0, 10) : ''}</span>
                    <span>·</span>
                    <span>阅读约 {previewHtml.minutes} 分钟</span>
                  </div>
                  <div className="preview-body" dangerouslySetInnerHTML={{ __html: previewHtml.html }} />
                </div>
              </div>
            ) : (
              <div className="preview-placeholder muted">预览加载中…</div>
            )}
          </div>
          <p className="muted center">375px 手机预览 · 复制时使用 dist/article.html（图片需到公众号编辑器重新上传）</p>
        </div>

        <div className="publish-side">
          <section className="side-card">
            <div className="side-head">
              <h3>交付状态</h3>
              <span className="muted">{formatTime(deliverable?.renderedAt)}</span>
            </div>
            <dl className="param-list">
              <div><dt>主题</dt><dd>{deliverable?.theme}</dd></div>
              <div><dt>正文字数</dt><dd>{deliverable?.chars || lint?.chars || '—'}</dd></div>
              <div><dt>图片</dt><dd>{deliverable?.images} 张 · {deliverable?.pending} 张待补</dd></div>
              <div><dt>素材核对</dt><dd>{source ? `${source.errors} error / ${source.warnings} warning` : '—'}</dd></div>
            </dl>
            <div className="button-row">
              <button className="button button-primary button-small" onClick={copy}>
                <Icon name="copy" size={15} /> 复制到公众号
              </button>
              <button className="button button-ghost button-small" onClick={rerender} disabled={busy}>
                <Icon name="refresh" size={15} /> {busy ? '重排中…' : '重新排版'}
              </button>
            </div>
          </section>

          <section className="side-card">
            <div className="side-head">
              <h3>主题</h3>
              <span className="muted">换主题不改正文</span>
            </div>
            <div className="theme-mini-grid">
              {themes.map((theme) => (
                <button
                  key={theme.id}
                  className={`theme-mini ${activeTheme === theme.id ? 'active' : ''}`}
                  onClick={() => chooseTheme(theme.id)}
                  disabled={busy}
                  title={theme.description}
                >
                  <span className="theme-swatch" style={{ background: theme.accent || '#8a8a8a' }} />
                  {theme.name}
                </button>
              ))}
            </div>
          </section>

          <section className="side-card">
            <div className="side-head">
              <h3>配图</h3>
              <span className="muted">{pendingSlots.length ? `${pendingSlots.length} 张待补` : '已补齐'}</span>
            </div>
            {slots.length === 0 ? (
              <p className="muted">正文没有图位。AI 按大纲在合适位置放了占位符才会出现卡片。</p>
            ) : (
              <div className="slot-list">
                {slots.map((slot) => (
                  <div key={slot.n} className={`slot-row ${slot.pending ? 'pending' : 'filled'}`}>
                    <span className="slot-number">{String(slot.n).padStart(2, '0')}</span>
                    <div className="slot-info">
                      <strong>{slot.caption || '无图注'}</strong>
                      <small>
                        line {slot.line}
                        {slot.pending ? ` · ${slot.keywords.join(' / ')}` : ''}
                      </small>
                    </div>
                    {slot.pending ? (
                      <div className="slot-row-actions">
                        <button className="button button-ghost button-small" onClick={() => inputs.current[slot.n]?.click()}>
                          选图
                        </button>
                        {slot.searches.map((search) => (
                          <a key={search.keyword} className="slot-google" href={search.url} target="_blank" rel="noreferrer" title={search.keyword}>
                            <Icon name="external" size={12} />
                          </a>
                        ))}
                      </div>
                    ) : (
                      <div className="slot-row-actions">
                        <button className="button button-ghost button-small" onClick={() => inputs.current[slot.n]?.click()}>换图</button>
                        <button className="button button-ghost button-small danger" onClick={() => clear(slot)} disabled={busy}>清除</button>
                      </div>
                    )}
                    <input
                      ref={(element) => { inputs.current[slot.n] = element }}
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/gif"
                      hidden
                      onChange={(event) => {
                        const file = event.target.files?.[0]
                        if (file) fill(slot, file)
                        event.target.value = ''
                      }}
                    />
                  </div>
                ))}
              </div>
            )}
          </section>

          {issues.length ? (
            <section className="side-card">
              <div className="side-head">
                <h3>发布检查</h3>
                <span className="muted">{issues.length} 项</span>
              </div>
              <div className="issue-list compact">
                {issues.map((issue) => (
                  <div key={`${issue.id}-${issue.line || ''}`} className={`issue issue-${issue.severity}`}>
                    <Badge tone={issue.severity === 'error' ? 'error' : issue.severity === 'warning' ? 'warn' : 'neutral'}>{issue.severity}</Badge>
                    <span>{issue.detail}</span>
                    {issue.line ? (
                      <button className="issue-goto" onClick={() => navigate(slug, 'write')} title="去写作页修改">
                        line {issue.line}
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </div>
  )
}
