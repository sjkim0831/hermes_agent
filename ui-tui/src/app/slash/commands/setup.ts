import { withInkSuspended } from '@hermes/ink'

import { launchHermesCommand, launchHermesOrchestratorCaptured, launchHermesOrchestratorStreaming } from '../../../lib/externalCli.js'
import type { LaunchResult } from '../../../lib/externalCli.js'
import { patchOverlayState } from '../../overlayStore.js'
import { runExternalSetup } from '../../setupHandoff.js'
import type { SlashCommand } from '../types.js'

export const setupCommands: SlashCommand[] = [
  {
    aliases: ['account'],
    help: 'manage saved provider accounts / API keys',
    name: 'auth',
    run: () => patchOverlayState({ authPicker: true })
  },
  {
    help: 'choose planner and executor providers separately',
    name: 'routing',
    run: () => patchOverlayState({ routingPicker: true })
  },
  {
    help: 'run the LangGraph Codex orchestrator for the current task',
    name: 'orchestrate',
    usage: '/orchestrate [--dry-run] <task>',
    run: async (arg, ctx) => {
      const trimmed = arg.trim()
      const dryRun = trimmed.startsWith('--dry-run ')
      const task = dryRun ? trimmed.slice('--dry-run'.length).trim() : trimmed

      if (!task) {
        ctx.transcript.sys('usage: /orchestrate [--dry-run] <task>')

        return
      }

      ctx.transcript.sys(`launching \`hermes-orchestrator ${dryRun ? '--dry-run ' : ''}${task}\`…`)

      const result: LaunchResult = dryRun
        ? await launchHermesOrchestratorCaptured(['--mode', 'default', '--dry-run', task])
        : await launchHermesOrchestratorStreaming(['--mode', 'default', task], {
            onStderrLine: line => {
              if (!line) {
                return
              }
              ctx.transcript.sys(line)
            }
          })

      if (result.error) {
        ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)

        return
      }

      if (result.code !== 0) {
        const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
        ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Orchestrator Error')

        return
      }

      const output = dryRun ? [result.stdout, result.stderr].filter(Boolean).join('\n').trim() : (result.stdout || '').trim()
      if (output) {
        const long = output.length > 180 || output.split('\n').filter(Boolean).length > 2
        if (long) {
          ctx.transcript.page(output, 'Orchestrator')
        } else {
          ctx.transcript.sys(output)
        }
      } else if (!dryRun && result.stderr?.trim()) {
        ctx.transcript.sys(result.stderr.trim())
      } else {
        ctx.transcript.sys('orchestrator completed with no output')
      }
    }
  },
  {
    help: 'run the LangGraph Codex orchestrator in reduced-shard mode',
    name: 'orchestrate2',
    usage: '/orchestrate2 [--dry-run] <task>',
    run: async (arg, ctx) => {
      const trimmed = arg.trim()
      const dryRun = trimmed.startsWith('--dry-run ')
      const task = dryRun ? trimmed.slice('--dry-run'.length).trim() : trimmed

      if (!task) {
        ctx.transcript.sys('usage: /orchestrate2 [--dry-run] <task>')

        return
      }

      ctx.transcript.sys('launching `hermes-orchestrator --mode reduced ' + (dryRun ? '--dry-run ' : '') + task + '`...')

      const result: LaunchResult = dryRun
        ? await launchHermesOrchestratorCaptured(['--mode', 'reduced', '--dry-run', task])
        : await launchHermesOrchestratorStreaming(['--mode', 'reduced', task], {
            onStderrLine: line => {
              if (!line) {
                return
              }
              ctx.transcript.sys(line)
            }
          })

      if (result.error) {
        ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)

        return
      }

      if (result.code !== 0) {
        const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
        ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Orchestrator Error')

        return
      }

      const output = dryRun ? [result.stdout, result.stderr].filter(Boolean).join('\n').trim() : (result.stdout || '').trim()
      if (output) {
        const long = output.length > 180 || output.split('\n').filter(Boolean).length > 2
        if (long) {
          ctx.transcript.page(output, 'Orchestrator')
        } else {
          ctx.transcript.sys(output)
        }
      } else if (!dryRun && result.stderr?.trim()) {
        ctx.transcript.sys(result.stderr.trim())
      } else {
        ctx.transcript.sys('orchestrator completed with no output')
      }
    }
  },
  {
    help: 'run the LangGraph Codex orchestrator with stage approvals',
    name: 'orchestrate3',
    usage: '/orchestrate3 start <task> | /orchestrate3 continue <checkpoint> | /orchestrate3 retry <checkpoint>',
    run: async (arg, ctx) => {
      const trimmed = arg.trim()
      const parts = trimmed.split(/\s+/).filter(Boolean)
      const action = parts[0] || 'start'

      if (action === 'start') {
        const task = parts.slice(1).join(' ').trim()
        if (!task) {
          ctx.transcript.sys('usage: /orchestrate3 start <task>')
          return
        }

        ctx.transcript.sys('launching `hermes-orchestrator --mode gated ' + task + '`...')

        const result: LaunchResult = await launchHermesOrchestratorCaptured(['--mode', 'gated', task])

        if (result.error) {
          ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)
          return
        }

        if (result.code !== 0) {
          const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
          ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Orchestrator Error')
          return
        }

        const output = [result.stdout, result.stderr].filter(Boolean).join('\n').trim()
        if (output) {
          ctx.transcript.page(output, 'Orchestrator')
        } else {
          ctx.transcript.sys('orchestrator3 start completed with no output')
        }
        return
      }

      if (action === 'continue' || action === 'retry') {
        const checkpoint = parts.slice(1).join(' ').trim()
        if (!checkpoint) {
          ctx.transcript.sys(`usage: /orchestrate3 ${action} <checkpoint>`)
          return
        }

        const launchArgs = ['--mode', 'gated', '--resume', checkpoint]
        if (action === 'retry') {
          launchArgs.push('--retry')
        }

        ctx.transcript.sys('launching `hermes-orchestrator --mode gated ' + action + '`...')

        const result: LaunchResult = await launchHermesOrchestratorStreaming(launchArgs, {
          onStdoutLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          },
          onStderrLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          }
        })

        if (result.error) {
          ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)
          return
        }

        if (result.code !== 0) {
          const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
          ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Orchestrator Error')
          return
        }

        const output = [result.stdout, result.stderr].filter(Boolean).join('\n').trim()
        if (output) {
          ctx.transcript.page(output, 'Orchestrator')
        } else {
          ctx.transcript.sys('orchestrator3 resume completed with no output')
        }
        return
      }

      ctx.transcript.sys('usage: /orchestrate3 start <task> | /orchestrate3 continue <checkpoint> | /orchestrate3 retry <checkpoint>')
    }
  },
  {
    help: 'run the LangGraph Codex orchestrator with strict approvals',
    name: 'orchestrate4',
    usage: '/orchestrate4 start <task> | /orchestrate4 1|2|3 | /orchestrate4 y|n|s | /orchestrate4 continue <checkpoint> | /orchestrate4 retry <checkpoint>',
    run: async (arg, ctx) => {
      const trimmed = arg.trim()
      const parts = trimmed.split(/\s+/).filter(Boolean)
      const action = parts[0] || 'start'
      const actionAlias = {
        '1': 'continue',
        '2': 'retry',
        '3': 'stop',
        yes: 'continue',
        y: 'continue',
        no: 'retry',
        n: 'retry',
        s: 'stop',
        stop: 'stop',
      } as Record<string, string>
      const normalizedAction = actionAlias[action.toLowerCase()] || action

      if (normalizedAction === 'start') {
        const task = parts.slice(1).join(' ').trim()
        if (!task) {
          ctx.transcript.sys('usage: /orchestrate4 start <task>')
          return
        }

        ctx.transcript.sys('launching `hermes-orchestrator --mode strict ' + task + '`...')

        const result: LaunchResult = await launchHermesOrchestratorStreaming(['--mode', 'strict', task], {
          onStdoutLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          },
          onStderrLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          }
        })

        if (result.error) {
          ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)
          return
        }

        if (result.code !== 0) {
          const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
          ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Orchestrator Error')
          return
        }

        const output = [result.stdout, result.stderr].filter(Boolean).join('\n').trim()
        if (output) {
          const pageTitle = /Proceed\?|Approval Required/i.test(output) ? 'Approval' : 'Orchestrator'
          if (pageTitle === 'Approval') {
            ctx.transcript.sys('strict prompt: 1=continue 2=retry 3=stop')
          }
          ctx.transcript.page(output, pageTitle)
        } else {
          ctx.transcript.sys('orchestrate4 start completed with no output')
        }
        return
      }

      if (normalizedAction === 'continue' || normalizedAction === 'retry' || normalizedAction === 'stop') {
        const checkpoint = parts.slice(1).join(' ').trim()
        if (!checkpoint) {
          if (normalizedAction === 'stop') {
            const result: LaunchResult = await launchHermesOrchestratorStreaming(['--mode', 'strict', '--approve', 'stop'], {
              onStdoutLine: line => {
                if (line) ctx.transcript.sys(line)
              },
              onStderrLine: line => {
                if (line) ctx.transcript.sys(line)
              }
            })
            if (result.error) {
              ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)
              return
            }
            const output = [result.stdout, result.stderr].filter(Boolean).join('\n').trim()
            if (output) {
              ctx.transcript.page(output, 'Orchestrator')
            } else {
              ctx.transcript.sys('orchestrate4 stop completed with no output')
            }
            return
          }
          const launchArgs = ['--mode', 'strict', '--approve', normalizedAction]
          ctx.transcript.sys('launching `hermes-orchestrator --mode strict ' + normalizedAction + '`...')
          const result: LaunchResult = await launchHermesOrchestratorStreaming(launchArgs, {
            onStdoutLine: line => {
              if (line) {
                ctx.transcript.sys(line)
              }
            },
            onStderrLine: line => {
              if (line) {
                ctx.transcript.sys(line)
              }
            }
          })
          if (result.error) {
            ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)
            return
          }
          if (result.code !== 0) {
            const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
            ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Orchestrator Error')
            return
          }
          const output = [result.stdout, result.stderr].filter(Boolean).join('\n').trim()
          if (output) {
            ctx.transcript.page(output, 'Approval Required')
          } else {
            ctx.transcript.sys('orchestrate4 response completed with no output')
          }
          return
        }

        const launchArgs = ['--mode', 'strict', '--resume', checkpoint]
        if (normalizedAction === 'retry') {
          launchArgs.push('--retry')
        }

        ctx.transcript.sys('launching `hermes-orchestrator --mode strict ' + normalizedAction + '`...')
        const result: LaunchResult = await launchHermesOrchestratorStreaming(launchArgs, {
          onStdoutLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          },
          onStderrLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          }
        })

        if (result.error) {
          ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)
          return
        }

        if (result.code !== 0) {
          const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
          ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Orchestrator Error')
          return
        }

        const output = [result.stdout, result.stderr].filter(Boolean).join('\n').trim()
        if (output) {
          const pageTitle = /Proceed\?|Approval Required/i.test(output) ? 'Approval' : 'Orchestrator'
          if (pageTitle === 'Approval') {
            ctx.transcript.sys('strict prompt: 1=continue 2=retry 3=stop')
          }
          ctx.transcript.page(output, pageTitle)
        } else {
          ctx.transcript.sys('orchestrate4 resume completed with no output')
        }
        return
      }

      ctx.transcript.sys('usage: /orchestrate4 start <task> | /orchestrate4 1 | /orchestrate4 2 | /orchestrate4 3 | /orchestrate4 continue <checkpoint> | /orchestrate4 retry <checkpoint>')
    }
  },
  {
    help: 'show or reset recorded orchestrator quota usage',
    name: 'quota',
    usage: '/quota [show|reset [all|gemini|cerebras]]',
    run: async (arg, ctx) => {
      const trimmed = arg.trim()
      const parts = trimmed ? trimmed.split(/\s+/) : []
      const action = parts[0] || 'show'
      let launchArgs: string[] = []

      if (action === 'show') {
        launchArgs = ['--quota-show']
      } else if (action === 'reset') {
        const target = parts[1] || 'all'
        if (!['all', 'gemini', 'cerebras'].includes(target)) {
          ctx.transcript.sys('usage: /quota [show|reset [all|gemini|cerebras]]')
          return
        }
        launchArgs = ['--quota-reset', target]
      } else {
        ctx.transcript.sys('usage: /quota [show|reset [all|gemini|cerebras]]')
        return
      }

      ctx.transcript.sys(`launching \`hermes-orchestrator ${launchArgs.join('\n')}\`…`)

      const result: LaunchResult = await launchHermesOrchestratorStreaming(launchArgs, {
          onStdoutLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          },
          onStderrLine: line => {
            if (line) {
              ctx.transcript.sys(line)
            }
          }
        })

      if (result.error) {
        ctx.transcript.sys(`error launching hermes-orchestrator: ${result.error}`)
        return
      }

      if (result.code !== 0) {
        const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim()
        ctx.transcript.page(detail || `hermes-orchestrator exited with code ${result.code}`, 'Quota Error')
        return
      }

      const output = [result.stdout, result.stderr].filter(Boolean).join('\n').trim()
      if (output) {
        ctx.transcript.page(output, 'Quota')
      } else {
        ctx.transcript.sys('quota command completed with no output')
      }
    }
  },
  {
    help: 'configure LLM provider + model (launches `hermes model`)',
    name: 'provider',
    run: (_arg, ctx) =>
      void runExternalSetup({
        args: ['model'],
        ctx,
        done: 'provider updated — starting session…',
        launcher: launchHermesCommand,
        suspend: withInkSuspended
      })
  },
  {
    help: 'run full setup wizard (launches `hermes setup`)',
    name: 'setup',
    run: (arg, ctx) =>
      void runExternalSetup({
        args: ['setup', ...arg.split(/\s+/).filter(Boolean)],
        ctx,
        done: 'setup complete — starting session…',
        launcher: launchHermesCommand,
        suspend: withInkSuspended
      })
  }
]
