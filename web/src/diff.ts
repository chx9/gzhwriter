// 前端段落级 diff：把 AI 改稿结果对比展示，接受/拒绝。

export interface DiffBlock {
  type: 'same' | 'removed' | 'added'
  text: string
}

export function splitBlocks(text: string): string[] {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: string[] = []
  let buffer: string[] = []
  for (const line of lines) {
    buffer.push(line)
    if (line.trim() === '') {
      if (buffer.join('').trim()) blocks.push(buffer.join('\n'))
      buffer = []
    }
  }
  if (buffer.join('').trim()) blocks.push(buffer.join('\n'))
  return blocks
}

const norm = (block: string) => block.replace(/\s+/g, ' ').trim()

/** 简单 LCS（段落数很少，够用）：返回合并后的 diff 块序列。 */
export function diffBlocks(before: string, after: string): DiffBlock[] {
  const a = splitBlocks(before)
  const b = splitBlocks(after)
  const n = a.length
  const m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = norm(a[i]) === norm(b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: DiffBlock[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (norm(a[i]) === norm(b[j])) {
      out.push({ type: 'same', text: a[i] })
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: 'removed', text: a[i] })
      i += 1
    } else {
      out.push({ type: 'added', text: b[j] })
      j += 1
    }
  }
  while (i < n) out.push({ type: 'removed', text: a[i++] })
  while (j < m) out.push({ type: 'added', text: b[j++] })
  // 合并相邻同类块，减少碎片
  const merged: DiffBlock[] = []
  for (const block of out) {
    const last = merged[merged.length - 1]
    if (last && last.type === block.type && block.type !== 'same') {
      last.text += '\n\n' + block.text
    } else {
      merged.push({ ...block })
    }
  }
  return merged
}
