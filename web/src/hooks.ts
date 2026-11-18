import { useCallback, useEffect, useRef, useState } from 'react'
import type { AppEvent } from './types'

export type AppView = 'write' | 'publish'

export interface Route {
  slug?: string
  view: 'home' | AppView
}

const VIEW_ALIASES: Record<string, AppView> = {
  write: 'write',
  compose: 'write',
  publish: 'publish',
  materials: 'publish',
}

export function parseHash(): Route {
  const hash = window.location.hash.replace(/^#\/?/, '')
  const parts = hash.split('/').filter(Boolean)
  if (parts[0] !== 'p' || !parts[1]) return { view: 'home' }
  const slug = parts[1]
  const view = VIEW_ALIASES[parts[2] || 'write'] || 'write'
  return { slug, view }
}

export function navigate(slug: string | undefined, view: AppView = 'write') {
  const hash = slug ? `#/p/${slug}/${view}` : '#/'
  if (window.location.hash !== hash) window.location.hash = hash
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(parseHash)
  useEffect(() => {
    const onChange = () => setRoute(parseHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function useProjectEvents(slug: string | undefined, onEvent: (event: AppEvent) => void) {
  const handler = useRef(onEvent)
  handler.current = onEvent
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<AppEvent[]>([])
  useEffect(() => {
    if (!slug) return
    setEvents([])
    const source = new EventSource(`/api/projects/${slug}/events`)
    const consume = (raw: MessageEvent) => {
      try {
        const event = JSON.parse(raw.data) as AppEvent
        setEvents((current) => [...current.slice(-40), event])
        handler.current(event)
      } catch {
        // ignore malformed event
      }
    }
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    for (const type of ['snapshot', 'state', 'node', 'interrupt', 'done', 'error', 'run_started']) {
      source.addEventListener(type, consume as EventListener)
    }
    return () => {
      source.close()
      setConnected(false)
    }
  }, [slug])
  return { connected, events }
}

export function usePolling(callback: () => void, intervalMs: number, enabled = true) {
  const ref = useRef(callback)
  ref.current = callback
  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => ref.current(), intervalMs)
    return () => window.clearInterval(timer)
  }, [enabled, intervalMs])
}

export function useAsync<T>(loader: () => Promise<T>, deps: any[]) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    setLoading(true)
    loader()
      .then((value) => {
        setData(value)
        setError(null)
      })
      .catch((err) => setError(err?.message || String(err)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => {
    load()
  }, [load])
  return { data, loading, error, reload: load, setData }
}
