"""Worker allocation heuristics for staged Codex orchestration."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict

from .config import DEFAULT_ROLE_BASELINE, MAX_STAGE_WORKERS, ROLE_NAMES
from .telemetry import TelemetryStore

ROLE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "general": {
        "finder": 0.7,
        "reader": 0.9,
        "summarizer": 0.8,
        "implementer": 1.2,
        "verifier": 0.7,
    },
    "repo_search": {
        "finder": 1.6,
        "reader": 1.4,
        "summarizer": 1.0,
        "implementer": 0.6,
        "verifier": 0.4,
    },
    "ui_design": {
        "finder": 0.8,
        "reader": 1.0,
        "summarizer": 0.8,
        "implementer": 1.6,
        "verifier": 0.7,
    },
    "bug_fix": {
        "finder": 0.9,
        "reader": 1.2,
        "summarizer": 0.9,
        "implementer": 1.3,
        "verifier": 1.0,
    },
    "refactor": {
        "finder": 0.8,
        "reader": 1.1,
        "summarizer": 1.0,
        "implementer": 1.4,
        "verifier": 0.9,
    },
}


@dataclass(frozen=True)
class StageAllocation:
    role: str
    workers: int
    estimated_tokens: int
    token_limit: int
    overload_ratio: float


def _clamp_workers(value: float) -> int:
    return max(1, min(MAX_STAGE_WORKERS, int(round(value))))


def _telemetry_multiplier(telemetry: TelemetryStore, task_type: str, role: str) -> float:
    stats = telemetry.stage_stats(task_type, role)
    multiplier = 1.0
    if stats["avg_duration_seconds"] > 60:
        multiplier += min(1.5, stats["avg_duration_seconds"] / 180.0)
    if stats["success_rate"] and stats["success_rate"] < 0.75:
        multiplier += 0.25
    if stats["last_worker_count"]:
        multiplier += min(0.5, stats["last_worker_count"] / 40.0)
    return multiplier


def build_stage_allocations(
    *,
    task_type: str,
    difficulty: int,
    estimated_tokens: int,
    token_limit: int,
    telemetry: TelemetryStore,
) -> Dict[str, StageAllocation]:
    weights = ROLE_WEIGHTS.get(task_type, ROLE_WEIGHTS["general"])
    overload_ratio = max(1.0, estimated_tokens / max(1, token_limit))
    allocations: Dict[str, StageAllocation] = {}
    for role in ROLE_NAMES:
        base = DEFAULT_ROLE_BASELINE * weights.get(role, 1.0)
        difficulty_factor = 0.8 + (difficulty * 0.35)
        token_factor = max(1.0, math.sqrt(overload_ratio))
        telemetry_factor = _telemetry_multiplier(telemetry, task_type, role)
        workers = _clamp_workers(base * difficulty_factor * token_factor * telemetry_factor)
        allocations[role] = StageAllocation(
            role=role,
            workers=workers,
            estimated_tokens=estimated_tokens,
            token_limit=token_limit,
            overload_ratio=round(overload_ratio, 3),
        )
    return allocations
