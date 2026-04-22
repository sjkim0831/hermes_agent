import { withInkSuspended } from '@hermes/ink'
import { Box, Text, useInput } from '@hermes/ink'
import { useEffect, useMemo, useState } from 'react'

import { providerDisplayNames } from '../domain/providers.js'
import type { GatewayClient } from '../gatewayClient.js'
import type { AuthStatusProvider, AuthStatusResponse } from '../gatewayTypes.js'
import { launchHermesCommand } from '../lib/externalCli.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { TextInput } from './textInput.js'

const VISIBLE = 12
const STRATEGIES = ['fill_first', 'round_robin', 'least_used', 'random'] as const

const pageOffset = (count: number, sel: number) => Math.max(0, Math.min(sel - Math.floor(VISIBLE / 2), count - VISIBLE))

const displaySource = (source: string) => (source.startsWith('manual:') ? source.split(':', 2)[1] ?? source : source)

const statusText = (status?: string) => (status && status.trim() ? status : 'ready')

type ActionRow = { kind: 'action'; label: string; value: 'add-api' | 'add-oauth' | 'reset' | 'strategy' }
type CredentialRow = { kind: 'credential'; id: string }
type DetailRow = ActionRow | CredentialRow

export function AuthPicker({ gw, onClose, t }: AuthPickerProps) {
  const [providers, setProviders] = useState<AuthStatusProvider[]>([])
  const [providerIdx, setProviderIdx] = useState(0)
  const [detailIdx, setDetailIdx] = useState(0)
  const [strategyIdx, setStrategyIdx] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [label, setLabel] = useState('')
  const [stage, setStage] = useState<'api-key' | 'label' | 'provider' | 'strategy'>('provider')

  const load = () => {
    setLoading(true)
    setErr('')

    gw.request<AuthStatusResponse>('auth.status', {})
      .then(raw => {
        const result = asRpcResult<AuthStatusResponse>(raw)

        if (!result) {
          setErr('invalid response: auth.status')
          setLoading(false)

          return
        }

        const next = result.providers ?? []
        setProviders(next)
        setProviderIdx(current => Math.min(current, Math.max(0, next.length - 1)))
        setDetailIdx(0)
        setStrategyIdx(0)
        setLoading(false)
      })
      .catch((e: unknown) => {
        setErr(rpcErrorMessage(e))
        setLoading(false)
      })
  }

  useEffect(() => {
    load()
  }, [gw])

  const provider = providers[providerIdx]
  const names = useMemo(() => providerDisplayNames(providers), [providers])
  const actions: ActionRow[] = [
    { kind: 'action', label: 'Add API key', value: 'add-api' },
    ...(provider?.oauth_capable
      ? ([{ kind: 'action', label: 'Start OAuth / browser login', value: 'add-oauth' }] satisfies ActionRow[])
      : []),
    { kind: 'action', label: 'Reset cooldowns', value: 'reset' },
    { kind: 'action', label: 'Change rotation strategy', value: 'strategy' }
  ]
  const rows: DetailRow[] = [
    ...actions,
    ...(provider?.entries ?? []).map(entry => ({ kind: 'credential', id: entry.id } satisfies CredentialRow))
  ]
  const selectedRow = rows[detailIdx]

  useEffect(() => {
    if (!provider) {
      setStrategyIdx(0)

      return
    }

    const idx = Math.max(
      0,
      STRATEGIES.findIndex(strategy => strategy === provider.strategy)
    )
    setStrategyIdx(idx)
  }, [provider])

  const runAction = (action: ActionRow['value']) => {
    if (!provider || busy) {
      return
    }

    setErr('')
    setNotice('')

    if (action === 'add-api') {
      setApiKey('')
      setLabel('')
      setStage('api-key')

      return
    }

    if (action === 'strategy') {
      setStage('strategy')

      return
    }

    if (action === 'reset') {
      setBusy(`resetting ${provider.slug} cooldowns…`)
      gw.request('auth.reset', { provider: provider.slug })
        .then(() => {
          setBusy('')
          setNotice(`${provider.slug}: cooldowns reset`)
          load()
        })
        .catch((e: unknown) => {
          setBusy('')
          setErr(rpcErrorMessage(e))
        })

      return
    }

    if (action === 'add-oauth') {
      setBusy(`launching ${provider.slug} oauth…`)

      void withInkSuspended(async () => {
        const result = await launchHermesCommand(['auth', 'add', provider.slug, '--type', 'oauth'])

        setBusy('')

        if (result.error) {
          setErr(result.error)

          return
        }

        if (result.code !== 0) {
          setErr(`hermes auth add ${provider.slug} exited with code ${result.code}`)

          return
        }

        setNotice(`${provider.slug}: account added`)
        load()
      })
    }
  }

  const runCredentialSelect = (id: string) => {
    if (!provider || busy) {
      return
    }

    setBusy(`selecting ${provider.slug} credential…`)
    setErr('')
    setNotice('')

    gw.request('auth.select', { provider: provider.slug, target: id })
      .then(() => {
        setBusy('')
        onClose()
      })
      .catch((e: unknown) => {
        setBusy('')
        setErr(rpcErrorMessage(e))
      })
  }

  const runCredentialRemove = (id: string) => {
    if (!provider || busy) {
      return
    }

    setBusy(`removing ${provider.slug} credential…`)
    setErr('')
    setNotice('')

    gw.request('auth.remove', { provider: provider.slug, target: id })
      .then(() => {
        setBusy('')
        setNotice(`${provider.slug}: credential removed`)
        load()
      })
      .catch((e: unknown) => {
        setBusy('')
        setErr(rpcErrorMessage(e))
      })
  }

  const submitApiKey = () => {
    if (!provider || busy) {
      return
    }

    const trimmedKey = apiKey.trim()

    if (!trimmedKey) {
      setErr('API key is required')

      return
    }

    setBusy(`saving ${provider.slug} API key…`)
    setErr('')
    setNotice('')

    gw.request('auth.add_api_key', { api_key: trimmedKey, label: label.trim(), provider: provider.slug })
      .then(() => {
        setBusy('')
        setApiKey('')
        setLabel('')
        setStage('provider')
        setNotice(`${provider.slug}: API key added`)
        load()
      })
      .catch((e: unknown) => {
        setBusy('')
        setErr(rpcErrorMessage(e))
      })
  }

  const submitStrategy = () => {
    if (!provider || busy) {
      return
    }

    const strategy = STRATEGIES[strategyIdx]

    if (!strategy) {
      return
    }

    setBusy(`updating ${provider.slug} strategy…`)
    setErr('')
    setNotice('')

    gw.request('auth.strategy', { provider: provider.slug, strategy })
      .then(() => {
        setBusy('')
        setStage('provider')
        setNotice(`${provider.slug}: strategy set to ${strategy}`)
        load()
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

    if (stage === 'api-key') {
      if (key.escape) {
        setErr('')
        setApiKey('')
        setStage('provider')
      }

      return
    }

    if (stage === 'label') {
      if (key.escape) {
        setErr('')
        setStage('api-key')
      }

      return
    }

    if (stage === 'strategy') {
      if (key.escape) {
        setStage('provider')

        return
      }

      if (key.upArrow && strategyIdx > 0) {
        setStrategyIdx(v => v - 1)

        return
      }

      if (key.downArrow && strategyIdx < STRATEGIES.length - 1) {
        setStrategyIdx(v => v + 1)

        return
      }

      if (key.return) {
        submitStrategy()

        return
      }

      const n = ch === '0' ? 10 : parseInt(ch, 10)

      if (!Number.isNaN(n) && n >= 1 && n <= Math.min(10, STRATEGIES.length)) {
        setStrategyIdx(n - 1)
      }

      return
    }

    if (key.escape) {
      onClose()

      return
    }

    if (!provider) {
      if (key.upArrow && providerIdx > 0) {
        setProviderIdx(v => v - 1)
      }

      if (key.downArrow && providerIdx < providers.length - 1) {
        setProviderIdx(v => v + 1)
      }

      return
    }

    if (key.leftArrow) {
      setDetailIdx(0)

      return
    }

    if (key.upArrow) {
      if (detailIdx > 0) {
        setDetailIdx(v => v - 1)
      } else if (providerIdx > 0) {
        setProviderIdx(v => v - 1)
        setDetailIdx(0)
      }

      return
    }

    if (key.downArrow) {
      if (detailIdx < rows.length - 1) {
        setDetailIdx(v => v + 1)
      } else if (providerIdx < providers.length - 1) {
        setProviderIdx(v => v + 1)
        setDetailIdx(0)
      }

      return
    }

    if (key.return) {
      if (!selectedRow) {
        return
      }

      if (selectedRow.kind === 'action') {
        runAction(selectedRow.value)
      } else {
        runCredentialSelect(selectedRow.id)
      }

      return
    }

    if ((ch.toLowerCase() === 'x' || key.delete) && selectedRow?.kind === 'credential') {
      runCredentialRemove(selectedRow.id)

      return
    }

    const n = ch === '0' ? 10 : parseInt(ch, 10)

    if (!Number.isNaN(n) && n >= 1) {
      const off = pageOffset(rows.length, detailIdx)
      const row = rows[off + n - 1]

      if (row) {
        setDetailIdx(off + n - 1)
      }
    }
  })

  if (loading) {
    return <Text color={t.color.dim}>loading accounts…</Text>
  }

  if (err && !providers.length) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.label}>error: {err}</Text>
        <Text color={t.color.dim}>Esc to cancel</Text>
      </Box>
    )
  }

  if (!providers.length) {
    return (
      <Box flexDirection="column">
        <Text color={t.color.dim}>no providers found</Text>
        <Text color={t.color.dim}>Esc to cancel</Text>
      </Box>
    )
  }

  if (stage === 'api-key') {
    return (
      <Box flexDirection="column" width={Math.max(68, VISIBLE * 6)}>
        <Text bold color={t.color.amber}>
          Add API Key
        </Text>
        <Text color={t.color.dim}>{names[providerIdx] || provider.slug}</Text>
        {err ? <Text color={t.color.label}>error: {err}</Text> : null}
        <Box marginTop={1}>
          <Text color={t.color.label}>{'> '}</Text>
          <TextInput columns={42} mask="*" onChange={setApiKey} onSubmit={() => setStage('label')} value={apiKey} />
        </Box>
        <Text color={t.color.dim}>Enter continue · Esc cancel</Text>
      </Box>
    )
  }

  if (stage === 'label') {
    return (
      <Box flexDirection="column" width={Math.max(68, VISIBLE * 6)}>
        <Text bold color={t.color.amber}>
          Label This Key
        </Text>
        <Text color={t.color.dim}>{names[providerIdx] || provider.slug}</Text>
        {err ? <Text color={t.color.label}>error: {err}</Text> : null}
        <Box marginTop={1}>
          <Text color={t.color.label}>{'> '}</Text>
          <TextInput columns={42} onChange={setLabel} onSubmit={submitApiKey} value={label} />
        </Box>
        <Text color={t.color.dim}>Enter save · Esc back · leave empty for default label</Text>
      </Box>
    )
  }

  if (stage === 'strategy') {
    const off = pageOffset(STRATEGIES.length, strategyIdx)

    return (
      <Box flexDirection="column" width={Math.max(68, VISIBLE * 6)}>
        <Text bold color={t.color.amber}>
          Rotation Strategy
        </Text>
        <Text color={t.color.dim}>{names[providerIdx] || provider.slug}</Text>
        {err ? <Text color={t.color.label}>error: {err}</Text> : null}
        {notice ? <Text color={t.color.ok}>{notice}</Text> : null}

        {STRATEGIES.slice(off, off + VISIBLE).map((strategy, i) => {
          const idx = off + i

          return (
            <Text color={idx === strategyIdx ? t.color.cornsilk : t.color.dim} key={strategy}>
              {idx === strategyIdx ? '▸ ' : '  '}
              {i + 1}. {strategy}
            </Text>
          )
        })}

        <Text color={t.color.dim}>↑/↓ select · Enter save · 1-4 quick · Esc back</Text>
      </Box>
    )
  }

  const providerOff = pageOffset(providers.length, providerIdx)
  const detailOff = pageOffset(rows.length, detailIdx)

  return (
    <Box flexDirection="column" width={Math.max(84, VISIBLE * 8)}>
      <Text bold color={t.color.amber}>
        Account Manager
      </Text>

      {busy ? <Text color={t.color.amber}>{busy}</Text> : null}
      {err ? <Text color={t.color.label}>error: {err}</Text> : null}
      {notice ? <Text color={t.color.ok}>{notice}</Text> : null}

      <Box marginTop={1}>
        <Box flexDirection="column" marginRight={2} width={36}>
          <Text color={t.color.dim}>Providers</Text>
          {providerOff > 0 ? <Text color={t.color.dim}> ↑ {providerOff} more</Text> : null}
          {providers.slice(providerOff, providerOff + VISIBLE).map((item, i) => {
            const idx = providerOff + i

            return (
              <Text color={idx === providerIdx ? t.color.cornsilk : t.color.dim} key={item.slug}>
                {idx === providerIdx ? '▸ ' : '  '}
                {i + 1}. {names[idx] || item.slug} · {item.entry_count ?? item.entries?.length ?? 0}
              </Text>
            )
          })}
          {providerOff + VISIBLE < providers.length ? (
            <Text color={t.color.dim}> ↓ {providers.length - providerOff - VISIBLE} more</Text>
          ) : null}
        </Box>

        <Box flexDirection="column" width={46}>
          <Text color={t.color.dim}>
            {names[providerIdx] || provider.slug} · strategy {provider.strategy || 'fill_first'}
          </Text>
          <Text color={t.color.dim}>
            current {provider.current_label || '(none)'} · {provider.oauth_capable ? 'oauth + api key' : 'api key'}
          </Text>
          {detailOff > 0 ? <Text color={t.color.dim}> ↑ {detailOff} more</Text> : null}

          {rows.slice(detailOff, detailOff + VISIBLE).map((row, i) => {
            const idx = detailOff + i
            const active = idx === detailIdx

            if (row.kind === 'action') {
              return (
                <Text color={active ? t.color.cornsilk : t.color.dim} key={`${provider.slug}:${row.value}`}>
                  {active ? '▸ ' : '  '}
                  {i + 1}. {row.label}
                </Text>
              )
            }

            const entry = provider.entries?.find(item => item.id === row.id)

            if (!entry) {
              return null
            }

            return (
              <Text color={active ? t.color.cornsilk : t.color.dim} key={`${provider.slug}:${entry.id}`}>
                {active ? '▸ ' : '  '}
                {i + 1}. {entry.is_current ? '* ' : '  '}
                {entry.label} · {entry.auth_type} · {displaySource(entry.source)} · {statusText(entry.status)}
              </Text>
            )
          })}

          {detailOff + VISIBLE < rows.length ? (
            <Text color={t.color.dim}> ↓ {rows.length - detailOff - VISIBLE} more</Text>
          ) : null}
        </Box>
      </Box>

      <Text color={t.color.dim}>Enter run/select · x/Delete remove selected credential · Esc close</Text>
    </Box>
  )
}

interface AuthPickerProps {
  gw: GatewayClient
  onClose: () => void
  t: Theme
}
