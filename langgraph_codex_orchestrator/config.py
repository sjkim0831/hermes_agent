"""Configuration helpers for the LangGraph Codex orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict, Iterable, List, Optional

from hermes_cli.config import custom_provider_model_ids, get_compatible_custom_providers, load_config

ROLE_NAMES = ("finder", "reader", "summarizer", "implementer", "verifier")
DEFAULT_ROLE_BASELINE = 4
MAX_STAGE_WORKERS = 20
DEFAULT_CEREBRAS_DAILY_TOKEN_BUDGET = 900_000
DEFAULT_GEMINI_DAILY_REQUEST_BUDGET = int(os.environ.get("HERMES_GEMINI_DAILY_REQUEST_BUDGET", "0") or "0")

MODEL_TOKEN_LIMITS = {
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    "qwen-3-235b-a22b-instruct-2507": 32_768,
    "llama3.1-8b": 8_192,
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


def list_provider_slots(provider_family: str) -> List[ProviderSlot]:
    deduped: dict[str, ProviderSlot] = {}
    for slot in _iter_slots(provider_family):
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


def summarize_capacity(slots: Iterable[ProviderSlot]) -> Dict[str, Any]:
    slot_list = list(slots)
    return {
        "count": len(slot_list),
        "models": sorted({model for slot in slot_list for model in slot.models}),
        "providers": [slot.provider_id for slot in slot_list],
    }
