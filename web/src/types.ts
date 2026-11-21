export type Severity = 'error' | 'warning' | 'info'

export interface Issue {
  id: string
  severity: Severity
  rule?: string
  type?: string
  detail: string
  anchor?: string
  line?: number
  excerpt?: string
}

export interface Checks {
  lint?: {
    summary: {
      errors: number
      warnings: number
      infos: number
      chars: number
      title: { text: string; chars: number }
      digest: { chars: number }
    }
    issues: Issue[]
  }
  sourceCheck?: {
    summary: {
      errors: number
      warnings: number
      paragraphs: number
      sourced: number
      opinion: number
      unsourced: number
      quotes: number
      figures: number
      citedSources: string[]
      unusedSources: string[]
    }
    issues: Issue[]
  }
  renderedAt?: string
  theme?: string
}

export interface InterruptPayload {
  kind: 'outline'
  title: string
  content: string
  path: string
  options?: {
    titles?: string[]
    titleOptions?: { title: string; style?: string; reason?: string }[]
    digest?: string
    recommended?: number
    reason?: string
  }
  round?: number
  maxRounds?: number
  hint?: string
  notice?: string
  actions?: { id: string; label: string }[]
}

export interface RunnerState {
  slug: string
  status: 'idle' | 'running' | 'interrupted' | 'done' | 'resumable'
  stage: string
  node: string | null
  next: string[]
  values: Record<string, any>
  interrupt: InterruptPayload | null
  running: boolean
  hasCheckpoint?: boolean
  updatedAt?: string
}

export interface ProjectSummary {
  slug: string
  title: string
  untitled?: boolean
  topic?: string
  stage: string
  status?: string
  sourceCount: number
  hasArticle: boolean
  hasOutput: boolean
  pendingImages: number
  targetChars: number
  theme: string
  createdAt?: string
  updatedAt?: string
}

export interface ProjectDetail {
  project: Record<string, any> & { slug: string }
  summary: ProjectSummary
  state: RunnerState
  checks: Checks | null
}

export interface FileEntry {
  path: string
  name: string
  dir: string
  bytes: number
  mtime: number
  category: 'source' | 'intake' | 'draft' | 'output'
  sourceId?: string
  kind: string
  editable?: boolean
}

export interface Slot {
  n: number
  line: number
  raw: string
  caption: string
  src: string
  pending: boolean
  keywords: string[]
  searches: { keyword: string; url: string }[]
  context: string
}

export interface Comment {
  id: string
  path: string
  body: string
  quote?: string
  lineStart?: number
  lineEnd?: number
  status: 'open' | 'sent'
  createdAt: string
  sentAt?: string
}

export interface ThemeInfo {
  id: string
  name: string
  description: string
  accent?: string
  builtin: boolean
  source: string
  active?: boolean
}

export type StyleKind = string

export interface StyleFile {
  kind: StyleKind
  label: string
  title: string
  filename: string
  exists: boolean
  text: string
  bytes: number
  defaultText: string
  builtin: boolean
  changed?: boolean
}

export interface StyleProject {
  slug: string
  title: string
  changed: StyleKind[]
}

export interface StyleOverview {
  files: StyleFile[]
  projects: StyleProject[]
  root: string
  maxChars: number
  result?: { synced: number; skipped: string[] }
}

export interface ModelOption {
  provider: string
  model: string
  configured: boolean
  active: boolean
}

export interface Health {
  ok: boolean
  version: string
  demo: boolean
  provider?: string
  model: string | null
  baseUrl: string | null
  models?: ModelOption[]
  dataDir: string
  maxReviewRounds: number
}

export interface DeliverableImage {
  n: number
  src: string
  name: string
  alt: string
}

export interface Deliverable {
  html: string
  copyHtml: string
  copyImages: DeliverableImage[]
  renderedAt: number
  articleChangedAt: number | null
  stale: boolean
  pending: number
  images: number
  chars: number
  theme: string
  checks: Checks | null
  articleTitle: string
}

export interface AppEvent {
  type: string
  slug?: string
  node?: string
  status?: string
  message?: string
  code?: string
  payload?: any
  state?: RunnerState
  at?: string
}
