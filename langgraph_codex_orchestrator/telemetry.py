"""Telemetry persistence and heuristics for worker allocation."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .config import ROLE_NAMES


def _default_path() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".hermes" / "orchestrator" / "telemetry.json"


def _default_event_log_path() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".hermes" / "orchestrator" / "events.jsonl"


def estimate_tokens(text: str) -> int:
    cleaned = str(text or "").strip()
    if not cleaned:
        return 0
    return max(1, len(cleaned) // 4)


def classify_task_type(text: str) -> str:
    lowered = str(text or "").lower()
    if any(word in lowered for word in ("login", "screen", "html", "design", "ui", "page")):
        return "ui_design"
    if any(word in lowered for word in ("bug", "fix", "error", "exception", "trace")):
        return "bug_fix"
    if any(word in lowered for word in ("find", "search", "scan", "locate", "read", "analyze repo")):
        return "repo_search"
    if any(word in lowered for word in ("refactor", "cleanup", "restructure")):
        return "refactor"
    return "general"


def estimate_difficulty(text: str) -> int:
    lowered = str(text or "").lower()
    score = 1
    score += min(4, len(lowered) // 800)
    if any(word in lowered for word in ("parallel", "multi", "many files", "entire repo", "langgraph")):
        score += 2
    if any(word in lowered for word in ("design", "html", "ui", "page")):
        score += 1
    return max(1, min(score, 5))


class TelemetryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.event_log_path = _default_event_log_path()
        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"stages": {}, "history": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"stages": {}, "history": []}

    def save(self, payload: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def append_event(self, event: Dict[str, Any]) -> None:
        row = dict(event)
        row.setdefault("ts", int(time.time()))
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    def record_stage(
        self,
        *,
        task_type: str,
        role: str,
        duration_seconds: float,
        success: bool,
        token_estimate: int,
        worker_count: int,
        provider_family: str,
        quota_failures: int = 0,
        task_signature: str = "",
    ) -> None:
        payload = self.load()
        stages = payload.setdefault("stages", {})
        stage_key = f"{task_type}:{role}"
        entry = stages.setdefault(
            stage_key,
            {
                "task_type": task_type,
                "role": role,
                "runs": 0,
                "successes": 0,
                "total_duration_seconds": 0.0,
                "total_tokens": 0,
                "last_worker_count": 0,
                "provider_family": provider_family,
                "quota_failures": 0,
            },
        )
        entry["runs"] += 1
        entry["successes"] += 1 if success else 0
        entry["total_duration_seconds"] += float(duration_seconds)
        entry["total_tokens"] += int(token_estimate)
        entry["last_worker_count"] = int(worker_count)
        entry["provider_family"] = provider_family
        entry["quota_failures"] = int(entry.get("quota_failures") or 0) + int(quota_failures)
        payload.setdefault("history", []).append(
            {
                "ts": int(time.time()),
                "task_type": task_type,
                "role": role,
                "duration_seconds": round(float(duration_seconds), 3),
                "success": bool(success),
                "token_estimate": int(token_estimate),
                "worker_count": int(worker_count),
                "provider_family": provider_family,
                "quota_failures": int(quota_failures),
                "task_signature": task_signature[:200],
            }
        )
        payload["history"] = payload["history"][-400:]
        self.save(payload)
        self.append_event(
            {
                "kind": "stage_summary",
                "task_type": task_type,
                "role": role,
                "duration_seconds": round(float(duration_seconds), 3),
                "success": bool(success),
                "token_estimate": int(token_estimate),
                "worker_count": int(worker_count),
                "provider_family": provider_family,
                "quota_failures": int(quota_failures),
                "task_signature": task_signature[:200],
            }
        )

    def record_worker_result(
        self,
        *,
        task_type: str,
        role: str,
        provider_family: str,
        provider_id: str,
        model: str,
        credential_label: str,
        duration_seconds: float,
        success: bool,
        rate_limited: bool,
        shard_index: int,
        shard_task: str,
        error: str = "",
    ) -> None:
        payload = self.load()
        provider_stats = payload.setdefault("provider_stats", {})
        key = f"{provider_id}:{model}"
        entry = provider_stats.setdefault(
            key,
            {
                "provider_id": provider_id,
                "model": model,
                "runs": 0,
                "successes": 0,
                "rate_limits": 0,
                "avg_duration_seconds": 0.0,
                "last_credential_label": "",
            },
        )
        runs = int(entry.get("runs") or 0) + 1
        total_duration = float(entry.get("avg_duration_seconds") or 0.0) * int(entry.get("runs") or 0)
        total_duration += float(duration_seconds)
        entry["runs"] = runs
        entry["successes"] = int(entry.get("successes") or 0) + (1 if success else 0)
        entry["rate_limits"] = int(entry.get("rate_limits") or 0) + (1 if rate_limited else 0)
        entry["avg_duration_seconds"] = total_duration / runs
        entry["last_credential_label"] = credential_label
        self.save(payload)
        self.append_event(
            {
                "kind": "worker_result",
                "task_type": task_type,
                "role": role,
                "provider_family": provider_family,
                "provider_id": provider_id,
                "model": model,
                "credential_label": credential_label,
                "duration_seconds": round(float(duration_seconds), 3),
                "success": bool(success),
                "rate_limited": bool(rate_limited),
                "shard_index": int(shard_index),
                "shard_task": shard_task[:400],
                "error": error[:400],
            }
        )

    def stage_stats(self, task_type: str, role: str) -> Dict[str, float]:
        payload = self.load()
        entry = payload.get("stages", {}).get(f"{task_type}:{role}", {})
        runs = int(entry.get("runs") or 0)
        total_duration = float(entry.get("total_duration_seconds") or 0.0)
        total_tokens = int(entry.get("total_tokens") or 0)
        successes = int(entry.get("successes") or 0)
        quota_failures = int(entry.get("quota_failures") or 0)
        return {
            "runs": runs,
            "avg_duration_seconds": (total_duration / runs) if runs else 0.0,
            "avg_tokens": (total_tokens / runs) if runs else 0.0,
            "success_rate": (successes / runs) if runs else 0.0,
            "last_worker_count": int(entry.get("last_worker_count") or 0),
            "quota_failure_rate": (quota_failures / runs) if runs else 0.0,
        }

    def provider_penalty(self, provider_id: str, model: str) -> float:
        payload = self.load()
        entry = payload.get("provider_stats", {}).get(f"{provider_id}:{model}", {})
        runs = int(entry.get("runs") or 0)
        if not runs:
            return 0.0
        rate_limits = int(entry.get("rate_limits") or 0)
        avg_duration = float(entry.get("avg_duration_seconds") or 0.0)
        return min(2.0, (rate_limits / runs) * 1.5 + max(0.0, avg_duration - 60.0) / 240.0)

    def summarize(self) -> Dict[str, Any]:
        payload = self.load()
        grouped: Dict[str, Dict[str, Any]] = defaultdict(dict)
        for role in ROLE_NAMES:
            for task_type in ("general", "repo_search", "ui_design", "bug_fix", "refactor"):
                stats = self.stage_stats(task_type, role)
                if stats["runs"]:
                    grouped[task_type][role] = stats
        return dict(grouped)
