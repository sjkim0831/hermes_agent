import { afterEach, describe, expect, it } from 'vitest'

import { resolveHermesLaunch } from './externalCli.js'

describe('resolveHermesLaunch', () => {
  const oldHermesBin = process.env.HERMES_BIN
  const oldHermesPython = process.env.HERMES_PYTHON
  const oldPython = process.env.PYTHON

  afterEach(() => {
    if (oldHermesBin === undefined) {
      delete process.env.HERMES_BIN
    } else {
      process.env.HERMES_BIN = oldHermesBin
    }

    if (oldHermesPython === undefined) {
      delete process.env.HERMES_PYTHON
    } else {
      process.env.HERMES_PYTHON = oldHermesPython
    }

    if (oldPython === undefined) {
      delete process.env.PYTHON
    } else {
      process.env.PYTHON = oldPython
    }
  })

  it('prefers HERMES_BIN when available', () => {
    process.env.HERMES_BIN = '/tmp/hermes-bin'
    process.env.HERMES_PYTHON = '/tmp/python'

    expect(resolveHermesLaunch(['auth'])).toEqual({
      args: ['auth'],
      file: '/tmp/hermes-bin'
    })
  })

  it('falls back to HERMES_PYTHON module launch in dev installs', () => {
    delete process.env.HERMES_BIN
    process.env.HERMES_PYTHON = '/tmp/python'

    expect(resolveHermesLaunch(['auth'])).toEqual({
      args: ['-m', 'hermes_cli.main', 'auth'],
      file: '/tmp/python'
    })
  })

  it('uses PYTHON when HERMES_PYTHON is not set', () => {
    delete process.env.HERMES_BIN
    delete process.env.HERMES_PYTHON
    process.env.PYTHON = '/tmp/python3'

    expect(resolveHermesLaunch(['model'])).toEqual({
      args: ['-m', 'hermes_cli.main', 'model'],
      file: '/tmp/python3'
    })
  })

  it('keeps bare hermes fallback when no explicit runtime is available', () => {
    delete process.env.HERMES_BIN
    delete process.env.HERMES_PYTHON
    delete process.env.PYTHON

    expect(resolveHermesLaunch(['setup'])).toEqual({
      args: ['setup'],
      file: 'hermes'
    })
  })
})
