"""Runtime wrappers for Codex Gemini and Cerebras worker execution."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from agent.credential_pool import CredentialPool, load_pool

from .config import ProviderSlot

RATE_LIMIT_MARKERS = ("429", "quota", "rate limit", "too many requests", "resource exhausted")


def _is_rate_limited(detail: str) -> bool:
    lowered = str(detail or "").lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


@dataclass
class RuntimeResult:
    ok: bool
    text: str
    provider_id: str
    model: str
    duration_seconds: float
    attempts: int
    credential_label: str = ""
    error: str = ""


class CodexWorkerRuntime:
    def __init__(self, slot: ProviderSlot, *, command: str, env_key: str) -> None:
        self.slot = slot
        self.command = command
        self.env_key = env_key
        self.pool: CredentialPool = load_pool(slot.pool_key)

    def run_prompt(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout_seconds: float = 1800.0,
        max_attempts: Optional[int] = None,
    ) -> RuntimeResult:
        attempts = 0
        last_error = ""
        max_attempts = max_attempts or max(1, min(20, len(self.pool.entries()) or 1))
        while attempts < max_attempts:
            attempts += 1
            lease_id = self.pool.acquire_lease()
            entry = self.pool.current()
            if not lease_id or entry is None:
                return RuntimeResult(
                    ok=False,
                    text="",
                    provider_id=self.slot.provider_id,
                    model=model or self.slot.model,
                    duration_seconds=0.0,
                    attempts=attempts,
                    error="No available credentials in pool.",
                )
            started = time.time()
            try:
                env = os.environ.copy()
                env[self.env_key] = entry.runtime_api_key
                env.setdefault("OPENAI_API_KEY", entry.runtime_api_key)
                argv = [self.command, "-q"]
                if model or self.slot.model:
                    argv.extend(["-m", model or self.slot.model])
                argv.append(prompt)
                result = subprocess.run(
                    argv,
                    cwd=cwd or env.get("HERMES_CWD") or os.getcwd(),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                duration = time.time() - started
                if result.returncode == 0:
                    return RuntimeResult(
                        ok=True,
                        text=(result.stdout or "").strip(),
                        provider_id=self.slot.provider_id,
                        model=model or self.slot.model,
                        duration_seconds=duration,
                        attempts=attempts,
                        credential_label=entry.label,
                    )
                detail = ((result.stderr or "").strip() or (result.stdout or "").strip() or f"exit code {result.returncode}")
                last_error = detail
                if _is_rate_limited(detail):
                    self.pool.mark_exhausted_and_rotate(status_code=429, error_context={"message": detail})
                    continue
                return RuntimeResult(
                    ok=False,
                    text="",
                    provider_id=self.slot.provider_id,
                    model=model or self.slot.model,
                    duration_seconds=duration,
                    attempts=attempts,
                    credential_label=entry.label,
                    error=detail,
                )
            except subprocess.TimeoutExpired:
                last_error = f"{self.command} timed out after {timeout_seconds}s"
                self.pool.mark_exhausted_and_rotate(status_code=408, error_context={"message": last_error})
            finally:
                self.pool.release_lease(lease_id)
        return RuntimeResult(
            ok=False,
            text="",
            provider_id=self.slot.provider_id,
            model=model or self.slot.model,
            duration_seconds=0.0,
            attempts=attempts,
            error=last_error or "All runtime attempts failed.",
        )


def build_family_runtimes(slots: Iterable[ProviderSlot], provider_family: str) -> List[CodexWorkerRuntime]:
    if provider_family == "gemini":
        return [CodexWorkerRuntime(slot, command="codex-gemini", env_key="GEMINI_API_KEY") for slot in slots]
    return [CodexWorkerRuntime(slot, command="codex-cerebras", env_key="CEREBRAS_API_KEY") for slot in slots]
