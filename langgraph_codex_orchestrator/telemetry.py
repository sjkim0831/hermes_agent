"""Telemetry persistence and heuristics for worker allocation."""

from __future__ import annotations

import json
import os
import uuid
import time
import hashlib
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


def _default_run_state_path() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".hermes" / "orchestrator" / "current_run.json"


def _preview_text(text: str, limit: int = 240) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _trim_event_log(path: Path, *, keep_lines: int = 2000, max_bytes: int = 5 * 1024 * 1024) -> None:
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= keep_lines:
            return
        path.write_text("\n".join(lines[-keep_lines:]) + "\n", encoding="utf-8")
    except Exception:
        return


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
        _trim_event_log(self.event_log_path)

    def record_quota_event(
        self,
        *,
        provider_family: str,
        provider_id: str,
        credential_label: str,
        metric: str,
        used: int,
        limit: int,
        allowed: bool,
        reason: str = "",
    ) -> None:
        self.append_event(
            {
                "kind": "quota",
                "provider_family": provider_family,
                "provider_id": provider_id,
                "credential_label": credential_label,
                "metric": metric,
                "used": int(used),
                "limit": int(limit),
                "allowed": bool(allowed),
                "reason": reason[:300],
            }
        )

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
        completion_status: str = "complete",
        failed_shards: int = 0,
        retried_shards: int = 0,
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
                "completion_status": "unknown",
                "failed_shards": 0,
                "retried_shards": 0,
            },
        )
        entry["runs"] += 1
        entry["successes"] += 1 if success else 0
        entry["total_duration_seconds"] += float(duration_seconds)
        entry["total_tokens"] += int(token_estimate)
        entry["last_worker_count"] = int(worker_count)
        entry["provider_family"] = provider_family
        entry["quota_failures"] = int(entry.get("quota_failures") or 0) + int(quota_failures)
        entry["completion_status"] = completion_status
        entry["failed_shards"] = int(failed_shards)
        entry["retried_shards"] = int(retried_shards)
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
                "completion_status": completion_status,
                "failed_shards": int(failed_shards),
                "retried_shards": int(retried_shards),
                "task_signature": task_signature[:200],
            }
        )
        payload["history"] = payload["history"][-200:]
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
                "completion_status": completion_status,
                "failed_shards": int(failed_shards),
                "retried_shards": int(retried_shards),
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


class RunStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_run_state_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _default_state(self) -> Dict[str, Any]:
        return {
            "run_id": "",
            "status": "idle",
            "completion_status": "idle",
            "task": "",
            "cwd": "",
            "task_type": "",
            "difficulty": 0,
            "estimated_tokens": 0,
            "updated_at": "",
            "stages": {},
            "stage_order": list(ROLE_NAMES),
            "pending_approval": None,
        }

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._default_state()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return self._default_state()
        except Exception:
            return self._default_state()
        payload.setdefault("stages", {})
        payload.setdefault("stage_order", list(ROLE_NAMES))
        payload.setdefault("status", "idle")
        payload.setdefault("completion_status", "idle")
        payload.setdefault("pending_approval", None)
        return payload

    def save(self, payload: Dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("stages", {})
        payload.setdefault("stage_order", list(ROLE_NAMES))
        payload.setdefault("pending_approval", None)
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _ensure_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload.get("run_id"):
            payload["run_id"] = uuid.uuid4().hex
        payload.setdefault("stages", {})
        return payload

    def start_run(
        self,
        *,
        task: str,
        cwd: str,
        task_type: str,
        difficulty: int,
        estimated_tokens: int,
        allocations: Dict[str, Dict[str, Any]],
        stage_tasks: Dict[str, list[str]],
    ) -> Dict[str, Any]:
        payload = self._ensure_run(self.load())
        payload.update(
            {
                "status": "running",
                "completion_status": "running",
                "task": task,
                "cwd": cwd,
                "task_type": task_type,
                "difficulty": int(difficulty),
                "estimated_tokens": int(estimated_tokens),
            }
        )
        stages = payload.setdefault("stages", {})
        for role in ROLE_NAMES:
            role_tasks = list(stage_tasks.get(role, []))
            stage_alloc = dict(allocations.get(role) or {})
            stages[role] = {
                "role": role,
                "status": "pending",
                "planned_workers": int(stage_alloc.get("workers") or 0),
                "completed_shards": 0,
                "failed_shards": 0,
                "retried_shards": 0,
                "running_shards": 0,
                "pending_shards": len(role_tasks),
                "shards": [
                    {
                        "shard_index": index + 1,
                        "status": "pending",
                        "title": f"{role}-{index + 1}",
                        "ownership": "",
                        "instruction_preview": _preview_text(instruction),
                        "estimated_seconds": 0.0,
                        "attempts": 0,
                        "provider_id": "",
                        "model": "",
                        "credential_label": "",
                        "duration_seconds": 0.0,
                        "tokens_used": 0,
                        "rate_limited": False,
                        "error": "",
                    }
                    for index, instruction in enumerate(role_tasks)
                ],
            }
        self.save(payload)
        return payload

    def set_stage_plan(
        self,
        role: str,
        tasks: list[Dict[str, Any]],
        *,
        planned_workers: int,
        provider_family: str,
    ) -> Dict[str, Any]:
        payload = self._ensure_run(self.load())
        stage = payload.setdefault("stages", {}).setdefault(
            role,
            {
                "role": role,
                "status": "pending",
                "planned_workers": int(planned_workers),
                "completed_shards": 0,
                "failed_shards": 0,
                "retried_shards": 0,
                "running_shards": 0,
                "pending_shards": 0,
                "shards": [],
            },
        )
        stage.update(
            {
                "planned_workers": int(planned_workers),
                "provider_family": provider_family,
                "status": "running",
            }
        )
        stage["shards"] = [
            {
                "shard_index": int(task.get("shard_index") or index + 1),
                "status": "pending",
                "title": str(task.get("title") or f"{role}-{index + 1}"),
                "ownership": str(task.get("ownership") or ""),
                "instruction_preview": _preview_text(str(task.get("instruction_preview") or task.get("instruction") or "")),
                "estimated_seconds": float(task.get("estimated_seconds") or 0.0),
                "attempts": 0,
                "provider_id": "",
                "model": "",
                "credential_label": "",
                "duration_seconds": 0.0,
                "tokens_used": 0,
                "rate_limited": False,
                "error": "",
            }
            for index, task in enumerate(tasks)
        ]
        stage["pending_shards"] = len(stage["shards"])
        self._recalculate_stage(stage)
        self.save(payload)
        return payload

    def _recalculate_stage(self, stage: Dict[str, Any]) -> None:
        shards = list(stage.get("shards") or [])
        stage["completed_shards"] = sum(1 for shard in shards if shard.get("status") == "completed")
        stage["failed_shards"] = sum(1 for shard in shards if shard.get("status") == "failed")
        stage["running_shards"] = sum(1 for shard in shards if shard.get("status") == "running")
        stage["retrying_shards"] = sum(1 for shard in shards if shard.get("status") == "retrying")
        stage["pending_shards"] = sum(1 for shard in shards if shard.get("status") == "pending")
        stage["retried_shards"] = sum(max(0, int(shard.get("attempts") or 0) - 1) for shard in shards)

    def update_shard(
        self,
        role: str,
        shard_index: int,
        *,
        status: str,
        title: str = "",
        ownership: str = "",
        instruction: str = "",
        estimated_seconds: float = 0.0,
        provider_id: str = "",
        model: str = "",
        credential_label: str = "",
        attempts: int = 0,
        duration_seconds: float = 0.0,
        tokens_used: int = 0,
        rate_limited: bool = False,
        error: str = "",
    ) -> Dict[str, Any]:
        payload = self._ensure_run(self.load())
        stage = payload.setdefault("stages", {}).setdefault(role, {"role": role, "shards": []})
        shards = stage.setdefault("shards", [])
        target = None
        for shard in shards:
            if int(shard.get("shard_index") or 0) == int(shard_index):
                target = shard
                break
        if target is None:
            target = {
                "shard_index": int(shard_index),
                "status": "pending",
                "title": title or f"{role}-{shard_index}",
                "ownership": ownership,
                "instruction": instruction,
                "estimated_seconds": float(estimated_seconds or 0.0),
                "attempts": 0,
                "provider_id": "",
                "model": "",
                "credential_label": "",
                "duration_seconds": 0.0,
                "tokens_used": 0,
                "rate_limited": False,
                "error": "",
            }
            shards.append(target)
        if title:
            target["title"] = title
        if ownership:
            target["ownership"] = ownership
        if instruction:
            target["instruction_preview"] = _preview_text(instruction)
        if estimated_seconds:
            target["estimated_seconds"] = float(estimated_seconds)
        if attempts:
            target["attempts"] = max(int(target.get("attempts") or 0), int(attempts))
        if provider_id:
            target["provider_id"] = provider_id
        if model:
            target["model"] = model
        if credential_label:
            target["credential_label"] = credential_label
        if duration_seconds:
            target["duration_seconds"] = float(duration_seconds)
        if tokens_used:
            target["tokens_used"] = int(tokens_used)
        target["rate_limited"] = bool(rate_limited)
        if error:
            target["error"] = error[:500]
        target["status"] = status
        self._recalculate_stage(stage)
        self.save(payload)
        return payload

    def finish_stage(
        self,
        role: str,
        *,
        status: str,
        failed_shards: int,
        retried_shards: int,
        completed_shards: int,
    ) -> Dict[str, Any]:
        payload = self._ensure_run(self.load())
        stage = payload.setdefault("stages", {}).setdefault(role, {"role": role, "shards": []})
        stage.update(
            {
                "status": status,
                "failed_shards": int(failed_shards),
                "retried_shards": int(retried_shards),
                "completed_shards": int(completed_shards),
            }
        )
        self.save(payload)
        return payload

    def finalize(self, completion_status: str) -> Dict[str, Any]:
        payload = self._ensure_run(self.load())
        payload["status"] = "complete" if completion_status == "complete" else "finished"
        payload["completion_status"] = completion_status
        payload["pending_approval"] = None
        self.save(payload)
        return payload

    def set_pending_approval(self, approval: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._ensure_run(self.load())
        payload["pending_approval"] = dict(approval)
        self.save(payload)
        return payload

    def clear_pending_approval(self) -> Dict[str, Any]:
        payload = self._ensure_run(self.load())
        payload["pending_approval"] = None
        self.save(payload)
        return payload

    def pending_approval(self) -> Dict[str, Any] | None:
        payload = self.load()
        approval = payload.get("pending_approval")
        return approval if isinstance(approval, dict) else None

    def snapshot(self) -> Dict[str, Any]:
        return self.load()



class SearchCacheStore:
    def __init__(self, path: Path | None = None) -> None:
        home = Path(os.environ.get("HOME", "~")).expanduser()
        self.path = path or (home / ".hermes" / "orchestrator" / "search_cache.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _default_payload(self) -> Dict[str, Any]:
        return {"entries": {}}

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._default_payload()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("entries", {})
                return payload
        except Exception:
            pass
        return self._default_payload()

    def save(self, payload: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _key(self, *, role: str, task_type: str, cwd: str, task: str, planned_tasks: int, dispatch_workers: int) -> str:
        raw = "\n".join([
            role.strip().lower(),
            task_type.strip().lower(),
            cwd.strip(),
            task.strip(),
            str(int(planned_tasks)),
            str(int(dispatch_workers)),
        ])
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()

    def get(
        self,
        *,
        role: str,
        task_type: str,
        cwd: str,
        task: str,
        planned_tasks: int,
        dispatch_workers: int,
        max_age_seconds: int = 24 * 3600,
    ) -> Dict[str, Any] | None:
        payload = self.load()
        key = self._key(
            role=role,
            task_type=task_type,
            cwd=cwd,
            task=task,
            planned_tasks=planned_tasks,
            dispatch_workers=dispatch_workers,
        )
        entry = payload.get("entries", {}).get(key)
        if not isinstance(entry, dict):
            return None
        created_at = float(entry.get("created_at") or 0.0)
        if created_at and (time.time() - created_at) > max_age_seconds:
            return None
        return entry

    def set(
        self,
        *,
        role: str,
        task_type: str,
        cwd: str,
        task: str,
        planned_tasks: int,
        dispatch_workers: int,
        stage_tasks: list[Dict[str, Any]],
        results: list[Dict[str, Any]],
        api_report: Dict[str, Any],
        stage_outputs: list[str],
    ) -> None:
        payload = self.load()
        key = self._key(
            role=role,
            task_type=task_type,
            cwd=cwd,
            task=task,
            planned_tasks=planned_tasks,
            dispatch_workers=dispatch_workers,
        )
        payload.setdefault("entries", {})[key] = {
            "created_at": time.time(),
            "role": role,
            "task_type": task_type,
            "cwd": cwd,
            "task": task,
            "planned_tasks": int(planned_tasks),
            "dispatch_workers": int(dispatch_workers),
            "stage_tasks": stage_tasks,
            "results": results,
            "api_report": api_report,
            "stage_outputs": stage_outputs[:3],
        }
        payload["entries"] = dict(list(payload.get("entries", {}).items())[-200:])
        self.save(payload)
