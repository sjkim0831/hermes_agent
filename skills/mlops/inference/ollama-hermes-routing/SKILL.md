---
name: ollama-hermes-routing
description: Configure Hermes to use a local Ollama sidecar or a hybrid Ollama + remote-model stack, including model selection, deployment layout, and stage routing guidance.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [ollama, local-llm, routing, deployment, inference, hermes]
    related_skills: [mlops, software-development, mcp]
---

# Ollama + Hermes Routing

Use this skill when the user wants Hermes to run against a local Ollama
service, or wants a hybrid layout where Hermes routes cheap work locally and
keeps the strongest model for implementation and verification.

## Quick recommendation

On a 32 GB GPU machine, the best default is usually:

- **Single-model mode**: `qwen2.5-coder:32b-instruct`
- **Split mode**: `qwen2.5-coder:14b-instruct` for finder/reader/summarizer,
  `qwen2.5-coder:32b-instruct` for implementer/verifier

If you need a very fast fallback, `llama3.1:8b-instruct` is the lightest
practical option.

## What to do first

Before calling any provider, make sure Hermes has already loaded:

- skills and workspace instructions
- context files
- MCP tools
- stage routing rules

That ordering matters. The model is the backend that receives the already
prepared task.

## Deployment pattern

Treat Ollama as a sidecar, not as a vendored blob in the Hermes repo.

Recommended layout:

- Hermes source in `/opt/util/hermes`
- Ollama running locally as a service or container
- Hermes pointed at the local OpenAI-compatible endpoint
- model weights kept outside git and outside the Hermes tree

## When to use local vs remote

- Use local Ollama for fast search, reading, summarization, smoke tests, and
  fallback turns.
- Use a stronger remote model for tricky implementation or verification work.
- Use the local model to keep latency low and to avoid burning remote quota.

## Suggested stage mapping

- `finder`: local Ollama if the search space is small
- `reader`: local Ollama
- `summarizer`: local Ollama or a mid-tier remote model
- `implementer`: strongest available model
- `verifier`: strongest available model

## Caution

Do not assume "completion" means the file exists.
If the task requires a file on disk, Hermes should verify the file after the
write and fail loudly if the file is missing or mismatched.
