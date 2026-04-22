import { withInkSuspended } from '@hermes/ink'

import { launchHermesCommand, launchHermesOrchestratorCaptured } from '../../../lib/externalCli.js'
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

      let result: LaunchResult = { code: null }

      await withInkSuspended(async () => {
        result = await launchHermesOrchestratorCaptured([...(dryRun ? ['--dry-run'] : []), task])
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
        const long = output.length > 180 || output.split('\n').filter(Boolean).length > 2
        if (long) {
          ctx.transcript.page(output, 'Orchestrator')
        } else {
          ctx.transcript.sys(output)
        }
      } else {
        ctx.transcript.sys('orchestrator completed with no output')
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
