import { LONG_MSG, MAX_TRANSCRIPT_LOAD_CHARS, MAX_TRANSCRIPT_MESSAGE_CHARS, MAX_TOOL_CONTEXT_CHARS } from '../config/limits.js'
import { buildToolTrailLine, fmtK } from '../lib/text.js'
import type { Msg, SessionInfo } from '../types.js'

export const introMsg = (info: SessionInfo): Msg => ({ info, kind: 'intro', role: 'system', text: '' })

export const imageTokenMeta = (info?: ImageMeta | null) => {
  const { width, height, token_estimate: t } = info ?? {}

  return [width && height ? `${width}x${height}` : '', (t ?? 0) > 0 ? `~${fmtK(t!)} tok` : '']
    .filter(Boolean)
    .join(' · ')
}

export const userDisplay = (text: string) => {
  if (text.length <= LONG_MSG) {
    return text
  }

  const first = text.split('\n')[0]?.trim() ?? ''
  const words = first.split(/\s+/).filter(Boolean)
  const prefix = (words.length > 1 ? words.slice(0, 4).join(' ') : first).slice(0, 80)

  return `${prefix || '(message)'} [long message]`
}

const truncateMiddle = (text: string, limit: number) => {
  if (text.length <= limit) {
    return text
  }

  const marker = '\n...[truncated for TUI]\n'
  const budget = Math.max(0, limit - marker.length)
  const head = Math.floor(budget * 0.35)
  const tail = Math.max(0, budget - head)

  return `${text.slice(0, head)}${marker}${text.slice(-tail)}`
}

export const toTranscriptMessages = (rows: unknown): Msg[] => {
  if (!Array.isArray(rows)) {
    return []
  }

  const out: Msg[] = []
  let pending: string[] = []
  let totalChars = 0

  for (const row of rows.slice(-120)) {
    if (!row || typeof row !== 'object') {
      continue
    }

    const { context, name, role, text } = row as TranscriptRow

    if (role === 'tool') {
      const line = buildToolTrailLine(name ?? 'tool', truncateMiddle(context ?? '', MAX_TOOL_CONTEXT_CHARS))
      pending = [...pending, line].slice(-8)

      continue
    }

    if (typeof text !== 'string' || !text.trim()) {
      continue
    }

    const clipped = truncateMiddle(text, MAX_TRANSCRIPT_MESSAGE_CHARS)
    const size = clipped.length + pending.join('').length

    if (totalChars + size > MAX_TRANSCRIPT_LOAD_CHARS && out.length > 0) {
      out.splice(0, Math.max(1, Math.ceil(out.length / 3)))
      totalChars = out.reduce((sum, msg) => sum + (msg.text?.length ?? 0) + (msg.tools?.join('').length ?? 0), 0)
    }

    if (role === 'assistant') {
      out.push({ role, text: clipped, ...(pending.length && { tools: pending }) })
      totalChars += size
      pending = []
    } else if (role === 'user' || role === 'system') {
      out.push({ role, text: clipped })
      totalChars += clipped.length
      pending = []
    }
  }

  return out
}

export const fmtDuration = (ms: number) => {
  const t = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = t % 60

  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`
}

interface ImageMeta {
  height?: number
  token_estimate?: number
  width?: number
}

interface TranscriptRow {
  context?: string
  name?: string
  role?: string
  text?: string
}
