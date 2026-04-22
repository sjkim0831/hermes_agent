import { spawn } from 'node:child_process'

export interface LaunchResult {
  code: null | number
  error?: string
  stderr?: string
  stdout?: string
}

interface LaunchSpec {
  args: string[]
  file: string
}

const resolvePythonModuleLaunch = (
  moduleName: string,
  args: string[],
  {
    binaryEnv,
    fallbackBinary
  }: {
    binaryEnv?: string
    fallbackBinary: string
  }
): LaunchSpec => {
  const explicitBinary = binaryEnv ? process.env[binaryEnv]?.trim() : ''

  if (explicitBinary) {
    return { file: explicitBinary, args }
  }

  const hermesPython = process.env.HERMES_PYTHON?.trim() || process.env.PYTHON?.trim()

  if (hermesPython) {
    return { file: hermesPython, args: ['-m', moduleName, ...args] }
  }

  return { file: fallbackBinary, args }
}

export const resolveHermesLaunch = (args: string[]): LaunchSpec => {
  return resolvePythonModuleLaunch('hermes_cli.main', args, { binaryEnv: 'HERMES_BIN', fallbackBinary: 'hermes' })
}

export const launchHermesCommand = (args: string[]): Promise<LaunchResult> =>
  new Promise(resolve => {
    const launch = resolveHermesLaunch(args)
    const child = spawn(launch.file, launch.args, { stdio: 'inherit' })

    child.on('error', err => resolve({ code: null, error: err.message }))
    child.on('exit', code => resolve({ code }))
  })

export const resolveHermesOrchestratorLaunch = (args: string[]): LaunchSpec =>
  resolvePythonModuleLaunch('langgraph_codex_orchestrator.cli', args, {
    binaryEnv: 'HERMES_ORCHESTRATOR_BIN',
    fallbackBinary: 'hermes-orchestrator'
  })

export const launchHermesOrchestratorCommand = (args: string[]): Promise<LaunchResult> =>
  new Promise(resolve => {
    const launch = resolveHermesOrchestratorLaunch(args)
    const child = spawn(launch.file, launch.args, { stdio: 'inherit' })

    child.on('error', err => resolve({ code: null, error: err.message }))
    child.on('exit', code => resolve({ code }))
  })

export const launchHermesOrchestratorCaptured = (args: string[]): Promise<LaunchResult> =>
  new Promise(resolve => {
    const launch = resolveHermesOrchestratorLaunch(args)
    const child = spawn(launch.file, launch.args, { stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = ''
    let stderr = ''

    child.stdout?.on('data', chunk => {
      stdout += String(chunk)
    })
    child.stderr?.on('data', chunk => {
      stderr += String(chunk)
    })

    child.on('error', err => resolve({ code: null, error: err.message, stderr, stdout }))
    child.on('exit', code => resolve({ code, stderr, stdout }))
  })
