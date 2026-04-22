import { Box, Text, useInput } from '@hermes/ink'
import { useEffect, useMemo, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import type { ModelOptionProvider, ModelOptionsResponse, RoutingStatusResponse } from '../gatewayTypes.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

const VISIBLE = 12

type Role = 'executor' | 'planner'
type Stage = 'role' | 'provider' | 'model'

interface RoutingProviderOption {
  models?: string[]
  name: string
  slug: string
}

interface RoutingState {
  executor?: { model?: string; provider?: string; warning?: string }
  planner?: { model?: string; provider?: string }
}

interface RoutingPickerProps {
  gw: GatewayClient
  onClose: () => void
  sessionId: null | string
  t: Theme
}

interface StepBaseProps {
  err: string
  modeLabel: string
  notice: string
  plannerEnabled: boolean
  t: Theme
}

interface RoleStepProps extends StepBaseProps {
  roleIdx: number
  routing: RoutingState
}

interface ProviderStepProps extends StepBaseProps {
  currentProvider: string
  providerIdx: number
  providers: RoutingProviderOption[]
}

interface ModelStepProps extends StepBaseProps {
  modelIdx: number
  models: string[]
  providerName: string
  role: Role
}

const pageOffset = (count: number, sel: number) => Math.max(0, Math.min(sel - Math.floor(VISIBLE / 2), count - VISIBLE))

const quickPickIndex = (ch: string, count: number, sel: number) => {
  const n = ch === '0' ? 10 : parseInt(ch, 10)

  if (Number.isNaN(n) || n < 1 || n > Math.min(10, count)) {
    return null
  }

  return pageOffset(count, sel) + n - 1
}

function roleLabel(role: Role) {
  return role === 'executor' ? 'Executor' : 'Planner'
}

function modeLabelFor(routing: RoutingState) {
  return routing.planner?.provider ? 'Dual routing active' : 'Single-provider mode'
}

function providerOptionsFor(role: Role, providers: ModelOptionProvider[]): RoutingProviderOption[] {
  const customProviders = providers.filter(provider => provider.slug.startsWith('custom:'))
  if (role === 'planner') {
    return [{ name: 'Planner Off', slug: '', models: [] }, ...customProviders]
  }
  return customProviders
}

function renderListWindow<T>(items: T[], selectedIndex: number) {
  const off = pageOffset(items.length, selectedIndex)
  return { off, visible: items.slice(off, off + VISIBLE) }
}

function RoleStep({ err, modeLabel, notice, plannerEnabled, roleIdx, routing, t }: RoleStepProps) {
  return (
    <Box flexDirection="column" width={84}>
      <Text bold color={t.color.amber}>
        Planner / Executor Routing
      </Text>
      <Text color={plannerEnabled ? t.color.ok : t.color.dim}>{modeLabel}</Text>
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

function ProviderStep({ currentProvider, err, modeLabel, notice, plannerEnabled, providerIdx, providers, t }: ProviderStepProps) {
  const { off, visible } = renderListWindow(providers, providerIdx)

  return (
    <Box flexDirection="column" width={92}>
      <Text bold color={t.color.amber}>
        Select Provider
      </Text>
      <Text color={plannerEnabled ? t.color.ok : t.color.dim}>mode: {modeLabel}</Text>
      <Text color={t.color.dim}>current provider: {currentProvider || '(unset)'}</Text>
      {err ? <Text color={t.color.label}>error: {err}</Text> : null}
      {notice ? <Text color={t.color.ok}>{notice}</Text> : null}
      {off > 0 ? <Text color={t.color.dim}> ↑ {off} more</Text> : null}

      {visible.map((item, i) => {
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

      {off + VISIBLE < providers.length ? <Text color={t.color.dim}> ↓ {providers.length - off - VISIBLE} more</Text> : null}
      <Text color={t.color.dim}>Enter choose model · 1-9,0 quick · Esc back</Text>
    </Box>
  )
}

function ModelStep({ err, modeLabel, models, modelIdx, notice, plannerEnabled, providerName, role, t }: ModelStepProps) {
  const { off, visible } = renderListWindow(models, modelIdx)

  return (
    <Box flexDirection="column" width={92}>
      <Text bold color={t.color.amber}>
        Select {roleLabel(role)} Model
      </Text>
      <Text color={plannerEnabled ? t.color.ok : t.color.dim}>mode: {modeLabel}</Text>
      <Text color={t.color.dim}>{providerName || '(unset provider)'}</Text>
      {err ? <Text color={t.color.label}>error: {err}</Text> : null}
      {notice ? <Text color={t.color.ok}>{notice}</Text> : null}
      {!models.length ? <Text color={t.color.dim}>no models listed for this provider</Text> : null}
      {off > 0 ? <Text color={t.color.dim}> ↑ {off} more</Text> : null}

      {visible.map((item, i) => {
        const idx = off + i
        const active = idx === modelIdx

        return (
          <Text color={active ? t.color.cornsilk : t.color.dim} key={`${providerName}:${item}`}>
            {active ? '▸ ' : '  '}
            {i + 1}. {item}
          </Text>
        )
      })}

      {off + VISIBLE < models.length ? <Text color={t.color.dim}> ↓ {models.length - off - VISIBLE} more</Text> : null}
      <Text color={t.color.dim}>Enter apply · 1-9,0 quick · Esc back</Text>
    </Box>
  )
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
  const [stage, setStage] = useState<Stage>('role')

  const roles: Role[] = ['executor', 'planner']
  const role = roles[roleIdx] ?? 'executor'

  const plannerEnabled = Boolean(routing.planner?.provider)
  const modeLabel = modeLabelFor(routing)

  const providerOptions = useMemo(() => providerOptionsFor(role, providers), [role, providers])
  const provider = providerOptions[providerIdx]
  const models = provider?.models ?? []
  const hasCustomProviders = providers.some(provider => provider.slug.startsWith('custom:'))

  const load = () => {
    setLoading(true)
    setErr('')

    Promise.all([
      gw.request<ModelOptionsResponse>('model.options', sessionId ? { session_id: sessionId } : {}),
      gw.request<RoutingStatusResponse>('config.get', sessionId ? { key: 'routing', session_id: sessionId } : { key: 'routing' })
    ])
      .then(([modelsRaw, routingRaw]) => {
        const modelOptions = asRpcResult<ModelOptionsResponse>(modelsRaw)
        const routingConfig = asRpcResult<RoutingStatusResponse>(routingRaw)

        if (!modelOptions) {
          setErr('invalid response: model.options')
          setLoading(false)
          return
        }

        const nextProviders = modelOptions.providers ?? []
        setProviders(nextProviders)
        setRouting(routingConfig ?? {})
        setProviderIdx(0)
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

  const resetForStage = (nextStage: Stage) => {
    if (nextStage !== 'model') {
      setModelIdx(0)
    }
    if (nextStage === 'role') {
      setProviderIdx(0)
    }
    setStage(nextStage)
  }

  const applyPlannerOff = () => {
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
        resetForStage('role')
      })
      .catch((e: unknown) => {
        setBusy('')
        setErr(rpcErrorMessage(e))
      })
  }

  const applyModelSelection = () => {
    if (!provider || busy) {
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
        resetForStage('role')
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
        resetForStage('provider')
        return
      }
      if (stage === 'provider') {
        resetForStage('role')
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
        resetForStage('provider')
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
          applyPlannerOff()
          return
        }
        resetForStage('model')
        return
      }

      const picked = quickPickIndex(ch, providerOptions.length, providerIdx)
      if (picked !== null && providerOptions[picked]) {
        setProviderIdx(picked)
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
      applyModelSelection()
      return
    }

    const picked = quickPickIndex(ch, models.length, modelIdx)
    if (picked !== null && models[picked]) {
      setModelIdx(picked)
    }
  })

  if (loading) {
    return <Text color={t.color.dim}>loading routing…</Text>
  }

  if (err && !hasCustomProviders) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.label}>error: {err}</Text>
        <Text color={t.color.dim}>Esc to cancel</Text>
      </Box>
    )
  }

  if (!hasCustomProviders) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.dim}>no custom providers available</Text>
        <Text color={t.color.dim}>run the Hermes provider installer first</Text>
      </Box>
    )
  }

  if (stage === 'role') {
    return (
      <RoleStep
        err={err}
        modeLabel={modeLabel}
        notice={notice}
        plannerEnabled={plannerEnabled}
        roleIdx={roleIdx}
        routing={routing}
        t={t}
      />
    )
  }

  if (stage === 'provider') {
    return (
      <ProviderStep
        currentProvider={(role === 'executor' ? routing.executor?.provider : routing.planner?.provider) || '(unset)'}
        err={err}
        modeLabel={modeLabel}
        notice={notice}
        plannerEnabled={plannerEnabled}
        providerIdx={providerIdx}
        providers={providerOptions}
        t={t}
      />
    )
  }

  return (
    <ModelStep
      err={err}
      modeLabel={modeLabel}
      modelIdx={modelIdx}
      models={models}
      notice={notice}
      plannerEnabled={plannerEnabled}
      providerName={provider?.name || ''}
      role={role}
      t={t}
    />
  )
}
