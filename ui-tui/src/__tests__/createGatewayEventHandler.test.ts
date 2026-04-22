import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { resetOverlayState } from '../app/overlayStore.js'
import { turnController } from '../app/turnController.js'
import { getTurnState, resetTurnState } from '../app/turnStore.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'
import { MAX_REASONING_CHARS, MAX_STREAM_SEGMENTS } from '../config/limits.js'
import { estimateTokensRough } from '../lib/text.js'
import type { Msg } from '../types.js'

const ref = <T>(current: T) => ({ current })

const buildCtx = (appended: Msg[]) =>
  ({
    composer: {
      dequeue: () => undefined,
      queueEditRef: ref<null | number>(null),
      sendQueued: vi.fn()
    },
    gateway: {
      gw: { request: vi.fn() },
      rpc: vi.fn(async () => null)
    },
    session: {
      STARTUP_RESUME_ID: '',
      colsRef: ref(80),
      newSession: vi.fn(),
      resetSession: vi.fn(),
      resumeById: vi.fn(),
      setCatalog: vi.fn()
    },
    system: {
      bellOnComplete: false,
      sys: vi.fn()
    },
    transcript: {
      appendMessage: (msg: Msg) => appended.push(msg),
      panel: (title: string, sections: any[]) =>
        appended.push({ kind: 'panel', panelData: { sections, title }, role: 'system', text: '' }),
      setHistoryItems: vi.fn()
    }
  }) as any

describe('createGatewayEventHandler', () => {
  beforeEach(() => {
    resetOverlayState()
    resetUiState()
    resetTurnState()
    turnController.fullReset()
    patchUiState({ showReasoning: true })
  })

  it('persists completed tool rows when message.complete lands immediately after tool.complete', () => {
    const appended: Msg[] = []

    turnController.reasoningText = 'mapped the page'
    const onEvent = createGatewayEventHandler(buildCtx(appended))

    onEvent({
      payload: { context: 'home page', name: 'search', tool_id: 'tool-1' },
      type: 'tool.start'
    } as any)
    onEvent({
      payload: { name: 'search', preview: 'hero cards' },
      type: 'tool.progress'
    } as any)
    onEvent({
      payload: { summary: 'done', tool_id: 'tool-1' },
      type: 'tool.complete'
    } as any)
    onEvent({
      payload: { text: 'final answer' },
      type: 'message.complete'
    } as any)

    expect(appended).toHaveLength(1)
    expect(appended[0]).toMatchObject({
      role: 'assistant',
      text: 'final answer',
      thinking: 'mapped the page'
    })
    expect(appended[0]?.tools).toHaveLength(1)
    expect(appended[0]?.tools?.[0]).toContain('hero cards')
    expect(appended[0]?.toolTokens).toBeGreaterThan(0)
  })

  it('keeps tool tokens across handler recreation mid-turn', () => {
    const appended: Msg[] = []

    turnController.reasoningText = 'mapped the page'

    createGatewayEventHandler(buildCtx(appended))({
      payload: { context: 'home page', name: 'search', tool_id: 'tool-1' },
      type: 'tool.start'
    } as any)

    const onEvent = createGatewayEventHandler(buildCtx(appended))

    onEvent({
      payload: { name: 'search', preview: 'hero cards' },
      type: 'tool.progress'
    } as any)
    onEvent({
      payload: { summary: 'done', tool_id: 'tool-1' },
      type: 'tool.complete'
    } as any)
    onEvent({
      payload: { text: 'final answer' },
      type: 'message.complete'
    } as any)

    expect(appended).toHaveLength(1)
    expect(appended[0]?.tools).toHaveLength(1)
    expect(appended[0]?.toolTokens).toBeGreaterThan(0)
  })

  it('ignores fallback reasoning.available when streamed reasoning already exists', () => {
    const appended: Msg[] = []
    const streamed = 'short streamed reasoning'
    const fallback = 'x'.repeat(400)

    const onEvent = createGatewayEventHandler(buildCtx(appended))

    onEvent({ payload: { text: streamed }, type: 'reasoning.delta' } as any)
    onEvent({ payload: { text: fallback }, type: 'reasoning.available' } as any)
    onEvent({ payload: { text: 'final answer' }, type: 'message.complete' } as any)

    expect(appended).toHaveLength(1)
    expect(appended[0]?.thinking).toBe(streamed)
    expect(appended[0]?.thinkingTokens).toBe(estimateTokensRough(streamed))
  })

  it('uses message.complete reasoning when no streamed reasoning ref', () => {
    const appended: Msg[] = []
    const fromServer = 'recovered from last_reasoning'

    const onEvent = createGatewayEventHandler(buildCtx(appended))

    onEvent({ payload: { reasoning: fromServer, text: 'final answer' }, type: 'message.complete' } as any)

    expect(appended).toHaveLength(1)
    expect(appended[0]?.thinking).toBe(fromServer)
    expect(appended[0]?.thinkingTokens).toBe(estimateTokensRough(fromServer))
  })

  it('caps streamed reasoning retained in memory during long turns', () => {
    const appended: Msg[] = []
    const onEvent = createGatewayEventHandler(buildCtx(appended))
    const huge = 'r'.repeat(MAX_REASONING_CHARS + 5000)

    onEvent({ payload: { text: huge }, type: 'reasoning.delta' } as any)
    onEvent({ payload: { text: 'final answer' }, type: 'message.complete' } as any)

    expect(appended).toHaveLength(1)
    expect(appended[0]?.thinking?.length).toBeLessThanOrEqual(MAX_REASONING_CHARS)
    expect(appended[0]?.thinking?.startsWith('...[truncated]\n')).toBe(true)
    expect(appended[0]?.thinking?.endsWith('r'.repeat(64))).toBe(true)
  })

  it('caps live stream segments retained for a single turn', () => {
    const appended: Msg[] = []
    const onEvent = createGatewayEventHandler(buildCtx(appended))

    for (let i = 0; i < MAX_STREAM_SEGMENTS + 5; i += 1) {
      onEvent({ payload: { text: `segment-${i}` }, type: 'message.delta' } as any)
      onEvent({
        payload: { context: `ctx-${i}`, name: 'search', tool_id: `tool-${i}` },
        type: 'tool.start'
      } as any)
      onEvent({
        payload: { summary: `done-${i}`, tool_id: `tool-${i}` },
        type: 'tool.complete'
      } as any)
    }

    const state = getTurnState()

    expect(state.streamSegments).toHaveLength(MAX_STREAM_SEGMENTS)
    expect(state.streamSegments[0]?.text).toBe('segment-5')
    expect(state.streamSegments.at(-1)?.text).toBe(`segment-${MAX_STREAM_SEGMENTS + 4}`)
  })

  it('shows setup panel for missing provider startup error', () => {
    const appended: Msg[] = []
    const onEvent = createGatewayEventHandler(buildCtx(appended))

    onEvent({
      payload: {
        message:
          'agent init failed: No LLM provider configured. Run `hermes model` to select a provider, or run `hermes setup` for first-time configuration.'
      },
      type: 'error'
    } as any)

    expect(appended).toHaveLength(1)
    expect(appended[0]).toMatchObject({
      kind: 'panel',
      panelData: { title: 'Setup Required' },
      role: 'system'
    })
  })
})
