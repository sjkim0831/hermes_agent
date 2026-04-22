import { spawn } from 'node:child_process'

export interface LaunchResult {
  code: null | number
  error?: string
  stderr?: string
  stdout?: string
}

interface StreamHandlers {
  onStderrLine?: (line: string) => void
  onStdoutLine?: (line: string) => void
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
    const child = spawn(launch.file, launch.args, {
      stdio: 'inherit',
      env: { ...process.env, HERMES_ORCHESTRATOR_PROGRESS: '1' }
    })

    child.on('error', err => resolve({ code: null, error: err.message }))
    child.on('exit', code => resolve({ code }))
  })

export const launchHermesOrchestratorCaptured = (args: string[]): Promise<LaunchResult> =>
  new Promise(resolve => {
    const launch = resolveHermesOrchestratorLaunch(args)
    const child = spawn(launch.file, launch.args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, HERMES_ORCHESTRATOR_PROGRESS: '1' }
    })
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

export const launchHermesOrchestratorStreaming = (
  args: string[],
  handlers: StreamHandlers = {}
): Promise<LaunchResult> =>
  new Promise(resolve => {
    const launch = resolveHermesOrchestratorLaunch(args)
    const child = spawn(launch.file, launch.args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, HERMES_ORCHESTRATOR_PROGRESS: '1' }
    })
    let stdout = ''
    let stderr = ''
    let stdoutBuf = ''
    let stderrBuf = ''

    const flushLines = (buffer: string, emit?: (line: string) => void) => {
      const parts = buffer.split(/\r?\n/)
      const carry = parts.pop() ?? ''
      for (const line of parts) {
        const trimmed = line.trim()
        if (trimmed && emit) {
          emit(trimmed)
        }
      }
      return carry
    }

    child.stdout?.on('data', chunk => {
      const text = String(chunk)
      stdout += text
      stdoutBuf += text
      stdoutBuf = flushLines(stdoutBuf, handlers.onStdoutLine)
    })
    child.stderr?.on('data', chunk => {
      const text = String(chunk)
      stderr += text
      stderrBuf += text
      stderrBuf = flushLines(stderrBuf, handlers.onStderrLine)
    })

    child.on('error', err => resolve({ code: null, error: err.message, stderr, stdout }))
    child.on('close', code => {
      const pendingStdout = stdoutBuf.trim()
      const pendingStderr = stderrBuf.trim()
      if (pendingStdout && handlers.onStdoutLine) {
        handlers.onStdoutLine(pendingStdout)
      }
      if (pendingStderr && handlers.onStderrLine) {
        handlers.onStderrLine(pendingStderr)
      }
      resolve({ code, stderr, stdout })
    })
  })
