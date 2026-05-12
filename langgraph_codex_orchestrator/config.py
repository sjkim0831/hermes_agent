"""Configuration helpers for the LangGraph Codex orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv
from hermes_cli.config import custom_provider_model_ids, get_compatible_custom_providers, load_config

ROLE_NAMES = ("finder", "reader", "summarizer", "implementer", "verifier")
DEFAULT_ROLE_BASELINE = 4
MAX_STAGE_WORKERS = 20
DEFAULT_CEREBRAS_DAILY_TOKEN_BUDGET = 900_000
DEFAULT_GEMINI_DAILY_REQUEST_BUDGET = int(os.environ.get("HERMES_GEMINI_DAILY_REQUEST_BUDGET", "500") or "500")

_HERMES_ENV_PATH = os.path.expanduser("~/.hermes/.env")
load_dotenv(_HERMES_ENV_PATH, override=False)

MODEL_TOKEN_LIMITS = {
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    "qwen-3-235b-a22b-instruct-2507": 32_768,
    "llama3.1-8b": 8_192,
    "llama3.1-8b-instruct": 8_192,
    "qwen2.5-coder:14b-instruct": 32_768,
    "qwen2.5-coder:32b-instruct": 32_768,
}


def normalize_custom_provider_name(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "-")


@dataclass(frozen=True)
class ProviderSlot:
    provider_id: str
    pool_key: str
    name: str
    provider_family: str
    model: str
    models: tuple[str, ...]
    base_url: str
    key_env: Optional[str] = None

    @property
    def token_limit(self) -> int:
        if self.model in MODEL_TOKEN_LIMITS:
            return MODEL_TOKEN_LIMITS[self.model]
        for item in self.models:
            if item in MODEL_TOKEN_LIMITS:
                return MODEL_TOKEN_LIMITS[item]
        return 32_768


def _iter_slots(provider_family: str) -> Iterable[ProviderSlot]:
    config = load_config()
    for entry in get_compatible_custom_providers(config):
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if provider_family == "gemini" and not lowered.startswith("gemini api "):
            continue
        if provider_family == "cerebras" and not lowered.startswith("cerebras api "):
            continue
        if provider_family == "ollama":
            provider_key = str(entry.get("provider_key") or "").strip().lower()
            if not (
                lowered.startswith("ollama local")
                or provider_key.startswith("ollama-local")
                or lowered.startswith("ollama")
            ):
                continue
        models = tuple(custom_provider_model_ids(entry))
        model = str(entry.get("model") or "").strip() or (models[0] if models else "")
        normalized = normalize_custom_provider_name(name)
        yield ProviderSlot(
            provider_id=f"custom:{normalized}",
            pool_key=f"custom:{normalized}",
            name=name,
            provider_family=provider_family,
            model=model,
            models=models,
            base_url=str(entry.get("base_url") or "").strip(),
            key_env=str(entry.get("key_env") or "").strip() or None,
        )


def _default_ollama_slots() -> List[ProviderSlot]:
    base_url = str(os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1") or "").strip() or "http://127.0.0.1:11434/v1"
    default_models = [
        model.strip()
        for model in str(
            os.environ.get(
                "OLLAMA_MODELS",
                "qwen2.5-coder:14b-instruct,qwen2.5-coder:32b-instruct,llama3.1:8b-instruct",
            )
            or ""
        ).split(",")
        if model.strip()
    ]
    if not default_models:
        default_models = ["qwen2.5-coder:14b-instruct", "qwen2.5-coder:32b-instruct"]
    primary_model = str(os.environ.get("OLLAMA_DEFAULT_MODEL", "") or "").strip() or default_models[0]
    worker_count = max(1, min(4, int(os.environ.get("OLLAMA_WORKER_COUNT", "1") or "1")))
    slots: List[ProviderSlot] = []
    for index in range(worker_count):
        suffix = index + 1
        name = "Ollama Local" if worker_count == 1 else f"Ollama Local {suffix}"
        slots.append(
            ProviderSlot(
                provider_id=f"ollama-local-{suffix:02d}",
                pool_key=f"ollama-local-{suffix:02d}",
                name=name,
                provider_family="ollama",
                model=primary_model,
                models=tuple(default_models),
                base_url=base_url,
                key_env=None,
            )
        )
    return slots


def list_provider_slots(provider_family: str) -> List[ProviderSlot]:
    deduped: dict[str, ProviderSlot] = {}
    for slot in _iter_slots(provider_family):
        deduped.setdefault(slot.provider_id, slot)
    if provider_family == "ollama" and not deduped:
        ollama_hint = any(
            os.environ.get(name, "").strip()
            for name in ("OLLAMA_BASE_URL", "OLLAMA_MODELS", "OLLAMA_WORKER_COUNT", "OLLAMA_DEFAULT_MODEL")
        ) or shutil.which("ollama") is not None
        if not ollama_hint:
            return list(deduped.values())
        for slot in _default_ollama_slots():
            deduped.setdefault(slot.provider_id, slot)
    return list(deduped.values())


def pick_default_model(slot: ProviderSlot, preferred: Optional[str] = None) -> str:
    if preferred and preferred in slot.models:
        return preferred
    if slot.model:
        return slot.model
    if slot.models:
        return slot.models[0]
    return ""


def load_stage_model_routing() -> Dict[str, str]:
    """Load optional role-to-provider-family overrides for orchestration stages."""
    config = load_config()
    raw = config.get("stage_model_routing") or {}
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, str] = {}
    for role, family in raw.items():
        role_name = str(role or "").strip().lower()
        family_name = str(family or "").strip().lower()
        if role_name and family_name in {"gemini", "cerebras", "ollama"}:
            normalized[role_name] = family_name
    return normalized


def summarize_capacity(slots: Iterable[ProviderSlot]) -> Dict[str, Any]:
    slot_list = list(slots)
    return {
        "count": len(slot_list),
        "models": sorted({model for slot in slot_list for model in slot.models}),
        "providers": [slot.provider_id for slot in slot_list],
    }
