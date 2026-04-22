import { spawn } from 'node:child_process'

export interface LaunchResult {
  code: null | number
  error?: string
}

interface LaunchSpec {
  args: string[]
  file: string
}

export const resolveHermesLaunch = (args: string[]): LaunchSpec => {
  const hermesBin = process.env.HERMES_BIN?.trim()

  if (hermesBin) {
    return { file: hermesBin, args }
  }

  const hermesPython = process.env.HERMES_PYTHON?.trim() || process.env.PYTHON?.trim()

  if (hermesPython) {
    return { file: hermesPython, args: ['-m', 'hermes_cli.main', ...args] }
  }

  return { file: 'hermes', args }
}

export const launchHermesCommand = (args: string[]): Promise<LaunchResult> =>
  new Promise(resolve => {
    const launch = resolveHermesLaunch(args)
    const child = spawn(launch.file, launch.args, { stdio: 'inherit' })

    child.on('error', err => resolve({ code: null, error: err.message }))
    child.on('exit', code => resolve({ code }))
  })
