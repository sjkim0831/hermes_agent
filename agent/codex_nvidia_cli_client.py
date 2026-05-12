"""OpenAI-compatible shim that forwards Hermes requests to ``codex-nvidia``.

Hermes expects an OpenAI-style ``client.chat.completions.create(...)`` surface.
This adapter spawns the local ``codex-nvidia`` CLI, lets Codex perform the
actual agent/tool loop, then maps the final text response back into the minimal
chat-completions shape Hermes already understands.
"""

from __future__ import annotations

import os
import subprocess
import time
from types import SimpleNamespace
from typing import Any

CODEX_NVIDIA_MARKER_BASE_URL = "codex+nvidia://local"
_DEFAULT_TIMEOUT_SECONDS = 1800.0
_PLANNER_BRIEF_PREFIX = "Planner brief for the execution model:"
_MAX_MESSAGE_CHARS = 4000
_MAX_TRANSCRIPT_MESSAGES = 6


def _render_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text.strip()
        return str(content).strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content).strip()


def _trim_message_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= _MAX_MESSAGE_CHARS:
        return cleaned
    return cleaned[:_MAX_MESSAGE_CHARS].rstrip() + "\n...[truncated]"


def _select_relevant_messages(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "context").strip().lower()
        if role == "system":
            continue
        rendered = _trim_message_text(_render_message_content(message.get("content")))
        if rendered:
            collected.append((role, rendered))

    if not collected:
        return []

    planner_indices = [
        idx for idx, (role, rendered) in enumerate(collected)
        if role == "user" and rendered.startswith(_PLANNER_BRIEF_PREFIX)
    ]
    if planner_indices:
        collected = collected[planner_indices[-1]:]

    deduped: list[tuple[str, str]] = []
    for item in collected:
        if not deduped or deduped[-1] != item:
            deduped.append(item)

    return deduped[-_MAX_TRANSCRIPT_MESSAGES:]


def _format_messages_as_prompt(messages: list[dict[str, Any]], model: str | None = None) -> str:
    sections: list[str] = [
        "You are being called as the active Codex execution backend for Hermes.",
        "Use your own Codex tools and workflow to complete the task.",
        "Return only the final assistant response for Hermes to display.",
    ]
    if model:
        sections.append(f"Requested model hint: {model}")

    transcript: list[str] = []
    for role, rendered in _select_relevant_messages(messages):
        label = {
            "user": "User",
            "assistant": "Assistant",
            "tool": "Tool",
        }.get(role, role.title())
        transcript.append(f"{label}:\n{rendered}")

    if transcript:
        sections.append("Conversation transcript:\n\n" + "\n\n".join(transcript))
    sections.append("Continue from the latest user request.")
    return "\n\n".join(part for part in sections if part.strip())


def _coerce_timeout_seconds(timeout_value: Any) -> float:
    if timeout_value is None:
        return _DEFAULT_TIMEOUT_SECONDS
    if isinstance(timeout_value, (int, float)):
        return max(1.0, float(timeout_value))
    for attr in ("read", "timeout"):
        candidate = getattr(timeout_value, attr, None)
        if isinstance(candidate, (int, float)):
            return max(1.0, float(candidate))
    return _DEFAULT_TIMEOUT_SECONDS


def _chunk_text(text: str, size: int = 160) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + size] for i in range(0, len(text), size)]


class _CodexNvidiaStream:
    def __init__(self, text: str):
        self._chunks = _chunk_text(text)
        self._index = 0

    def __iter__(self) -> "_CodexNvidiaStream":
        return self

    def __next__(self) -> Any:
        if self._index < len(self._chunks):
            chunk_text = self._chunks[self._index]
            self._index += 1
            delta = SimpleNamespace(content=chunk_text, tool_calls=None)
            choice = SimpleNamespace(delta=delta, finish_reason=None)
            return SimpleNamespace(choices=[choice])

        if self._index == len(self._chunks):
            self._index += 1
            delta = SimpleNamespace(content=None, tool_calls=None)
            choice = SimpleNamespace(delta=delta, finish_reason="stop")
            return SimpleNamespace(choices=[choice])

        raise StopIteration


class _CodexNvidiaChatCompletions:
    def __init__(self, client: "CodexNvidiaCLIClient"):
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create_chat_completion(**kwargs)


class _CodexNvidiaChatNamespace:
    def __init__(self, client: "CodexNvidiaCLIClient"):
        self.completions = _CodexNvidiaChatCompletions(client)


class CodexNvidiaCLIClient:
    """Minimal OpenAI-style client facade backed by ``codex-nvidia``."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = CODEX_NVIDIA_MARKER_BASE_URL,
        command: str | None = None,
        args: list[str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url or CODEX_NVIDIA_MARKER_BASE_URL
        self.command = command or "codex-nvidia"
        self.args = list(args or [])
        self.chat = _CodexNvidiaChatNamespace(self)

    def _build_command(self, *, model: str | None, prompt: str) -> list[str]:
        argv = [self.command, *self.args, "-q"]
        if model:
            argv.extend(["-m", model])
        argv.append(prompt)
        return argv

    def _run_codex(self, *, model: str | None, prompt: str, timeout: float) -> str:
        env = os.environ.copy()
        env["NVIDIA_API_KEY"] = self.api_key
        env.setdefault("OPENAI_API_KEY", self.api_key)
        cwd = env.get("HERMES_CWD") or os.getcwd()
        argv = self._build_command(model=model, prompt=prompt)
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or f"exit code {result.returncode}"
            raise RuntimeError(f"codex-nvidia failed: {detail}")
        return (result.stdout or "").strip()

    def _create_chat_completion(self, **kwargs: Any) -> Any:
        messages = list(kwargs.get("messages") or [])
        model = str(kwargs.get("model") or "").strip() or None
        timeout = _coerce_timeout_seconds(kwargs.get("timeout"))
        stream = bool(kwargs.get("stream"))

        prompt = _format_messages_as_prompt(messages, model=model)
        text = self._run_codex(model=model, prompt=prompt, timeout=timeout)

        if stream:
            return _CodexNvidiaStream(text)

        message = SimpleNamespace(role="assistant", content=text, tool_calls=None)
        choice = SimpleNamespace(index=0, message=message, finish_reason="stop")
        return SimpleNamespace(
            id=f"codex-nvidia-{int(time.time() * 1000)}",
            object="chat.completion",
            created=int(time.time()),
            model=model or "codex-nvidia",
            choices=[choice],
            usage=None,
        )
