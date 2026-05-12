"""Runtime wrappers for Codex Gemini and Cerebras worker execution."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from agent.credential_pool import CredentialPool, STATUS_EXHAUSTED, load_pool
from openai import OpenAI

from .config import ProviderSlot
from .quota import QuotaStore
from .telemetry import estimate_tokens, TelemetryStore

RATE_LIMIT_MARKERS = ("429", "quota", "rate limit", "too many requests", "resource exhausted")


def _is_rate_limited(detail: str) -> bool:
    lowered = str(detail or "").lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


def _progress(message: str) -> None:
    if os.environ.get("HERMES_ORCHESTRATOR_PROGRESS", "0") != "1":
        return
    sys.stderr.write(f"[orchestrator] {message}\n")
    sys.stderr.flush()


def _append_tail(text: str, addition: str, limit: int) -> str:
    combined = f"{text or ''}{addition or ''}"
    if len(combined) <= limit:
        return combined
    return combined[-limit:]


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
    stdout_path: str = ""
    stderr_path: str = ""


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
                return entry, decision
        return None, None

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
            _progress(
                f"api call: provider={self.slot.provider_id} credential={entry.label} "
                f"model={model or self.slot.model} mode=fast prompt_tokens={prompt_tokens}"
            )
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
            stdout_path = ""
            stderr_path = ""
            last_stdout = ""
            last_stderr = ""
            proc = None
            try:
                env = os.environ.copy()
                env[self.env_key] = entry.runtime_api_key
                env.setdefault("OPENAI_API_KEY", entry.runtime_api_key)
                argv = [self.command, "-q"]
                if model or self.slot.model:
                    argv.extend(["-m", model or self.slot.model])
                argv.append(prompt)
                display_argv = [*argv[:-1], f"<PROMPT {len(prompt)} chars>"]
                _progress(
                    "cmd: "
                    + " ".join(shlex.quote(part) for part in display_argv)
                    + f" cwd={cwd or env.get('HERMES_CWD') or os.getcwd()} "
                    + f"provider={self.slot.provider_id} credential={entry.label}"
                )
                stdout_fd, stdout_path = tempfile.mkstemp(prefix="hermes-stdout-", suffix=".log")
                stderr_fd, stderr_path = tempfile.mkstemp(prefix="hermes-stderr-", suffix=".log")
                os.close(stdout_fd)
                os.close(stderr_fd)

                def pump(stream, sink, kind: str) -> None:
                    nonlocal last_stdout, last_stderr
                    try:
                        for line in iter(stream.readline, ""):
                            if not line:
                                break
                            sink.write(line)
                            sink.flush()
                            preview = line.rstrip("\n\r")
                            if preview:
                                _progress(f"{kind}: {preview[:240]}")
                            if kind == "stdout":
                                last_stdout = _append_tail(last_stdout, line, 4096)
                            else:
                                last_stderr = _append_tail(last_stderr, line, 4096)
                    finally:
                        try:
                            stream.close()
                        except Exception:
                            pass

                with open(stdout_path, "w", encoding="utf-8", errors="replace") as stdout_file, open(
                    stderr_path, "w", encoding="utf-8", errors="replace"
                ) as stderr_file:
                    proc = subprocess.Popen(
                        argv,
                        cwd=cwd or env.get("HERMES_CWD") or os.getcwd(),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )
                    stdout_thread = threading.Thread(target=pump, args=(proc.stdout, stdout_file, "stdout"), daemon=True)
                    stderr_thread = threading.Thread(target=pump, args=(proc.stderr, stderr_file, "stderr"), daemon=True)
                    stdout_thread.start()
                    stderr_thread.start()
                    try:
                        returncode = proc.wait(timeout=timeout_seconds)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            pass
                        raise
                    finally:
                        stdout_thread.join(timeout=5)
                        stderr_thread.join(timeout=5)
                duration = time.time() - started
                if returncode == 0:
                    completion_tokens = estimate_tokens(last_stdout)
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
                        text=(last_stdout or "").strip()[-4096:],
                        provider_id=self.slot.provider_id,
                        model=model or self.slot.model,
                        duration_seconds=duration,
                        attempts=attempts,
                        credential_label=entry.label,
                        tokens_used=total_tokens,
                        request_count=1,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                    )
                detail = ((last_stderr or "").strip() or (last_stdout or "").strip() or f"exit code {returncode}")
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
                    text=(last_stdout or "").strip()[-4096:],
                    provider_id=self.slot.provider_id,
                    model=model or self.slot.model,
                    duration_seconds=duration,
                    attempts=attempts,
                    credential_label=entry.label,
                    error=detail,
                    rate_limited=False,
                    tokens_used=total_tokens,
                    request_count=1,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
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


class OllamaWorkerRuntime:
    def __init__(self, slot: ProviderSlot, *, env_key: str = "OLLAMA_API_KEY") -> None:
        self.slot = slot
        self.env_key = env_key
        self.telemetry = TelemetryStore()

    def _api_key(self) -> str:
        for key_name in (self.env_key, "OLLAMA_API_KEY", "OPENAI_API_KEY"):
            value = str(os.environ.get(key_name, "") or "").strip()
            if value:
                return value
        return "ollama"

    def _base_url(self) -> str:
        return str(self.slot.base_url or os.environ.get("OLLAMA_BASE_URL", "") or "http://127.0.0.1:11434/v1").strip()

    def _call_openai(self, prompt: str, *, model: Optional[str], timeout_seconds: float) -> RuntimeResult:
        prompt_tokens = estimate_tokens(prompt)
        started = time.time()
        try:
            _progress(
                f"api call: provider={self.slot.provider_id} credential={self.slot.name} "
                f"model={model or self.slot.model} mode=ollama prompt_tokens={prompt_tokens}"
            )
            client = OpenAI(
                api_key=self._api_key(),
                base_url=self._base_url(),
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
            self.telemetry.record_usage(
                provider_family=self.slot.provider_family,
                provider_id=self.slot.provider_id,
                credential_label=self.slot.name,
                token_count=total_tokens,
                request_count=1,
            )
            return RuntimeResult(
                ok=True,
                text=text,
                provider_id=self.slot.provider_id,
                model=model or self.slot.model,
                duration_seconds=duration,
                attempts=1,
                credential_label=self.slot.name,
                tokens_used=total_tokens,
                request_count=1,
            )
        except Exception as exc:
            detail = str(exc).strip()
            duration = time.time() - started
            self.telemetry.record_usage(
                provider_family=self.slot.provider_family,
                provider_id=self.slot.provider_id,
                credential_label=self.slot.name,
                token_count=prompt_tokens,
                request_count=1,
            )
            return RuntimeResult(
                ok=False,
                text="",
                provider_id=self.slot.provider_id,
                model=model or self.slot.model,
                duration_seconds=duration,
                attempts=1,
                credential_label=self.slot.name,
                error=detail,
                rate_limited=_is_rate_limited(detail),
                tokens_used=prompt_tokens,
                request_count=1,
            )

    def run_fast_prompt(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout_seconds: float = 120.0,
    ) -> RuntimeResult:
        return self._call_openai(prompt, model=model, timeout_seconds=timeout_seconds)

    def run_prompt(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout_seconds: float = 1800.0,
        max_attempts: Optional[int] = None,
    ) -> RuntimeResult:
        return self._call_openai(prompt, model=model, timeout_seconds=timeout_seconds)


def build_family_runtimes(slots: Iterable[ProviderSlot], provider_family: str) -> List[Any]:
    if provider_family == "ollama":
        return [OllamaWorkerRuntime(slot) for slot in slots]
    if provider_family == "gemini":
        return [CodexWorkerRuntime(slot, command="codex-gemini", env_key="GEMINI_API_KEY") for slot in slots]
    return [CodexWorkerRuntime(slot, command="codex-cerebras", env_key="CEREBRAS_API_KEY") for slot in slots]
