import { Box, Text, useInput } from '@hermes/ink'
import { useEffect, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import type { ModelOptionProvider, ModelOptionsResponse, RoutingStatusResponse } from '../gatewayTypes.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

const VISIBLE = 12

const pageOffset = (count: number, sel: number) => Math.max(0, Math.min(sel - Math.floor(VISIBLE / 2), count - VISIBLE))

type Role = 'executor' | 'planner'
interface RoutingProviderOption {
  models?: string[]
  name: string
  slug: string
}

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
  const [modelIdx, setModelIdx] = useState(0)
  const [stage, setStage] = useState<'role' | 'provider' | 'model'>('role')

  const roles: Role[] = ['executor', 'planner']
  const role = roles[roleIdx] ?? 'executor'
  const customProviders = providers.filter(provider => provider.slug.startsWith('custom:'))
  const plannerOptions: RoutingProviderOption[] = [{ name: 'Planner Off', slug: '', models: [] }, ...customProviders]
  const providerOptions: RoutingProviderOption[] = role === 'planner' ? plannerOptions : customProviders
  const provider = providerOptions[providerIdx]
  const models = provider?.models ?? []
  const plannerEnabled = Boolean(routing.planner?.provider)
  const modeLabel = plannerEnabled ? 'Dual routing active' : 'Single-provider mode'

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
        const maxProviderIdx = role === 'planner' ? nextProviders.length : Math.max(0, nextProviders.length - 1)
        setProviderIdx(current => Math.min(current, maxProviderIdx))
        setModelIdx(0)
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

    if (role === 'planner' && !provider.slug) {
      setBusy('disabling planner…')
      setErr('')
      setNotice('')

      gw.request<RoutingStatusResponse>('routing.set', {
        model: '',
        provider: '',
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
          setNotice('planner disabled · single-provider mode')
          setStage('role')
        })
        .catch((e: unknown) => {
          setBusy('')
          setErr(rpcErrorMessage(e))
        })

      return
    }

    const selectedModel = models[modelIdx] ?? ''

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
          setModelIdx(0)
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
      if (stage === 'model') {
        setStage('provider')
        setModelIdx(0)
        return
      }
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
        setProviderIdx(0)
        return
      }
      return
    }

    if (stage === 'provider') {
      if (key.upArrow && providerIdx > 0) {
        setProviderIdx(v => v - 1)
        return
      }
      if (key.downArrow && providerIdx < providerOptions.length - 1) {
        setProviderIdx(v => v + 1)
        return
      }
      if (key.return) {
        if (role === 'planner' && !provider?.slug) {
          applySelection()
          return
        }
        setStage('model')
        setModelIdx(0)
        return
      }

      const n = ch === '0' ? 10 : parseInt(ch, 10)
      if (!Number.isNaN(n) && n >= 1 && n <= Math.min(10, providerOptions.length)) {
        const off = pageOffset(providerOptions.length, providerIdx)
        const row = providerOptions[off + n - 1]
        if (row) {
          setProviderIdx(off + n - 1)
        }
      }
      return
    }

    if (key.upArrow && modelIdx > 0) {
      setModelIdx(v => v - 1)
      return
    }
    if (key.downArrow && modelIdx < models.length - 1) {
      setModelIdx(v => v + 1)
      return
    }
    if (key.return) {
      applySelection()
      return
    }

    const n = ch === '0' ? 10 : parseInt(ch, 10)
    if (!Number.isNaN(n) && n >= 1 && n <= Math.min(10, models.length)) {
      const off = pageOffset(models.length, modelIdx)
      const row = models[off + n - 1]
      if (row) {
        setModelIdx(off + n - 1)
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
        <Text color={plannerEnabled ? t.color.ok : t.color.dim}>{modeLabel}</Text>
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

  const off = pageOffset(providerOptions.length, providerIdx)

  if (stage === 'provider') {
    return (
      <Box flexDirection="column" width={92}>
        <Text bold color={t.color.amber}>
          Select {role === 'executor' ? 'Executor' : 'Planner'} Provider
        </Text>
        <Text color={plannerEnabled ? t.color.ok : t.color.dim}>mode: {modeLabel}</Text>
        <Text color={t.color.dim}>
          current {role}: {(role === 'executor' ? routing.executor?.provider : routing.planner?.provider) || '(unset)'}
        </Text>
        {err ? <Text color={t.color.label}>error: {err}</Text> : null}
        {notice ? <Text color={t.color.ok}>{notice}</Text> : null}
        {off > 0 ? <Text color={t.color.dim}> ↑ {off} more</Text> : null}

        {providerOptions.slice(off, off + VISIBLE).map((item, i) => {
          const idx = off + i
          const active = idx === providerIdx
          const modelSummary = item.slug ? `${item.models?.length ?? 0} models` : 'disable planner pre-routing'

          return (
            <Text color={active ? t.color.cornsilk : t.color.dim} key={`${item.slug}:${modelSummary}`}>
              {active ? '▸ ' : '  '}
              {i + 1}. {item.name} · {modelSummary}
            </Text>
          )
        })}

        {off + VISIBLE < providerOptions.length ? (
          <Text color={t.color.dim}> ↓ {providerOptions.length - off - VISIBLE} more</Text>
        ) : null}
        <Text color={t.color.dim}>Enter choose model · 1-9,0 quick · Esc back</Text>
      </Box>
    )
  }

  const modelOff = pageOffset(models.length, modelIdx)

  return (
    <Box flexDirection="column" width={92}>
      <Text bold color={t.color.amber}>
        Select {role === 'executor' ? 'Executor' : 'Planner'} Model
      </Text>
      <Text color={plannerEnabled ? t.color.ok : t.color.dim}>mode: {modeLabel}</Text>
      <Text color={t.color.dim}>{provider?.name || '(unset provider)'}</Text>
      {err ? <Text color={t.color.label}>error: {err}</Text> : null}
      {notice ? <Text color={t.color.ok}>{notice}</Text> : null}
      {!models.length ? <Text color={t.color.dim}>no models listed for this provider</Text> : null}
      {modelOff > 0 ? <Text color={t.color.dim}> ↑ {modelOff} more</Text> : null}

      {models.slice(modelOff, modelOff + VISIBLE).map((item, i) => {
        const idx = modelOff + i
        const active = idx === modelIdx

        return (
          <Text color={modelIdx === idx ? t.color.cornsilk : t.color.dim} key={`${provider?.slug ?? 'prov'}:${item}`}>
            {active ? '▸ ' : '  '}
            {i + 1}. {item}
          </Text>
        )
      })}

      {modelOff + VISIBLE < models.length ? (
        <Text color={t.color.dim}> ↓ {models.length - modelOff - VISIBLE} more</Text>
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
