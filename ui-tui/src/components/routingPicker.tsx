import { Box, Text, useInput } from '@hermes/ink'
import { useEffect, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import type { ModelOptionProvider, ModelOptionsResponse, RoutingStatusResponse } from '../gatewayTypes.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

const VISIBLE = 12

const pageOffset = (count: number, sel: number) => Math.max(0, Math.min(sel - Math.floor(VISIBLE / 2), count - VISIBLE))

type Role = 'executor' | 'planner'

interface RoutingState {
  executor?: { model?: string; provider?: string; warning?: string }
  planner?: { model?: string; provider?: string }
}

export function RoutingPicker({ gw, onClose, sessionId, t }: RoutingPickerProps) {
  const [providers, setProviders] = useState<ModelOptionProvider[]>([])
  const [routing, setRouting] = useState<RoutingState>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')
  const [roleIdx, setRoleIdx] = useState(0)
  const [providerIdx, setProviderIdx] = useState(0)
  const [stage, setStage] = useState<'role' | 'provider'>('role')

  const roles: Role[] = ['executor', 'planner']
  const role = roles[roleIdx] ?? 'executor'
  const customProviders = providers.filter(provider => provider.slug.startsWith('custom:'))
  const provider = customProviders[providerIdx]

  const load = () => {
    setLoading(true)
    setErr('')

    Promise.all([
      gw.request<ModelOptionsResponse>('model.options', sessionId ? { session_id: sessionId } : {}),
      gw.request<RoutingStatusResponse>('config.get', sessionId ? { key: 'routing', session_id: sessionId } : { key: 'routing' })
    ])
      .then(([modelsRaw, routingRaw]) => {
        const models = asRpcResult<ModelOptionsResponse>(modelsRaw)
        const routingConfig = asRpcResult<RoutingStatusResponse>(routingRaw)

        if (!models) {
          setErr('invalid response: model.options')
          setLoading(false)

          return
        }

        const nextProviders = (models.providers ?? []).filter(provider => provider.slug.startsWith('custom:'))
        setProviders(nextProviders)
        setRouting(routingConfig ?? {})
        setProviderIdx(current => Math.min(current, Math.max(0, nextProviders.length - 1)))
        setLoading(false)
      })
      .catch((e: unknown) => {
        setErr(rpcErrorMessage(e))
        setLoading(false)
      })
  }

  useEffect(() => {
    load()
  }, [gw, sessionId])

  const applySelection = () => {
    if (!provider || busy) {
      return
    }

    const selectedModel = provider.models?.[0] ?? ''

    if (!selectedModel) {
      setErr('selected provider has no model')

      return
    }

    setBusy(`updating ${role}…`)
    setErr('')
    setNotice('')

    gw.request<RoutingStatusResponse>('routing.set', {
      model: selectedModel,
      provider: provider.slug,
      role,
      session_id: sessionId
    })
      .then(raw => {
        const result = asRpcResult<RoutingStatusResponse>(raw)
        setBusy('')
        if (!result) {
          setErr('invalid response: routing.set')
          return
        }
        setRouting(result)
        setNotice(`${role}: ${provider.name} · ${selectedModel}`)
        if (role === 'executor') {
          onClose()
          return
        }
        setStage('role')
      })
      .catch((e: unknown) => {
        setBusy('')
        setErr(rpcErrorMessage(e))
      })
  }

  useInput((ch, key) => {
    if (loading || busy) {
      if (key.escape) {
        onClose()
      }
      return
    }

    if (key.escape) {
      if (stage === 'provider') {
        setStage('role')
        return
      }
      onClose()
      return
    }

    if (stage === 'role') {
      if (key.upArrow && roleIdx > 0) {
        setRoleIdx(v => v - 1)
        return
      }
      if (key.downArrow && roleIdx < roles.length - 1) {
        setRoleIdx(v => v + 1)
        return
      }
      if (key.return) {
        setStage('provider')
        return
      }
      return
    }

    if (key.upArrow && providerIdx > 0) {
      setProviderIdx(v => v - 1)
      return
    }
    if (key.downArrow && providerIdx < customProviders.length - 1) {
      setProviderIdx(v => v + 1)
      return
    }
    if (key.return) {
      applySelection()
      return
    }

    const n = ch === '0' ? 10 : parseInt(ch, 10)
    if (!Number.isNaN(n) && n >= 1 && n <= Math.min(10, customProviders.length)) {
      const off = pageOffset(customProviders.length, providerIdx)
      const row = customProviders[off + n - 1]
      if (row) {
        setProviderIdx(off + n - 1)
      }
    }
  })

  if (loading) {
    return <Text color={t.color.dim}>loading routing…</Text>
  }

  if (err && !customProviders.length) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.label}>error: {err}</Text>
        <Text color={t.color.dim}>Esc to cancel</Text>
      </Box>
    )
  }

  if (!customProviders.length) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.dim}>no custom providers available</Text>
        <Text color={t.color.dim}>run the Hermes provider installer first</Text>
      </Box>
    )
  }

  if (stage === 'role') {
    return (
      <Box flexDirection="column" width={84}>
        <Text bold color={t.color.amber}>
          Planner / Executor Routing
        </Text>
        {busy ? <Text color={t.color.amber}>{busy}</Text> : null}
        {err ? <Text color={t.color.label}>error: {err}</Text> : null}
        {notice ? <Text color={t.color.ok}>{notice}</Text> : null}

        <Box flexDirection="column" marginTop={1}>
          <Text color={roleIdx === 0 ? t.color.cornsilk : t.color.dim}>
            {roleIdx === 0 ? '▸ ' : '  '}
            1. Executor · {routing.executor?.provider || '(unset)'} · {routing.executor?.model || '(unset)'}
          </Text>
          <Text color={roleIdx === 1 ? t.color.cornsilk : t.color.dim}>
            {roleIdx === 1 ? '▸ ' : '  '}
            2. Planner · {routing.planner?.provider || '(disabled)'} · {routing.planner?.model || '(unset)'}
          </Text>
        </Box>

        <Text color={t.color.dim} marginTop={1}>
          Executor = real Codex work model
        </Text>
        <Text color={t.color.dim}>Planner = pre-routing model that rewrites the task before execution</Text>
        <Text color={t.color.dim}>↑/↓ select · Enter choose provider · Esc close</Text>
      </Box>
    )
  }

  const off = pageOffset(customProviders.length, providerIdx)

  return (
    <Box flexDirection="column" width={92}>
      <Text bold color={t.color.amber}>
        Select {role === 'executor' ? 'Executor' : 'Planner'} Provider
      </Text>
      <Text color={t.color.dim}>
        current {role}: {(role === 'executor' ? routing.executor?.provider : routing.planner?.provider) || '(unset)'}
      </Text>
      {err ? <Text color={t.color.label}>error: {err}</Text> : null}
      {notice ? <Text color={t.color.ok}>{notice}</Text> : null}
      {off > 0 ? <Text color={t.color.dim}> ↑ {off} more</Text> : null}

      {customProviders.slice(off, off + VISIBLE).map((item, i) => {
        const idx = off + i
        const active = idx === providerIdx
        const model = item.models?.[0] ?? '(no model)'

        return (
          <Text color={active ? t.color.cornsilk : t.color.dim} key={`${item.slug}:${model}`}>
            {active ? '▸ ' : '  '}
            {i + 1}. {item.name} · {model}
          </Text>
        )
      })}

      {off + VISIBLE < customProviders.length ? (
        <Text color={t.color.dim}> ↓ {customProviders.length - off - VISIBLE} more</Text>
      ) : null}
      <Text color={t.color.dim}>Enter apply · 1-9,0 quick · Esc back</Text>
    </Box>
  )
}

interface RoutingPickerProps {
  gw: GatewayClient
  onClose: () => void
  sessionId: null | string
  t: Theme
}
