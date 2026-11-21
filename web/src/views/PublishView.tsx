import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { navigate } from '../hooks'
import type { Deliverable, ProjectSummary, Slot, ThemeInfo } from '../types'
import { Empty, Icon, useToast } from '../ui'

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('图片解码失败，请到图片目录手动复制'))
    image.src = src
  })
}

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
  const [captionDrafts, setCaptionDrafts] = useState<Record<number, string>>({})
  const inputs = useRef<Record<number, HTMLInputElement | null>>({})
  const skipCaptionBlur = useRef<number | null>(null)

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
      setCaptionDrafts({})
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
      notify('已复制；图片会显示成【配图 NN】占位文字，粘贴后按编号手动补图', 'ok')
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

  const saveCaption = async (slot: Slot, value: string) => {
    const next = value.replace(/\s+/g, ' ').trim()
    if (next === slot.caption) {
      setCaptionDrafts((prev) => {
        const copy = { ...prev }
        delete copy[slot.n]
        return copy
      })
      return
    }
    setBusy(true)
    try {
      const data = await api.post<{ slots: Slot[] }>(`/api/projects/${slug}/slots/caption`, {
        line: slot.line,
        raw: slot.raw,
        caption: next,
      })
      setSlots(data.slots)
      setCaptionDrafts((prev) => {
        const copy = { ...prev }
        delete copy[slot.n]
        return copy
      })
      await load()
      notify(next ? '图注已更新，正文已重新排版' : '已清空图注，正文已重新排版', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const copyImage = async (slot: Slot) => {
    try {
      if (!navigator.clipboard || !window.ClipboardItem) {
        throw new Error('当前浏览器不支持复制图片，请到图片目录手动复制')
      }
      const data = await api.get<{ dataUrl?: string }>(`/api/projects/${slug}/file?path=${encodeURIComponent(slot.src)}`)
      if (!data.dataUrl) throw new Error('图片读取失败，请到图片目录手动复制')
      const image = await loadImage(data.dataUrl)
      const canvas = document.createElement('canvas')
      canvas.width = image.naturalWidth
      canvas.height = image.naturalHeight
      const context = canvas.getContext('2d')
      if (!context) throw new Error('图片转换失败')
      context.drawImage(image, 0, 0)
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
      if (!blob) throw new Error('图片转换失败')
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
      notify(`已复制 ${slot.src}；到公众号对应【配图】位置粘贴`, 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    }
  }

  if (missing) {
    return (
      <div className="publish-view">
        <div className="panel">
          <Empty title="还没有排版产物" hint="在「写作」页完成初稿并选定标题后，这里会出现手机预览、补图和复制。" />
          <div className="center-actions">
            <button className="button button-secondary" onClick={() => navigate(slug, 'write')}>回写作页</button>
          </div>
        </div>
      </div>
    )
  }

  const pendingSlots = slots.filter((slot) => slot.pending)

  return (
    <div className="publish-view">

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
          <div className="publish-actions">
            <button className="button button-primary" onClick={copy}>
              <Icon name="copy" size={15} /> 复制到公众号
            </button>
            <button className="button button-ghost button-small" onClick={rerender} disabled={busy}>
              <Icon name="refresh" size={15} /> {busy ? '重排中…' : '重新排版'}
            </button>
          </div>
        </div>

        <div className="publish-side">
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
                      <div className="slot-caption-row">
                        <input
                          className="slot-caption"
                          value={captionDrafts[slot.n] ?? slot.caption}
                          placeholder="图注（可留空）"
                          aria-label={`配图 ${slot.n} 的图注`}
                          onChange={(event) =>
                            setCaptionDrafts((prev) => ({ ...prev, [slot.n]: event.target.value }))
                          }
                          onBlur={(event) => {
                            if (skipCaptionBlur.current === slot.n) {
                              skipCaptionBlur.current = null
                              return
                            }
                            saveCaption(slot, event.target.value)
                          }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') event.currentTarget.blur()
                          }}
                          disabled={busy}
                        />
                        <button
                          className="slot-caption-clear"
                          onMouseDown={() => {
                            skipCaptionBlur.current = slot.n
                          }}
                          onClick={() => {
                            setCaptionDrafts((prev) => {
                              const copy = { ...prev }
                              delete copy[slot.n]
                              return copy
                            })
                            saveCaption(slot, '')
                          }}
                          disabled={busy || !(captionDrafts[slot.n] ?? slot.caption)}
                          title="删除图注"
                          aria-label={`删除配图 ${slot.n} 的图注`}
                        >
                          ×
                        </button>
                      </div>
                      <small>
                        {slot.pending ? `待补` : slot.src}
                        {slot.pending && slot.keywords.length ? ` · ${slot.keywords.join(' / ')}` : ''}
                        {` · line ${slot.line}`}
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
                        <button className="button button-ghost button-small" onClick={() => copyImage(slot)} disabled={busy}>复制图</button>
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

        </div>
      </div>
    </div>
  )
}
