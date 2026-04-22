import { withInkSuspended } from '@hermes/ink'

import { launchHermesCommand } from '../../../lib/externalCli.js'
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
