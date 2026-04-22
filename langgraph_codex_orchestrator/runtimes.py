"""Runtime wrappers for Codex Gemini and Cerebras worker execution."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from agent.credential_pool import CredentialPool, STATUS_EXHAUSTED, load_pool
from openai import OpenAI

from .config import ProviderSlot
from .quota import QuotaStore
from .telemetry import estimate_tokens, TelemetryStore

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
    rate_limited: bool = False
    tokens_used: int = 0
    request_count: int = 0
    budget_blocked: bool = False


@dataclass(frozen=True)
class EnvCredential:
    id: str
    label: str
    runtime_api_key: str
    last_status: Optional[str] = None
    last_status_at: Optional[float] = None
    last_error_reset_at: Optional[float] = None


class CodexWorkerRuntime:
    def __init__(self, slot: ProviderSlot, *, command: str, env_key: str) -> None:
        self.slot = slot
        self.command = command
        self.env_key = env_key
        self.pool: CredentialPool = load_pool(slot.pool_key)
        self.quota = QuotaStore()
        self.telemetry = TelemetryStore()

    def _entry_available(self, entry: object) -> bool:
        status = getattr(entry, "last_status", None)
        if status != STATUS_EXHAUSTED:
            return True
        reset_at = getattr(entry, "last_error_reset_at", None)
        if isinstance(reset_at, (int, float)):
            return float(reset_at) <= time.time()
        last_status_at = getattr(entry, "last_status_at", None)
        if isinstance(last_status_at, (int, float)):
            return float(last_status_at) + 3600 <= time.time()
        return False

    def _choose_entry(self, estimated_tokens: int):
        candidates = []
        entries = list(self.pool.entries())
        if not entries and self.slot.key_env:
            raw = os.environ.get(self.slot.key_env, "").strip()
            if raw:
                entries = [EnvCredential(id=f"env:{self.slot.key_env}", label=self.slot.name, runtime_api_key=raw)]
        for entry in entries:
            if not self._entry_available(entry):
                continue
            decision = self.quota.can_allocate(
                provider_family=self.slot.provider_family,
                provider_id=self.slot.provider_id,
                credential_label=getattr(entry, "label", "") or getattr(entry, "id", "unknown"),
                estimated_tokens=estimated_tokens,
            )
            used = decision.limit - decision.remaining if decision.limit > 0 and decision.remaining >= 0 else 0
            self.telemetry.record_quota_event(
                provider_family=self.slot.provider_family,
                provider_id=self.slot.provider_id,
                credential_label=getattr(entry, "label", "") or getattr(entry, "id", "unknown"),
                metric=decision.metric,
                used=used,
                limit=decision.limit,
                allowed=decision.allowed,
                reason=decision.reason,
            )
            if decision.allowed:
                candidates.append((entry, decision))
        if not candidates:
            return None, None
        candidates.sort(
            key=lambda item: (
                self.telemetry.provider_penalty(self.slot.provider_id, self.slot.model),
                0 if item[1].remaining < 0 else item[1].remaining,
            ),
            reverse=False,
        )
        return candidates[0]

    def run_fast_prompt(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout_seconds: float = 120.0,
    ) -> RuntimeResult:
        prompt_tokens = estimate_tokens(prompt)
        picked = self._choose_entry(prompt_tokens)
        if not picked or not picked[0]:
            return RuntimeResult(
                ok=False,
                text="",
                provider_id=self.slot.provider_id,
                model=model or self.slot.model,
                duration_seconds=0.0,
                attempts=1,
                error="All credentials are blocked by quota budget or cooldown.",
                budget_blocked=True,
            )
        entry, _decision = picked
        started = time.time()
        try:
            client = OpenAI(
                api_key=entry.runtime_api_key,
                base_url=self.slot.base_url,
                timeout=timeout_seconds,
            )
            response = client.chat.completions.create(
                model=model or self.slot.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = ((response.choices[0].message.content or "") if response.choices else "").strip()
            duration = time.time() - started
            completion_tokens = estimate_tokens(text)
            total_tokens = prompt_tokens + completion_tokens
            self.quota.record_usage(
                provider_family=self.slot.provider_family,
                provider_id=self.slot.provider_id,
                credential_label=entry.label,
                token_count=total_tokens if self.slot.provider_family == "cerebras" else 0,
                request_count=1,
            )
            return RuntimeResult(
                ok=True,
                text=text,
                provider_id=self.slot.provider_id,
                model=model or self.slot.model,
                duration_seconds=duration,
                attempts=1,
                credential_label=entry.label,
                tokens_used=total_tokens,
                request_count=1,
            )
        except Exception as exc:
            detail = str(exc).strip()
            duration = time.time() - started
            self.quota.record_usage(
                provider_family=self.slot.provider_family,
                provider_id=self.slot.provider_id,
                credential_label=entry.label,
                token_count=prompt_tokens if self.slot.provider_family == "cerebras" else 0,
                request_count=1,
            )
            if _is_rate_limited(detail):
                self.pool.mark_exhausted_and_rotate(status_code=429, error_context={"message": detail})
            return RuntimeResult(
                ok=False,
                text="",
                provider_id=self.slot.provider_id,
                model=model or self.slot.model,
                duration_seconds=duration,
                attempts=1,
                credential_label=entry.label,
                error=detail,
                rate_limited=_is_rate_limited(detail),
                tokens_used=prompt_tokens,
                request_count=1,
            )

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
        prompt_tokens = estimate_tokens(prompt)
        while attempts < max_attempts:
            attempts += 1
            picked = self._choose_entry(prompt_tokens)
            if not picked or not picked[0]:
                return RuntimeResult(
                    ok=False,
                    text="",
                    provider_id=self.slot.provider_id,
                    model=model or self.slot.model,
                    duration_seconds=0.0,
                    attempts=attempts,
                    error="All credentials are blocked by quota budget or cooldown.",
                    budget_blocked=True,
                )
            entry, _decision = picked
            lease_id = None
            if not isinstance(entry, EnvCredential):
                lease_id = self.pool.acquire_lease(getattr(entry, "id", None))
                entry = self.pool.current()
            if (lease_id is not None and entry is None) or not getattr(entry, "runtime_api_key", ""):
                return RuntimeResult(
                    ok=False,
                    text="",
                    provider_id=self.slot.provider_id,
                    model=model or self.slot.model,
                    duration_seconds=0.0,
                    attempts=attempts,
                    error="No available credentials in pool.",
                    budget_blocked=True,
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
                    completion_tokens = estimate_tokens(result.stdout or "")
                    total_tokens = prompt_tokens + completion_tokens
                    self.quota.record_usage(
                        provider_family=self.slot.provider_family,
                        provider_id=self.slot.provider_id,
                        credential_label=entry.label,
                        token_count=total_tokens if self.slot.provider_family == "cerebras" else 0,
                        request_count=1,
                    )
                    return RuntimeResult(
                        ok=True,
                        text=(result.stdout or "").strip(),
                        provider_id=self.slot.provider_id,
                        model=model or self.slot.model,
                        duration_seconds=duration,
                        attempts=attempts,
                        credential_label=entry.label,
                        tokens_used=total_tokens,
                        request_count=1,
                    )
                detail = ((result.stderr or "").strip() or (result.stdout or "").strip() or f"exit code {result.returncode}")
                completion_tokens = estimate_tokens(detail)
                total_tokens = prompt_tokens + completion_tokens
                self.quota.record_usage(
                    provider_family=self.slot.provider_family,
                    provider_id=self.slot.provider_id,
                    credential_label=entry.label,
                    token_count=total_tokens if self.slot.provider_family == "cerebras" else 0,
                    request_count=1,
                )
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
                    rate_limited=False,
                    tokens_used=total_tokens,
                    request_count=1,
                )
            except subprocess.TimeoutExpired:
                last_error = f"{self.command} timed out after {timeout_seconds}s"
                self.quota.record_usage(
                    provider_family=self.slot.provider_family,
                    provider_id=self.slot.provider_id,
                    credential_label=entry.label,
                    token_count=prompt_tokens if self.slot.provider_family == "cerebras" else 0,
                    request_count=1,
                )
                if not isinstance(entry, EnvCredential):
                    self.pool.mark_exhausted_and_rotate(status_code=408, error_context={"message": last_error})
            finally:
                if lease_id is not None:
                    self.pool.release_lease(lease_id)
        return RuntimeResult(
            ok=False,
            text="",
            provider_id=self.slot.provider_id,
            model=model or self.slot.model,
            duration_seconds=0.0,
            attempts=attempts,
            error=last_error or "All runtime attempts failed.",
            rate_limited=_is_rate_limited(last_error),
            tokens_used=prompt_tokens,
            request_count=max(1, attempts),
        )


def build_family_runtimes(slots: Iterable[ProviderSlot], provider_family: str) -> List[CodexWorkerRuntime]:
    if provider_family == "gemini":
        return [CodexWorkerRuntime(slot, command="codex-gemini", env_key="GEMINI_API_KEY") for slot in slots]
    return [CodexWorkerRuntime(slot, command="codex-cerebras", env_key="CEREBRAS_API_KEY") for slot in slots]
