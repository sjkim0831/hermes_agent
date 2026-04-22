"""Daily quota tracking for provider credentials."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable
from zoneinfo import ZoneInfo

from .config import DEFAULT_CEREBRAS_DAILY_TOKEN_BUDGET, DEFAULT_GEMINI_DAILY_REQUEST_BUDGET


def _default_path() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".hermes" / "orchestrator" / "quota_usage.json"


def _local_tz() -> ZoneInfo:
    name = os.environ.get("TZ", "Asia/Seoul") or "Asia/Seoul"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Seoul")


def _now_local() -> datetime:
    return datetime.now(_local_tz())


def _window_info(provider_family: str, now: datetime | None = None) -> Dict[str, str]:
    current = now or _now_local()
    if provider_family == "cerebras":
        reset_candidate = datetime.combine(current.date(), time(hour=9), tzinfo=current.tzinfo)
        if current < reset_candidate:
            reset_at = reset_candidate - timedelta(days=1)
        else:
            reset_at = reset_candidate
        next_reset_at = reset_at + timedelta(days=1)
        return {
            "window_key": f"{reset_at.date().isoformat()}@09:00",
            "reset_at": reset_at.isoformat(),
            "next_reset_at": next_reset_at.isoformat(),
        }
    reset_at = datetime.combine(current.date(), time.min, tzinfo=current.tzinfo)
    next_reset_at = reset_at + timedelta(days=1)
    return {
        "window_key": reset_at.date().isoformat(),
        "reset_at": reset_at.isoformat(),
        "next_reset_at": next_reset_at.isoformat(),
    }


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
            return {"days": {}, "meta": {"windows": {}}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"days": {}, "meta": {"windows": {}}}
        payload.setdefault("days", {})
        payload.setdefault("meta", {})
        payload["meta"].setdefault("windows", {})
        return payload

    def save(self, payload: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _touch_window_meta(self, payload: Dict[str, Any], provider_family: str) -> Dict[str, str]:
        windows = payload.setdefault("meta", {}).setdefault("windows", {})
        info = _window_info(provider_family)
        current = windows.get(provider_family) or {}
        if current.get("window_key") != info["window_key"]:
            windows[provider_family] = {
                "window_key": info["window_key"],
                "last_reset_at": info["reset_at"],
                "next_reset_at": info["next_reset_at"],
                "updated_at": _now_local().isoformat(),
            }
        else:
            current["next_reset_at"] = info["next_reset_at"]
            current["updated_at"] = _now_local().isoformat()
        return info

    def _entry(self, payload: Dict[str, Any], provider_family: str, provider_id: str, credential_label: str) -> Dict[str, Any]:
        info = self._touch_window_meta(payload, provider_family)
        days = payload.setdefault("days", {})
        day = days.setdefault(info["window_key"], {})
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
        self.save(payload)
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
        result: Dict[str, Any] = {}
        for family in ("gemini", "cerebras"):
            info = self._touch_window_meta(payload, family)
            window_meta = payload.get("meta", {}).get("windows", {}).get(family, {})
            day = payload.get("days", {}).get(info["window_key"], {})
            entries = day.get(family, {})
            family_entries = list(entries.values())
            result[family] = {
                "window_key": info["window_key"],
                "last_reset_at": str(window_meta.get("last_reset_at") or info["reset_at"]),
                "last_manual_reset_at": window_meta.get("last_manual_reset_at"),
                "next_reset_at": info["next_reset_at"],
                "entries": family_entries,
                "totals": {
                    "requests": sum(int(item.get("requests") or 0) for item in family_entries),
                    "tokens": sum(int(item.get("tokens") or 0) for item in family_entries),
                },
            }
        self.save(payload)
        return result

    def reset(self, provider_families: Iterable[str] | None = None) -> Dict[str, Any]:
        payload = self.load()
        current = _now_local().isoformat()
        families = list(provider_families or ("gemini", "cerebras"))
        for family in families:
            info = self._touch_window_meta(payload, family)
            days = payload.setdefault("days", {})
            day = days.setdefault(info["window_key"], {})
            day[family] = {}
            windows = payload.setdefault("meta", {}).setdefault("windows", {})
            window_meta = windows.setdefault(family, {})
            window_meta["window_key"] = info["window_key"]
            window_meta["last_reset_at"] = current
            window_meta["last_manual_reset_at"] = current
            window_meta["next_reset_at"] = info["next_reset_at"]
            window_meta["updated_at"] = current
        self.save(payload)
        return self.summary()
