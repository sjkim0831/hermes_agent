"""Daily quota tracking for provider credentials."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .config import DEFAULT_CEREBRAS_DAILY_TOKEN_BUDGET, DEFAULT_GEMINI_DAILY_REQUEST_BUDGET


def _default_path() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".hermes" / "orchestrator" / "quota_usage.json"


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    remaining: int
    limit: int
    metric: str
    reason: str = ""


class QuotaStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"days": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"days": {}}

    def save(self, payload: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _entry(self, payload: Dict[str, Any], provider_family: str, provider_id: str, credential_label: str) -> Dict[str, Any]:
        days = payload.setdefault("days", {})
        day = days.setdefault(_today_key(), {})
        family = day.setdefault(provider_family, {})
        key = f"{provider_id}::{credential_label or 'unknown'}"
        return family.setdefault(
            key,
            {
                "provider_id": provider_id,
                "credential_label": credential_label or "unknown",
                "requests": 0,
                "tokens": 0,
            },
        )

    def can_allocate(
        self,
        *,
        provider_family: str,
        provider_id: str,
        credential_label: str,
        estimated_tokens: int,
    ) -> QuotaDecision:
        payload = self.load()
        entry = self._entry(payload, provider_family, provider_id, credential_label)
        if provider_family == "cerebras":
            limit = DEFAULT_CEREBRAS_DAILY_TOKEN_BUDGET
            used = int(entry.get("tokens") or 0)
            remaining = max(0, limit - used)
            return QuotaDecision(
                allowed=remaining >= max(1, estimated_tokens),
                remaining=remaining,
                limit=limit,
                metric="tokens",
                reason="daily token budget reached" if remaining < max(1, estimated_tokens) else "",
            )
        limit = DEFAULT_GEMINI_DAILY_REQUEST_BUDGET
        used = int(entry.get("requests") or 0)
        if limit <= 0:
            return QuotaDecision(allowed=True, remaining=-1, limit=limit, metric="requests")
        remaining = max(0, limit - used)
        return QuotaDecision(
            allowed=remaining >= 1,
            remaining=remaining,
            limit=limit,
            metric="requests",
            reason="daily request budget reached" if remaining < 1 else "",
        )

    def record_usage(
        self,
        *,
        provider_family: str,
        provider_id: str,
        credential_label: str,
        token_count: int,
        request_count: int = 1,
    ) -> None:
        payload = self.load()
        entry = self._entry(payload, provider_family, provider_id, credential_label)
        entry["requests"] = int(entry.get("requests") or 0) + int(request_count)
        entry["tokens"] = int(entry.get("tokens") or 0) + int(max(0, token_count))
        self.save(payload)

    def summary(self) -> Dict[str, Any]:
        payload = self.load()
        day = payload.get("days", {}).get(_today_key(), {})
        result: Dict[str, Any] = {}
        for family, entries in day.items():
            family_entries = list(entries.values())
            result[family] = {
                "entries": family_entries,
                "totals": {
                    "requests": sum(int(item.get("requests") or 0) for item in family_entries),
                    "tokens": sum(int(item.get("tokens") or 0) for item in family_entries),
                },
            }
        return result
