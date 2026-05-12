# Hermes Local Model Routing and Ollama

Hermes can work with local OpenAI-compatible model servers such as Ollama.
The important part is the order of operations:

1. Load skills, context files, and project instructions.
2. Apply MCP connections and tool availability.
3. Build the stage plan and shard the task.
4. Call the selected provider for each stage.
5. Verify the result on disk or through stage-specific checks.

So yes, the skills/docs/MCP layer is applied before the Codex/Ollama call.
The provider is the execution backend after Hermes has already shaped the
task.

## Recommended local models for this machine

The current machine has a 32 GB NVIDIA GPU and 64 GB system RAM. That makes
two practical options:

### One-model setup

- `qwen2.5-coder:32b-instruct`
- Prefer a 4-bit or 5-bit quantized build if you want to keep latency sane

Use this when you want the simplest possible setup and can accept a little
extra latency on smaller tasks.

### Two-model setup

- Fast helper: `qwen2.5-coder:14b-instruct`
- Main implementer/verifier: `qwen2.5-coder:32b-instruct`

Use this when you want the best balance of speed and correctness.
Finder/reader/summarizer can stay cheap, while implementer/verifier get the
stronger model.

### Fast fallback

- `llama3.1:8b-instruct`

Use this for trivial tasks, smoke tests, or when you want the lightest local
option.

## Deployment pattern

Ollama should be treated as a sidecar service, not as a vendored blob inside
the Hermes source tree.

That means:

- Keep Hermes source in `/opt/util/hermes`.
- Run Ollama as a local service or container.
- Point Hermes at the local OpenAI-compatible endpoint
  (`http://127.0.0.1:11434/v1` is the common default).
- Keep model weights outside git.

This is the practical "integrated" deployment model: one Hermes repo, one
local Ollama service, one routing layer.

## Why this helps

- Faster local turns for search and reading.
- Better control over implementer/verifier quality.
- Easier fallback when remote providers rate-limit.
- Cleaner separation between orchestration logic and model runtime.

## Why files sometimes looked "completed" but were missing

Hermes stages can complete even if a result only looked successful in logs.
The implementation stage now re-checks Desktop HTML writes after writing them.
If the file cannot be written or the contents do not match, the stage raises
an error instead of reporting a false success.

## Suggested Hermes routing policy

- `finder`, `reader`, `summarizer`: local Ollama helper when the task is cheap
- `implementer`, `verifier`: stronger remote model or your best local model
- Fallback to remote Codex/Cerebras when Ollama is overloaded or rate-limited

## Minimal rule of thumb

- One model: `qwen2.5-coder:32b-instruct`
- Two models: `qwen2.5-coder:14b-instruct` + `qwen2.5-coder:32b-instruct`
- Keep Ollama as a sidecar and keep Hermes as the router
