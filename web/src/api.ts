export class ApiError extends Error {
  code: string
  status: number
  constructor(message: string, code = 'ERROR', status = 0) {
    super(message)
    this.code = code
    this.status = status
  }
}

async function parse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!response.ok) {
    const detail = data?.detail
    const message = typeof detail === 'string' ? detail : detail?.message || data?.message || `HTTP ${response.status}`
    const code = typeof detail === 'object' ? detail?.code : undefined
    throw new ApiError(message, code || 'HTTP_ERROR', response.status)
  }
  return data as T
}

async function request<T>(method: string, path: string, body?: any): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return parse<T>(response)
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body: any = {}) => request<T>('POST', path, body),
  put: <T>(path: string, body: any) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: any) => request<T>('PATCH', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  async upload<T>(path: string, form: FormData): Promise<T> {
    const response = await fetch(path, { method: 'POST', body: form })
    return parse<T>(response)
  },
}

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}
