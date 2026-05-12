# Hermes Agent Dev

Use this skill when the task is about developing or tuning Hermes-based agents rather than changing the production runtime.

## Objective

Treat Hermes as the development and experimentation layer for agent workflows.

Hermes should be used for:

- workflow experiments
- resolver/planner/implementer/verifier orchestration
- skill design
- evaluation harnesses
- model-routing experiments

Hermes should not be assumed to be the required production runtime.

## Rules

1. Keep production assumptions out of Hermes-specific docs.
2. Prefer documenting:
   - prompt contracts
   - JSON handoff contracts
   - routing rules
   - evaluation scenarios
3. When a request is really about the runtime product:
   - move the design toward Resonance or Carbonet docs instead of Hermes internals.
4. When discussing Codex replacement:
   - explain that Hermes is an orchestration layer, not a model.
   - it can drive Ollama, Gemini, Cerebras, or Codex depending on the experiment.
5. When Carbonet install governance is involved:
   - keep `ollama-local`, `codex-cloud`, and `hermes-codex-cerebras` as explicit runner entries.
   - keep `git-governance`, `harness-eval`, and `unsloth-axolotl` as explicit toolchain entries.
   - treat Harness, Unsloth, and Axolotl as evaluation or finetune layers, not runtime-critical production dependencies.

## Expected Outputs

- Hermes-side workflow docs
- agent-role definitions
- evaluation plans
- routing experiments
- skill updates
