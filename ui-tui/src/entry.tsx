#!/usr/bin/env node
// Order matters: paint banner + spawn python before loading @hermes/ink.
import { bootBanner } from './bootBanner.js'
import fs from 'node:fs'

import { GatewayClient } from './gatewayClient.js'

if (!process.stdin.isTTY) {
  console.log('hermes-tui: no TTY')
  process.exit(0)
}

const saneDimension = (value: number | undefined, min: number, max: number, fallback: number) => {
  const n = Number(value)

  return Number.isFinite(n) && n >= min && n <= max ? Math.floor(n) : fallback
}

const normalizeTtySize = () => {
  const columns = saneDimension(process.stdout.columns, 20, 320, 120)
  const rows = saneDimension(process.stdout.rows, 8, 120, 30)

  try {
    Object.defineProperty(process.stdout, 'columns', { configurable: true, get: () => columns })
    Object.defineProperty(process.stdout, 'rows', { configurable: true, get: () => rows })
  } catch {
    process.stdout.columns = columns
    process.stdout.rows = rows
  }
}

normalizeTtySize()

const startMemoryProbe = () => {
  const path = process.env.HERMES_TUI_MEMORY_LOG || '/tmp/hermes-tui-memory.log'

  try {
    fs.appendFileSync(path, '--- hermes-tui start ' + new Date().toISOString() + ' pid=' + process.pid + ' ---\n')
  } catch {
    return
  }

  setInterval(() => {
    const m = process.memoryUsage()
    const line = [
      new Date().toISOString(),
      'pid=' + process.pid,
      'rss=' + Math.round(m.rss / 1024 / 1024) + 'MiB',
      'heapUsed=' + Math.round(m.heapUsed / 1024 / 1024) + 'MiB',
      'heapTotal=' + Math.round(m.heapTotal / 1024 / 1024) + 'MiB',
      'external=' + Math.round(m.external / 1024 / 1024) + 'MiB',
      'arrayBuffers=' + Math.round(m.arrayBuffers / 1024 / 1024) + 'MiB',
      'cols=' + process.stdout.columns,
      'rows=' + process.stdout.rows
    ].join(' ')

    try {
      fs.appendFileSync(path, line + '\n')
    } catch {}
  }, Number(process.env.HERMES_TUI_MEMORY_PROBE_MS || 30000)).unref()
}

if (process.env.HERMES_TUI_MEMORY_PROBE === '1') {
  startMemoryProbe()
}
process.stdout.write(bootBanner())

const gw = new GatewayClient()
gw.start()

const [{ render }, { App }] = await Promise.all([import('@hermes/ink'), import('./app.js')])

render(<App gw={gw} />, { exitOnCtrlC: false })
