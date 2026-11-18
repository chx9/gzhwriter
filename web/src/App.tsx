import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { navigate, useHashRoute, usePolling, useProjectEvents, type AppView } from './hooks'
import type { AppEvent, Health, ProjectDetail, ProjectSummary, RunnerState, ThemeInfo } from './types'
import { Badge, Empty, Icon, useToast } from './ui'
import { WriteView } from './views/WriteView'
import { PublishView } from './views/PublishView'

const MAIN_TABS: { id: AppView; label: string }[] = [
  { id: 'write', label: '写作' },
  { id: 'publish', label: '发布' },
]

export function App() {
  const route = useHashRoute()
  const { notify } = useToast()
  const [health, setHealth] = useState<Health | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [state, setState] = useState<RunnerState | null>(null)
  const [themes, setThemes] = useState<ThemeInfo[]>([])
  const [changing, setChanging] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const bootRedirected = useRef(false)

  const slug = route.slug

  const refreshProjects = useCallback(async () => {
    try {
      const data = await api.get<{ projects: ProjectSummary[] }>('/api/projects')
      setProjects(data.projects)
      return data.projects
    } catch (error: any) {
      notify(error.message, 'error')
      return []
    }
  }, [notify])

  const refreshDetail = useCallback(async () => {
    if (!slug) return
    try {
      const data = await api.get<ProjectDetail>(`/api/projects/${slug}`)
      setDetail(data)
      setState(data.state)
    } catch (error: any) {
      if (error instanceof ApiError && error.status === 404) {
        notify('文章不存在，已返回列表', 'error')
        navigate(undefined)
      }
    }
  }, [slug, notify])

  useEffect(() => {
    api.get<Health>('/api/health').then(setHealth).catch(() => {})
    refreshProjects()
    api.get<{ themes: ThemeInfo[] }>('/api/themes').then((data) => setThemes(data.themes)).catch(() => {})
  }, [refreshProjects])

  useEffect(() => {
    if (!slug && projects.length && !bootRedirected.current) {
      bootRedirected.current = true
      navigate(projects[0].slug, 'write')
    }
  }, [slug, projects])

  useEffect(() => {
    setDetail(null)
    setState(null)
    if (slug) refreshDetail()
  }, [slug, refreshDetail])

  useEffect(() => {
    if (!detail) return
    document.title = `${detail.summary.title} · 稿纸`
  }, [detail])

  usePolling(() => {
    if (slug && state?.status === 'running') refreshDetail()
  }, 3500, Boolean(slug && state?.status === 'running'))

  usePolling(refreshDetail, 15000, Boolean(slug))

  const onEvent = useCallback(
    (event: AppEvent) => {
      if ((event.type === 'snapshot' || event.type === 'state' || event.type === 'interrupt' || event.type === 'done') && event.state) {
        setState(event.state)
      }
      if (event.type === 'done' || event.type === 'error' || event.type === 'interrupt') {
        refreshDetail()
        refreshProjects()
      }
      if (event.type === 'error') notify(event.message || '出错了', 'error')
    },
    [notify, refreshDetail, refreshProjects],
  )

  useProjectEvents(slug, onEvent)

  const createProject = async (payload: Record<string, any>) => {
    const { files, ...fields } = payload
    setChanging(true)
    try {
      const data = await api.post<{ project: ProjectSummary }>('/api/projects', fields)
      const created = data.project
      if (Array.isArray(files) && files.length) {
        const form = new FormData()
        for (const file of files) form.append('files', file)
        try {
          await api.upload(`/api/projects/${created.slug}/materials/upload`, form)
        } catch (error: any) {
          notify(`素材上传失败：${error.message}`, 'error')
        }
      }
      await refreshProjects()
      setShowNew(false)
      navigate(created.slug, 'write')
      notify('文章已创建', 'ok')
      // 有素材就直接开写：LLM 自己根据素材写初稿
      const fresh = await api.get<ProjectDetail>(`/api/projects/${created.slug}`)
      if ((fresh.project.sources || []).length > 0) {
        try {
          await api.post(`/api/projects/${created.slug}/start`, {})
        } catch (error: any) {
          notify(error.message, 'info')
        }
      }
      refreshDetail()
    } catch (error: any) {
      notify(error.message, 'error')
    } finally {
      setChanging(false)
    }
  }

  const deleteProject = async () => {
    if (!slug || !window.confirm('删除这篇文章？素材、正文、配图和运行记录都会清理。')) return
    try {
      await api.del(`/api/projects/${slug}`)
      await refreshProjects()
      navigate(undefined)
      notify('已删除', 'ok')
    } catch (error: any) {
      notify(error.message, 'error')
    }
  }

  const changeProject = (next: string) => navigate(next, route.view === 'home' ? 'write' : route.view)

  const current = detail?.summary || projects.find((item) => item.slug === slug) || null

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => navigate(undefined)} title="返回文章列表">
          <span className="brand-mark">稿</span>
          <span className="brand-text">
            <strong>稿纸</strong>
            <small>公众号写作</small>
          </span>
        </button>

        {current ? (
          <nav className="main-tabs" aria-label="主导航">
            {MAIN_TABS.map((tab) => (
              <button key={tab.id} className={route.view === tab.id ? 'active' : ''} onClick={() => navigate(slug, tab.id)}>
                {tab.label}
              </button>
            ))}
          </nav>
        ) : null}

        <div className="topbar-spacer" />

        {health?.demo ? (
          <span className="demo-pill" title="未配置 OPENAI_API_KEY，当前使用演示引擎">
            <span className="demo-dot" />
            演示模式
          </span>
        ) : (
          <span className="model-pill">{health?.model || '模型已连接'}</span>
        )}

        {projects.length ? (
          <select className="project-select" value={slug || ''} onChange={(event) => changeProject(event.target.value)} aria-label="切换文章">
            {projects.map((project) => (
              <option key={project.slug} value={project.slug}>
                {project.title}
              </option>
            ))}
          </select>
        ) : null}

        <button className="button button-primary button-small" onClick={() => setShowNew(true)}>
          <Icon name="plus" size={16} /> 新建文章
        </button>

        {slug ? (
          <button className="icon-button danger" onClick={deleteProject} title="删除文章">
            <Icon name="trash" size={17} />
          </button>
        ) : null}
      </header>

      {health?.demo ? (
        <div className="demo-banner">
          <strong>演示模式</strong>
          <span>未连接真实模型，生成的内容只用于验证流程，不代表写作质量。</span>
        </div>
      ) : null}

      <main className="app-main">
        {!current ? (
          <Home projects={projects} onCreate={() => setShowNew(true)} onOpen={(next) => navigate(next, 'write')} />
        ) : route.view === 'write' ? (
          <WriteView key={current.slug} slug={current.slug} summary={current} state={state} detail={detail} onRefresh={refreshDetail} onSaved={refreshProjects} />
        ) : (
          <PublishView key={current.slug} slug={current.slug} summary={current} onRefresh={refreshDetail} />
        )}
      </main>

      {showNew ? <NewProjectModal themes={themes} busy={changing} onClose={() => setShowNew(false)} onCreate={createProject} /> : null}
    </div>
  )
}

function Home({ projects, onCreate, onOpen }: { projects: ProjectSummary[]; onCreate: () => void; onOpen: (slug: string) => void }) {
  return (
    <div className="home">
      <section className="home-hero">
        <div>
          <p className="eyebrow">WRITING WORKBENCH</p>
          <h1>
            把素材，变成一篇
            <br />
            能直接发的文章。
          </h1>
          <p className="home-lead">
            素材丢进来，AI 在后台消化、拟大纲、写初稿；你在编辑器里过目、让 AI 改、最后排版复制。只在初稿完成时停一次。
          </p>
          <button className="button button-primary" onClick={onCreate}>
            <Icon name="plus" size={17} /> 开始一篇新文章
          </button>
        </div>
        <div className="home-note">
          <span className="note-number">01</span>
          <p>一篇文章就是一个项目：素材、正文、配图、排版都在里面，刷新和重启都不会丢。</p>
          <span className="note-number">02</span>
          <p>写作页左改右看：编辑器旁边就是 375px 手机实时预览，AI 的每次改动先给你看差异。</p>
          <span className="note-number">03</span>
          <p>没有 API Key 也能用演示引擎跑通全程，界面上会明确标注。</p>
        </div>
      </section>

      <section className="home-projects">
        <div className="section-head">
          <h2>我的文章</h2>
          <span>{projects.length} 篇</span>
        </div>
        {projects.length === 0 ? (
          <Empty title="还没有文章" hint="新建一篇，先加几份素材，然后点「开始写作」。" />
        ) : (
          <div className="project-grid">
            {projects.map((project) => (
              <button key={project.slug} className="project-card" onClick={() => onOpen(project.slug)}>
                <div className="project-card-top">
                  <Badge tone={project.status === 'done' ? 'ok' : project.status === 'running' ? 'accent' : 'neutral'}>
                    {project.status === 'done' ? '可发布' : project.status === 'running' ? '写作中' : '进行中'}
                  </Badge>
                  <span className="muted">{project.sourceCount} 份素材</span>
                </div>
                <h3>{project.title}</h3>
                <p>{project.topic || project.slug}</p>
                <div className="project-card-meta">
                  <span>{project.targetChars} 字目标</span>
                  <span>{project.pendingImages ? `${project.pendingImages} 张图待补` : '配图已齐'}</span>
                  <span>{project.theme}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
function NewProjectModal({
  themes,
  busy,
  onClose,
  onCreate,
}: {
  themes: ThemeInfo[]
  busy: boolean
  onClose: () => void
  onCreate: (payload: Record<string, any>) => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  const submit = async () => {
    await onCreate({ title: title.trim(), files })
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal modal-compact" role="dialog" aria-modal="true" aria-label="新建文章">
        <div className="modal-head">
          <div>
            <p className="eyebrow">NEW ARTICLE</p>
            <h2>新建一篇文章</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <label className="field">
          <span>想写什么？（标题或选题）</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="例如：低利率下，存款该怎么分层"
            autoFocus
            onKeyDown={(event) => event.key === 'Enter' && title.trim() && submit()}
          />
        </label>
        <div
          className="dropzone"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            setFiles((current) => [...current, ...Array.from(event.dataTransfer.files)])
          }}
          onClick={() => fileRef.current?.click()}
        >
          <Icon name="file" size={20} />
          <span>{files.length ? `已选 ${files.length} 个文件` : '把素材拖到这里（可选，可多选）'}</span>
          <small>.md .txt .html .srt .vtt .docx · 也可以建好之后再加</small>
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            accept=".md,.markdown,.txt,.html,.htm,.srt,.vtt,.docx"
            onChange={(event) => {
              setFiles((current) => [...current, ...Array.from(event.target.files || [])])
              event.target.value = ''
            }}
          />
        </div>
        {files.length ? (
          <div className="file-chips">
            {files.map((file, index) => (
              <span key={index} className="file-chip">
                {file.name}
                <button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button>
              </span>
            ))}
          </div>
        ) : null}
        <p className="muted modal-note">创建后 AI 会直接根据素材写出初稿；素材不够也可以先建后加。</p>
        <div className="modal-actions">
          <button className="button button-ghost" onClick={onClose}>取消</button>
          <button className="button button-primary" disabled={busy || !title.trim()} onClick={submit}>
            {busy ? '创建中…' : files.length ? '创建并开始写作' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}
