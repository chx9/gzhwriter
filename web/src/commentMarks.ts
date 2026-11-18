/**
 * 在渲染稿里把「被批注的原文」高亮出来（荧光笔效果），并支持点击回看批注。
 *
 * 做法：
 * 1. 清掉上一轮注入的 <mark data-comment-id>
 * 2. 对每条带 quote 的批注，在正文文本里做「去空白」匹配
 * 3. 命中后用 Range 包一层 <mark class="cmt-mark" data-comment-id>
 *
 * 只操作我们自己的 mark，不触碰主题渲染出来的 ==高亮==（它已经带内联样式）。
 */

export interface MarkedComment {
  id: string
  quote?: string
  status: 'open' | 'sent'
}

function normChar(ch: string): string {
  return ch
}

/** 收集容器的文本节点与拼接文本（raw + 去掉空白后的 norm 及映射）。 */
function buildTextIndex(root: HTMLElement) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement
      if (!parent) return NodeFilter.FILTER_REJECT
      if (parent.closest('script,style,textarea')) return NodeFilter.FILTER_REJECT
      if (!String(node.nodeValue || '').trim()) return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })
  const nodes: Text[] = []
  const starts: number[] = []
  let raw = ''
  let node = walker.nextNode() as Text | null
  while (node) {
    nodes.push(node)
    starts.push(raw.length)
    raw += node.nodeValue || ''
    node = walker.nextNode() as Text | null
  }
  const normMap: number[] = []
  let norm = ''
  for (let index = 0; index < raw.length; index += 1) {
    const ch = raw[index]
    if (/\s/.test(ch)) continue
    norm += normChar(ch)
    normMap.push(index)
  }
  return { nodes, starts, raw, norm, normMap }
}

function normalizeQuote(quote: string): string {
  return String(quote || '').replace(/\s+/g, '')
}

/** raw 位置 → (text 节点, 节点内偏移)。 */
function locate(index: ReturnType<typeof buildTextIndex>, rawIndex: number): { node: Text; offset: number } | null {
  for (let i = index.nodes.length - 1; i >= 0; i -= 1) {
    const start = index.starts[i]
    const text = index.nodes[i].nodeValue || ''
    if (rawIndex >= start && rawIndex <= start + text.length) {
      return { node: index.nodes[i], offset: Math.min(rawIndex - start, text.length) }
    }
  }
  return null
}

function wrapRange(root: HTMLElement, quote: string, commentId: string, status: 'open' | 'sent'): boolean {
  const index = buildTextIndex(root)
  const needle = normalizeQuote(quote)
  if (!needle) return false
  const pos = index.norm.indexOf(needle)
  if (pos < 0) return false
  const rawStart = index.normMap[pos]
  const rawEnd = index.normMap[pos + needle.length - 1] + 1
  const start = locate(index, rawStart)
  const end = locate(index, rawEnd)
  if (!start || !end) return false
  try {
    const range = document.createRange()
    range.setStart(start.node, start.offset)
    range.setEnd(end.node, end.offset)
    if (range.collapsed) return false
    const mark = document.createElement('mark')
    mark.className = `cmt-mark cmt-${status}`
    mark.dataset.commentId = commentId
    try {
      range.surroundContents(mark)
    } catch {
      // 跨元素边界：抽出内容再包一层，保留内部元素与样式
      const fragment = range.extractContents()
      mark.appendChild(fragment)
      range.insertNode(mark)
    }
    return true
  } catch {
    return false
  }
}

const MARK_SELECTOR = 'mark.cmt-mark'

export interface MarkHit {
  id: string
  rect: DOMRect
}

/** 在容器里重绘批注高亮；返回注入的数量。 */
export function applyCommentMarks(root: HTMLElement | null, comments: MarkedComment[]): number {
  if (!root) return 0
  // 清掉旧标记
  root.querySelectorAll(MARK_SELECTOR).forEach((mark) => {
    const parent = mark.parentNode
    if (!parent) return
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
    parent.removeChild(mark)
    parent.normalize()
  })
  let count = 0
  for (const comment of comments) {
    if (!comment.quote) continue
    if (wrapRange(root, comment.quote, comment.id, comment.status)) count += 1
  }
  return count
}

/** 点击命中批注高亮。 */
export function hitTest(event: MouseEvent): MarkHit | null {
  const target = event.target as HTMLElement | null
  const mark = target?.closest?.(MARK_SELECTOR) as HTMLElement | null
  if (!mark) return null
  return { id: mark.dataset.commentId || '', rect: mark.getBoundingClientRect() }
}
