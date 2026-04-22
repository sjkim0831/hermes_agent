"""Telemetry persistence and heuristics for worker allocation."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable

from .config import ROLE_NAMES


def _default_path() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".hermes" / "orchestrator" / "telemetry.json"


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

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"stages": {}, "history": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"stages": {}, "history": []}

    def save(self, payload: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

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
            },
        )
        entry["runs"] += 1
        entry["successes"] += 1 if success else 0
        entry["total_duration_seconds"] += float(duration_seconds)
        entry["total_tokens"] += int(token_estimate)
        entry["last_worker_count"] = int(worker_count)
        entry["provider_family"] = provider_family
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
            }
        )
        payload["history"] = payload["history"][-400:]
        self.save(payload)

    def stage_stats(self, task_type: str, role: str) -> Dict[str, float]:
        payload = self.load()
        entry = payload.get("stages", {}).get(f"{task_type}:{role}", {})
        runs = int(entry.get("runs") or 0)
        total_duration = float(entry.get("total_duration_seconds") or 0.0)
        total_tokens = int(entry.get("total_tokens") or 0)
        successes = int(entry.get("successes") or 0)
        return {
            "runs": runs,
            "avg_duration_seconds": (total_duration / runs) if runs else 0.0,
            "avg_tokens": (total_tokens / runs) if runs else 0.0,
            "success_rate": (successes / runs) if runs else 0.0,
            "last_worker_count": int(entry.get("last_worker_count") or 0),
        }

    def summarize(self) -> Dict[str, Any]:
        payload = self.load()
        grouped: Dict[str, Dict[str, Any]] = defaultdict(dict)
        for role in ROLE_NAMES:
            for task_type in ("general", "repo_search", "ui_design", "bug_fix", "refactor"):
                stats = self.stage_stats(task_type, role)
                if stats["runs"]:
                    grouped[task_type][role] = stats
        return dict(grouped)
