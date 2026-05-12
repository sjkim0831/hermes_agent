"""LangGraph workflow for supervisor/worker Codex orchestration."""

from __future__ import annotations

import json
import math
import os
import random
import re
import subprocess
import shutil
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Dict, List, TypedDict

from .config import ROLE_NAMES, list_provider_slots, load_stage_model_routing, pick_default_model, summarize_capacity
from .runtimes import CodexWorkerRuntime, RuntimeResult, build_family_runtimes
from .scheduler import StageAllocation, build_stage_allocations
from .telemetry import RunStateStore, SearchCacheStore, TelemetryStore, classify_task_type, estimate_difficulty, estimate_tokens

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - runtime dependency guard
    END = "__end__"
    StateGraph = None  # type: ignore[assignment]


class OrchestratorState(TypedDict, total=False):
    task: str
    cwd: str
    task_type: str
    difficulty: int
    estimated_tokens: int
    allocations: Dict[str, Dict[str, Any]]
    provider_capacity: Dict[str, Any]
    plan: str
    findings: List[str]
    summaries: List[str]
    implementation: str
    verification: str
    final_response: str
    telemetry_summary: Dict[str, Any]
    stage_tasks: Dict[str, List[str]]
    fast_path: bool
    execution_mode: str
    gated_session_id: str
    gated_checkpoint_path: str
    next_stage_index: int
    api_report: Dict[str, Any]
    stage_outputs: Dict[str, List[str]]
    stage_model_routing: Dict[str, str]
    implementation_files: List[str]
    implementation_meta: Dict[str, Any]
    strict_implement_mode: bool


@dataclass(frozen=True)
class StageTask:
    role: str
    shard_index: int
    shard_count: int
    instruction: str
    title: str = ""
    ownership: str = ""
    estimated_seconds: float = 6.0


def _progress(message: str) -> None:
    if os.environ.get("HERMES_ORCHESTRATOR_PROGRESS", "0") != "1":
        return
    sys.stderr.write(f"[orchestrator] {message}\n")
    sys.stderr.flush()



def _resolve_provider_family_for_role(role: str, state: OrchestratorState | None, default: str) -> str:
    routing = dict((state or {}).get("stage_model_routing") or {})
    family = str(routing.get(role) or default).strip().lower()
    if family in {"gemini", "cerebras", "ollama"} and list_provider_slots(family):
        return family
    return default


def _strict_implement_mode(state: OrchestratorState | None = None) -> bool:
    state_flag = bool((state or {}).get("strict_implement_mode"))
    env_flag = str(os.environ.get("HERMES_ORCHESTRATOR_STRICT_IMPLEMENT", "") or "").strip().lower()
    return state_flag or env_flag in {"1", "true", "yes", "on"}


_SEARCH_STOPWORDS = {
    "http", "https", "www", "com", "net", "org", "html", "page", "screen", "update", "change",
    "modify", "please", "project", "server", "route", "url", "file", "files", "code", "repo", "repository",
    "example", "localhost", "index", "main",
}


def _extract_search_terms(task: str) -> List[str]:
    raw = str(task or "")
    url_parts = re.findall(r"https?://[^\s]+", raw, flags=re.IGNORECASE)
    tokens = re.findall(r"[A-Za-z0-9_./:-]+", raw)
    results: List[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        item = str(value or "").strip().strip("/ ").lower()
        if not item or item in seen or item in _SEARCH_STOPWORDS:
            return
        if len(item) < 3 and not any(ch in item for ch in "_-/."):
            return
        seen.add(item)
        results.append(item)

    for url in url_parts:
        for piece in re.split(r"[^A-Za-z0-9_]+", url):
            push(piece)
    for token in tokens:
        for piece in re.split(r"[^A-Za-z0-9_]+", token):
            push(piece)
    return results[:8]


def _extract_url_hints(task: str) -> List[str]:
    raw = str(task or "")
    hints: List[str] = []
    seen: set[str] = set()
    for url in re.findall(r"https?://[^\s]+", raw, flags=re.IGNORECASE):
        parts = re.split(r"[^A-Za-z0-9_]+", url)
        for part in parts:
            item = str(part or "").strip().lower()
            if not item or item in seen or item in _SEARCH_STOPWORDS:
                continue
            if len(item) < 2:
                continue
            seen.add(item)
            hints.append(item)
    return hints[:12]


def _normalized_path_tokens(path_text: str) -> List[str]:
    pieces = re.split(r"[^A-Za-z0-9_]+", str(path_text or "").lower())
    return [piece for piece in pieces if piece]


def _score_path_candidate(path_text: str, terms: List[str], url_hints: List[str]) -> int:
    lowered = str(path_text or "").lower()
    basename = Path(lowered).name
    stem = Path(lowered).stem
    tokens = set(_normalized_path_tokens(lowered))
    score = 0
    for term in terms:
        if term in lowered:
            score += 2
        if term in tokens:
            score += 2
        if term == stem or term == basename:
            score += 3
    for hint in url_hints:
        if hint in lowered:
            score += 2
        if hint in tokens:
            score += 3
        if hint == stem or hint == basename:
            score += 4
    if "/pages/" in lowered:
        score += 3
    if "/components/" in lowered:
        score += 2
    if "/lib/api" in lowered or basename == "api.ts":
        score += 3
    if any(marker in lowered for marker in ("/docs/", ".example", "example-", "sample", "fixture", "mock")):
        score -= 4
    if any(marker in lowered for marker in ("/src/pages/", "page.tsx", "page.jsx", "modal", "login", "auth")):
        score += 2
    if any(marker in lowered for marker in ("route", "router", "page", "screen", "view", "component", "service", "api", "controller")):
        score += 1
    if lowered.endswith((".tsx", ".ts", ".jsx", ".js", ".py", ".java", ".jsp", ".html", ".vue")):
        score += 1
    return score


def _deterministic_presearch(state: OrchestratorState, planned_tasks: int) -> List[RuntimeResult]:
    cwd_raw = str(state.get("cwd") or "").strip()
    if not cwd_raw:
        return []
    cwd = Path(cwd_raw).expanduser()
    if not cwd.exists() or not cwd.is_dir():
        return []
    if shutil.which("rg") is None:
        return []

    terms = _extract_search_terms(str(state.get("task") or ""))
    url_hints = _extract_url_hints(str(state.get("task") or ""))
    if not terms and not url_hints:
        return []

    score_map: Dict[str, Dict[str, Any]] = {}
    max_terms = min(6, len(terms)) if terms else 0
    base_cmd = [
        "rg", "-n", "-S", "-l", "--hidden",
        "-g", "!.git", "-g", "!node_modules", "-g", "!venv", "-g", "!dist", "-g", "!build",
        "-g", "!coverage", "-g", "!__pycache__",
    ]
    path_bonus_markers = ("route", "router", "page", "screen", "view", "component", "service", "api", "controller")

    for term in terms[:max_terms]:
        cmd = [*base_cmd, term, str(cwd)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
        except Exception:
            continue
        if proc.returncode not in (0, 1):
            continue
        for line in proc.stdout.splitlines()[:60]:
            path_text = line.strip()
            if not path_text:
                continue
            entry = score_map.setdefault(path_text, {"score": 0, "terms": [], "path_score": 0})
            entry["score"] += 1
            entry["terms"].append(term)
            lowered = path_text.lower()
            if any(marker in lowered for marker in path_bonus_markers):
                entry["score"] += 1
            if lowered.endswith((".tsx", ".ts", ".jsx", ".js", ".py", ".java", ".jsp", ".html", ".vue")):
                entry["score"] += 1

    file_cmd = [
        "rg", "--files", "--hidden",
        "-g", "!.git", "-g", "!node_modules", "-g", "!venv", "-g", "!dist", "-g", "!build",
        "-g", "!coverage", "-g", "!__pycache__", str(cwd),
    ]
    try:
        proc = subprocess.run(file_cmd, capture_output=True, text=True, timeout=4, check=False)
    except Exception:
        proc = None
    if proc and proc.returncode == 0:
        for line in proc.stdout.splitlines()[:4000]:
            path_text = line.strip()
            if not path_text:
                continue
            path_score = _score_path_candidate(path_text, terms, url_hints)
            if path_score <= 0:
                continue
            entry = score_map.setdefault(path_text, {"score": 0, "terms": [], "path_score": 0})
            entry["path_score"] = max(int(entry.get("path_score") or 0), path_score)
            entry["score"] += path_score

    if not score_map:
        return []

    ranked = sorted(
        score_map.items(),
        key=lambda item: (-int(item[1]["score"]), len(item[0]), item[0]),
    )[: min(18, max(6, planned_tasks * 4))]

    lines = [
        "Deterministic presearch candidates:",
        f"cwd: {cwd}",
        f"terms: {', '.join(terms[:max_terms])}",
        f"url_hints: {', '.join(url_hints[:8])}",
        "",
    ]
    for index, (path_text, meta) in enumerate(ranked, start=1):
        rel = path_text
        try:
            rel = str(Path(path_text).resolve().relative_to(cwd.resolve()))
        except Exception:
            pass
        lines.append(
            f"{index}. {rel} | score={meta['score']} | path_score={meta.get('path_score', 0)} | matched_terms={', '.join(meta['terms'][:4])}"
        )
    lines.extend([
        "",
        "Use only these files as the primary search frontier.",
        "Escalate to broader repo search only if none of these candidates fit the requested URL or feature.",
    ])
    return [
        RuntimeResult(
            ok=True,
            text="\n".join(lines),
            provider_id="deterministic-presearch",
            model="local-rg",
            duration_seconds=0.0,
            attempts=1,
            credential_label="local",
            tokens_used=estimate_tokens("\n".join(lines)),
            request_count=0,
        )
    ]


from pathlib import Path


def _gated_store_dir() -> Path:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".hermes" / "orchestrator" / "gated_sessions"


def _gated_checkpoint_path(session_id: str) -> Path:
    return _gated_store_dir() / f"{session_id}.json"


def _save_gated_checkpoint(state: OrchestratorState, session_id: str, next_stage_index: int) -> Path:
    payload = _compact_checkpoint_state(dict(state))
    payload["gated_session_id"] = session_id
    payload["next_stage_index"] = int(next_stage_index)
    path = _gated_checkpoint_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def _load_gated_checkpoint(path: str) -> OrchestratorState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid gated checkpoint")
    return data  # type: ignore[return-value]


def _gated_stage_cap(dispatch_workers: int) -> int:
    return max(dispatch_workers, min(24, dispatch_workers * 1))


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return []
    fence = raw.find("```json")
    if fence >= 0:
        raw = raw[fence + 7:]
        end = raw.find("```")
        if end >= 0:
            raw = raw[:end]
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    try:
        payload = json.loads(raw[start : end + 1])
    except Exception:
        return []
    return [item for item in payload if isinstance(item, dict)]


def _summarize_results(results: List[RuntimeResult]) -> List[str]:
    lines: List[str] = []
    for result in results:
        status = "ok" if result.ok else "error"
        preview = _runtime_text(result, limit=500) if result.ok else (result.error or _runtime_text(result, limit=500)).strip()
        if len(preview) > 500:
            preview = preview[:500].rstrip() + "\n...[truncated]"
        lines.append(
            f"[{status}] provider={result.provider_id} model={result.model} credential={result.credential_label or '-'} "
            f"attempts={result.attempts} duration={result.duration_seconds:.2f}s\n{preview}"
        )
    return lines


def _compact_result_preview(results: List[RuntimeResult], limit: int = 5) -> List[str]:
    preview: List[str] = []
    for result in results[: max(0, limit)]:
        status = "ok" if result.ok else "err"
        payload = (_runtime_text(result, limit=180) if result.ok else (result.error or _runtime_text(result, limit=180))).strip().replace("\n", " ")
        if len(payload) > 180:
            payload = payload[:180].rstrip() + "..."
        preview.append(
            f"- {result.provider_id} [{result.credential_label or '-'}] "
            f"{status} {result.duration_seconds:.2f}s tokens={result.tokens_used} :: {payload}"
        )
    if len(results) > limit:
        preview.append(f"- ... {len(results) - limit} more result(s) omitted")
    return preview


def _fold_stage_results(stage_outputs: List[str], report: Dict[str, Any]) -> List[str]:
    total = len(stage_outputs)
    ok_results = [line for line in stage_outputs if str(line).startswith("- ")]
    err_results = [line for line in stage_outputs if "err " in str(line) or "[error]" in str(line).lower()]
    lines = [
        "Results summary:",
        f"- total={total} success={len(ok_results)} failed={len(err_results)}",
        "",
        "Successful results:",
        *(ok_results[:3] or ["- none"]),
        "",
        "Failed results:",
        *(err_results[:3] or ["- none"]),
        "",
        "By API:",
        *(_format_api_report(report).splitlines() if report else ["- (no api report)"]),
    ]
    return lines


def _desktop_output_dir() -> Path:
    return Path("/mnt/c/Users/sjkim/Desktop")



def _tail_file(path: str, limit: int = 8192) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    try:
        size = file_path.stat().st_size
        with file_path.open("rb") as handle:
            if size > limit:
                handle.seek(-limit, os.SEEK_END)
            data = handle.read()
    except Exception:
        return ""
    return data.decode("utf-8", errors="replace")


def _runtime_text(result: RuntimeResult, *, limit: int = 8192) -> str:
    if result.stdout_path:
        stdout_text = _tail_file(result.stdout_path, limit=limit)
        if stdout_text:
            return stdout_text
    if result.ok and result.text:
        return result.text
    if result.stderr_path:
        stderr_text = _tail_file(result.stderr_path, limit=limit)
        if stderr_text:
            return stderr_text
    return result.error or result.text or ""


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "")
    if not raw:
        return {}
    candidates: List[str] = []
    fence_pattern = re.compile(r"```json\s*(.*?)```", re.IGNORECASE | re.DOTALL)
    candidates.extend(match.group(1).strip() for match in fence_pattern.finditer(raw) if match.group(1).strip())
    candidates.append(raw)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        starts = [match.start() for match in re.finditer(r"\{", candidate)]
        for start in reversed(starts):
            snippet = candidate[start:]
            try:
                payload, consumed = decoder.raw_decode(snippet)
            except Exception:
                continue
            if isinstance(payload, dict) and consumed > 0:
                return payload
    return {}


def _normalize_changed_files(value: Any) -> List[str]:
    files: List[str] = []
    seen: set[str] = set()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return files
    for item in value:
        path_text = str(item or "").strip()
        if not path_text or path_text in seen:
            continue
        seen.add(path_text)
        files.append(path_text)
    return files


def _collect_result_metadata(results: List[RuntimeResult]) -> Dict[str, Any]:
    changed_files: List[str] = []
    created_files: List[str] = []
    summaries: List[str] = []
    raw_objects: List[Dict[str, Any]] = []
    seen_changed: set[str] = set()
    seen_created: set[str] = set()
    for result in results:
        payload = _extract_json_object(_runtime_text(result, limit=65536))
        if not payload:
            continue
        raw_objects.append(payload)
        for path_text in _normalize_changed_files(payload.get("changed_files")):
            if path_text not in seen_changed:
                seen_changed.add(path_text)
                changed_files.append(path_text)
        for path_text in _normalize_changed_files(payload.get("created_files")):
            if path_text not in seen_created:
                seen_created.add(path_text)
                created_files.append(path_text)
        summary = str(payload.get("summary") or "").strip()
        if summary:
            summaries.append(summary)
    return {
        "changed_files": changed_files,
        "created_files": created_files,
        "summaries": summaries,
        "raw": raw_objects,
    }


def _verify_changed_files(cwd: str | None, changed_files: List[str]) -> List[str]:
    if not cwd:
        return []
    root = Path(cwd).expanduser()
    lines: List[str] = []
    for item in changed_files:
        candidate = Path(item)
        path = candidate if candidate.is_absolute() else root / candidate
        exists = path.exists()
        status = "exists" if exists else "missing"
        rel = str(path)
        try:
            rel = str(path.resolve().relative_to(root.resolve()))
        except Exception:
            pass
        lines.append(f"- {rel}: {status}")
    return lines


def _extract_diff_blocks(text: str) -> List[str]:
    raw = str(text or "")
    if not raw:
        return []
    blocks = [match.group(1).strip() for match in re.finditer(r"```diff\s*(.*?)```", raw, re.IGNORECASE | re.DOTALL)]
    results: List[str] = []
    for block in blocks:
        if block and ("diff --git" in block or block.startswith("--- ") or "*** Begin Patch" in block):
            results.append(block)
    return results


def _apply_patch_block(cwd: str | None, patch_text: str) -> Dict[str, Any]:
    if not cwd:
        return {"ok": False, "error": "missing cwd"}
    root = Path(cwd).expanduser()
    if not root.exists():
        return {"ok": False, "error": f"cwd not found: {root}"}
    if not patch_text.strip():
        return {"ok": False, "error": "empty patch"}
    cmd = ["git", "apply", "--whitespace=nowarn", "-"]
    proc = subprocess.run(cmd, input=patch_text, text=True, capture_output=True, cwd=str(root), check=False)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:2000],
        "stderr": (proc.stderr or "").strip()[:2000],
    }


def _apply_result_patches(cwd: str | None, results: List[RuntimeResult]) -> Dict[str, Any]:
    applied: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        blocks = _extract_diff_blocks(_runtime_text(result, limit=131072))
        if not blocks:
            continue
        for block_index, block in enumerate(blocks, start=1):
            outcome = _apply_patch_block(cwd, block)
            row = {
                "result_index": index,
                "block_index": block_index,
                "provider_id": result.provider_id,
                "model": result.model,
                "ok": bool(outcome.get("ok")),
                "stderr": outcome.get("stderr", ""),
                "stdout": outcome.get("stdout", ""),
            }
            if outcome.get("ok"):
                applied.append(row)
            else:
                failed.append(row)
    return {"applied": applied, "failed": failed}


def _compact_text(text: str, limit: int = 240) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _compact_stage_tasks(stage_tasks: Dict[str, List[str]]) -> Dict[str, List[str]]:
    compacted: Dict[str, List[str]] = {}
    for role, tasks in stage_tasks.items():
        compacted[role] = [_compact_text(task, 180) for task in list(tasks or [])]
    return compacted


def _compact_checkpoint_state(state: OrchestratorState) -> OrchestratorState:
    payload = dict(state)
    payload["stage_tasks"] = _compact_stage_tasks({role: list(tasks or []) for role, tasks in dict(state.get("stage_tasks") or {}).items()})
    stage_outputs = dict(state.get("stage_outputs") or {})
    payload["stage_outputs"] = {role: [str(item)[:220] for item in list(items or [])[:4]] for role, items in stage_outputs.items()}
    for key, limit in (("plan", 6000), ("findings", 4000), ("summaries", 4000), ("implementation", 6000), ("verification", 4000), ("final_response", 8000)):
        value = payload.get(key)
        if isinstance(value, str) and len(value) > limit:
            payload[key] = value[: max(0, limit - 3)].rstrip() + "..."
    return payload


def _preview_captured_outputs(stage_outputs: List[str], limit: int = 3) -> List[str]:
    lines = list(stage_outputs[:limit])
    if len(stage_outputs) > limit:
        lines.append(f"- ... {len(stage_outputs) - limit} more result(s) omitted")
    return lines


def _runtime_result_from_cache(item: Dict[str, Any]) -> RuntimeResult:
    return RuntimeResult(
        ok=bool(item.get("ok", True)),
        text=str(item.get("text") or ""),
        provider_id=str(item.get("provider_id") or ""),
        model=str(item.get("model") or ""),
        duration_seconds=float(item.get("duration_seconds") or 0.0),
        attempts=int(item.get("attempts") or 1),
        credential_label=str(item.get("credential_label") or ""),
        error=str(item.get("error") or ""),
        rate_limited=bool(item.get("rate_limited") or False),
        tokens_used=int(item.get("tokens_used") or 0),
        request_count=int(item.get("request_count") or 1),
        budget_blocked=bool(item.get("budget_blocked") or False),
    )


def _pick_login_theme(task: str) -> str:
    themes = ("aurora", "midnight", "ember", "mono")
    seed = sum(ord(ch) for ch in task) + sum(ord(ch) for ch in os.environ.get("HERMES_RUN_ID", "")) + os.getpid()
    rng = random.SystemRandom()
    return themes[(seed + rng.randrange(len(themes))) % len(themes)]


def _theme_tokens(theme: str) -> Dict[str, str]:
    palette = {
        "aurora": {
            "bg": "#050816",
            "panel": "rgba(8, 15, 31, 0.84)",
            "border": "rgba(96, 165, 250, 0.26)",
            "text": "#e5eefb",
            "muted": "#8fa3c7",
            "accent": "#5dd6ff",
            "accent2": "#8b5cf6",
            "glow": "rgba(93, 214, 255, 0.18)",
            "card_width": "440px",
            "card_radius": "28px",
            "font_stack": "Inter, 'Segoe UI', system-ui, sans-serif",
            "accent_name": "Aurora",
        },
        "midnight": {
            "bg": "#060814",
            "panel": "rgba(15, 23, 42, 0.88)",
            "border": "rgba(148, 163, 184, 0.22)",
            "text": "#e2e8f0",
            "muted": "#94a3b8",
            "accent": "#38bdf8",
            "accent2": "#0ea5e9",
            "glow": "rgba(56, 189, 248, 0.16)",
            "card_width": "420px",
            "card_radius": "22px",
            "font_stack": "Inter, 'Segoe UI', system-ui, sans-serif",
            "accent_name": "Midnight",
        },
        "ember": {
            "bg": "#120b08",
            "panel": "rgba(36, 15, 9, 0.88)",
            "border": "rgba(251, 146, 60, 0.22)",
            "text": "#fff4e8",
            "muted": "#e4b98d",
            "accent": "#fb923c",
            "accent2": "#f43f5e",
            "glow": "rgba(251, 146, 60, 0.16)",
            "card_width": "460px",
            "card_radius": "32px",
            "font_stack": "Aptos, 'Segoe UI', system-ui, sans-serif",
            "accent_name": "Ember",
        },
        "mono": {
            "bg": "#090909",
            "panel": "rgba(25, 25, 25, 0.92)",
            "border": "rgba(255, 255, 255, 0.18)",
            "text": "#f5f5f5",
            "muted": "#b5b5b5",
            "accent": "#f2f2f2",
            "accent2": "#c9c9c9",
            "glow": "rgba(255, 255, 255, 0.08)",
            "card_width": "400px",
            "card_radius": "12px",
            "font_stack": "'Courier New', ui-monospace, monospace",
            "accent_name": "Mono",
        },
    }
    return palette.get(theme, palette["midnight"])


def _render_login_draft_html(task: str) -> str:
    theme = _pick_login_theme(task)
    tokens = _theme_tokens(theme)
    title = f"Login Page Draft - {tokens['accent_name']}"
    task_comment = "\n".join(f"    <!-- {line} -->" for line in str(task).splitlines())
    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: {tokens['bg']};
      --panel: {tokens['panel']};
      --panel-border: {tokens['border']};
      --text: {tokens['text']};
      --muted: {tokens['muted']};
      --accent: {tokens['accent']};
      --accent-strong: {tokens['accent2']};
      --glow: {tokens['glow']};
      --card-width: {tokens['card_width']};
      --card-radius: {tokens['card_radius']};
      --font-stack: {tokens['font_stack']};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: var(--font-stack);
      color: var(--text);
      background:
        radial-gradient(circle at top, var(--glow), transparent 34%),
        linear-gradient(160deg, rgba(2, 6, 23, 0.92), var(--bg) 56%, #111827 100%);
    }}
    .card {{
      width: min(92vw, var(--card-width));
      padding: 34px;
      border: 1px solid var(--panel-border);
      border-radius: var(--card-radius);
      background: var(--panel);
      backdrop-filter: blur(18px);
      box-shadow: 0 30px 80px rgba(2, 6, 23, 0.55);
      position: relative;
      overflow: hidden;
    }}
    .card::after {{
      content: "";
      position: absolute;
      inset: auto -12% -20% auto;
      width: 180px;
      height: 180px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(255,255,255,0.12), transparent 65%);
      filter: blur(8px);
      pointer-events: none;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      margin-bottom: 16px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .eyebrow::before {{
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(135deg, var(--accent), var(--accent-strong));
      box-shadow: 0 0 18px var(--glow);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      letter-spacing: -0.03em;
    }}
    p {{
      margin: 0 0 24px;
      color: var(--muted);
      line-height: 1.5;
    }}
    label {{
      display: block;
      margin: 16px 0 8px;
      font-size: 13px;
      color: var(--muted);
    }}
    input {{
      width: 100%;
      padding: 14px 16px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 14px;
      background: rgba(15, 23, 42, 0.72);
      color: var(--text);
      outline: none;
    }}
    input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 4px rgba(96, 165, 250, 0.18);
    }}
    .actions {{
      display: grid;
      gap: 12px;
      margin-top: 22px;
    }}
    button {{
      width: 100%;
      padding: 14px 16px;
      border: 0;
      border-radius: 14px;
      background: linear-gradient(135deg, var(--accent), var(--accent-strong));
      color: white;
      font-weight: 700;
      cursor: pointer;
    }}
    .hint {{
      margin-top: 16px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(148, 163, 184, 0.14);
      background: rgba(255, 255, 255, 0.03);
      color: var(--muted);
      line-height: 1.5;
      font-size: 13px;
    }}
    .links {{
      display: flex;
      justify-content: space-between;
      margin-top: 14px;
      font-size: 13px;
      color: var(--muted);
    }}
    .links a {{
      color: var(--accent);
      text-decoration: none;
    }}
    .meta {{
      margin-top: 18px;
      font-size: 12px;
      color: rgba(226, 232, 240, 0.62);
      line-height: 1.5;
      word-break: break-word;
    }}
  </style>
</head>
<body>
  <main class=\"card\">
    <div class=\"eyebrow\">{tokens['accent_name']} theme</div>
    <h1>Login</h1>
    <p>Draft login screen generated for the requested task. This run uses a randomized visual theme so the layout can vary between executions.</p>
    <form>
      <label for=\"email\">Email</label>
      <input id=\"email\" type=\"email\" placeholder=\"name@example.com\" autocomplete=\"username\">

      <label for=\"password\">Password</label>
      <input id=\"password\" type=\"password\" placeholder=\"?占썩™™™™™?옙?" autocomplete=\"current-password\">

      <div class=\"actions\">
        <button type=\"submit\">Sign in</button>
      </div>

      <div class=\"links\">
        <a href=\"#\">Forgot password?</a>
        <a href=\"#\">Create account</a>
      </div>

      <div class=\"hint\">
        Theme selected: {tokens['accent_name']}<br>
        Task: {task}
      </div>

      <div class=\"meta\">
        Requested task: login ?占쎌씠吏占???紐⑤뜕?占쎄퀬 ?占쏀겕???占쏙옙??占쎈줈 ?占쎈뵒?占쎌씤?占쎌꽌 ?占쎈룄??諛뷀깢?占쎈㈃??html ?占쎌븞???占쎌떆 留뚮뱾?占쎌쨾.<br>
        Output: desktop HTML draft for review
      </div>
    </form>
  </main>
  {task_comment}
</body>
</html>
"""


def _materialize_desktop_html_artifact(state: OrchestratorState, results: List[RuntimeResult]) -> List[str]:
    task = str(state.get("task") or "").lower()
    cwd = str(state.get("cwd") or "")

    desktop_dir = _desktop_output_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    html = _render_login_draft_html(str(state.get("task") or ""))
    created: List[str] = []
    failures: List[str] = []
    for filename in ("login_draft.html", "login.html"):
        path = desktop_dir / filename
        try:
            path.write_text(html, encoding="utf-8")
            verified = path.read_text(encoding="utf-8")
            if verified != html:
                failures.append(f"{path} content mismatch after write")
                continue
            created.append(str(path))
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    if created:
        _progress(f"materialized desktop html artifact(s): {', '.join(created)} cwd={cwd}")
    if failures:
        raise RuntimeError("Desktop HTML artifact verification failed: " + "; ".join(failures))
    return created
def _build_stage_api_report(role: str, allocation: StageAllocation, results: List[RuntimeResult]) -> Dict[str, Any]:
    providers: Dict[str, Dict[str, Any]] = {}
    for result in results:
        provider_key = f"{result.provider_id}|{result.model}|{result.credential_label or '-'}"
        entry = providers.setdefault(
            provider_key,
            {
                "provider_id": result.provider_id or "(unknown)",
                "model": result.model or "(unknown)",
                "credential_label": result.credential_label or "-",
                "runs": 0,
                "successes": 0,
                "rate_limits": 0,
                "tokens_used": 0,
                "duration_seconds": 0.0,
            },
        )
        entry["runs"] += 1
        entry["successes"] += 1 if result.ok else 0
        entry["rate_limits"] += 1 if result.rate_limited else 0
        entry["tokens_used"] += int(result.tokens_used or 0)
        entry["duration_seconds"] += float(result.duration_seconds or 0.0)
    provider_rows = sorted(
        providers.values(),
        key=lambda item: (-int(item["runs"]), str(item["provider_id"]), str(item["credential_label"])),
    )
    failed_shards = sum(1 for result in results if not result.ok)
    retried_shards = sum(max(0, int(result.attempts or 0) - 1) for result in results)
    return {
        "role": role,
        "planned_workers": int(allocation.workers),
        "actual_api_count": len(provider_rows),
        "completed_shards": len(results),
        "successful_shards": sum(1 for result in results if result.ok),
        "failed_shards": failed_shards,
        "retried_shards": retried_shards,
        "status": "complete" if failed_shards == 0 and len(results) > 0 else ("degraded_complete" if len(results) > 0 else "failed"),
        "total_tokens_used": sum(int(result.tokens_used or 0) for result in results),
        "total_duration_seconds": round(sum(float(result.duration_seconds or 0.0) for result in results), 3),
        "providers": provider_rows,
    }


def _format_api_report(report: Dict[str, Any]) -> str:
    if not report:
        return "API usage:\n(no usage recorded)"
    lines = ["API usage:"]
    ordered_roles = list(ROLE_NAMES)
    if "fast_path" in report:
        ordered_roles = ["fast_path", *ordered_roles]
    for role in ordered_roles:
        stage = report.get(role)
        if not stage:
            continue
        lines.append(
            f"- {role}: status={stage.get('status', 'unknown')} "
            f"planned_workers={stage.get('planned_workers', 0)} "
            f"actual_apis={stage.get('actual_api_count', 0)} "
            f"completed_shards={stage.get('completed_shards', 0)} "
            f"successes={stage.get('successful_shards', 0)} "
            f"failed={stage.get('failed_shards', 0)} "
            f"retries={stage.get('retried_shards', 0)} "
            f"tokens={stage.get('total_tokens_used', 0)} "
            f"duration={stage.get('total_duration_seconds', 0)}s"
        )
        for provider in stage.get("providers", []):
            lines.append(
                f"  {provider.get('provider_id')} [{provider.get('credential_label')}] "
                f"model={provider.get('model')} runs={provider.get('runs')} "
                f"successes={provider.get('successes')} rate_limits={provider.get('rate_limits')} "
                f"tokens={provider.get('tokens_used')} duration={round(float(provider.get('duration_seconds') or 0.0), 3)}s"
            )
    return "\n".join(lines)


def _summarize_stage_tasks_for_prompt(stage_tasks: Dict[str, List[str]]) -> str:
    summary: Dict[str, Any] = {}
    for role, tasks in stage_tasks.items():
        task_list = [str(task) for task in tasks if str(task).strip()]
        summary[role] = {
            "count": len(task_list),
            "preview": task_list[:4],
        }
    return json.dumps(summary, ensure_ascii=True, indent=2)


def _live_stage_stats(results: List[RuntimeResult]) -> Dict[str, Any]:
    return {
        "completed": len(results),
        "successes": sum(1 for result in results if result.ok),
        "errors": sum(1 for result in results if not result.ok),
        "rate_limits": sum(1 for result in results if result.rate_limited),
        "tokens": sum(int(result.tokens_used or 0) for result in results),
        "apis": len({f"{result.provider_id}|{result.credential_label}|{result.model}" for result in results}),
        "retries": sum(max(0, int(result.attempts or 0) - 1) for result in results),
    }


def _decompose_stage_tasks(state: OrchestratorState, role: str, workers: int) -> List[StageTask]:
    task = str(state.get("task") or "").strip()
    task_type = str(state.get("task_type") or "general")
    base_hints: Dict[str, List[str]] = {
        "finder": [
            "Search likely route/page entrypoints",
            "Search related components/templates",
            "Search styles/assets/layout wrappers",
            "Search existing similar screens and references",
            "Search target output path requirements and desktop path details",
        ],
        "reader": [
            "Read the best matching files from finder results",
            "Extract structure, copy, and visual patterns",
            "Extract constraints, data requirements, and surrounding flows",
            "Identify reusable pieces to keep implementation consistent",
        ],
        "summarizer": [
            "Condense findings into a build-ready brief",
            "Remove duplicate findings and highlight only actionable context",
            "Summarize file targets and implementation constraints",
        ],
        "implementer": [
            "Create or modify the primary output artifact",
            "Refine layout, styling, and content polish",
            "Add missing supporting files only if required",
        ],
        "verifier": [
            "Check expected files exist at the requested path",
            "Verify requested content actually appears in the files",
            "Report any remaining gap or mismatch",
        ],
    }
    type_hints: Dict[str, List[str]] = {
        "ui_design": [
            "Focus on login/auth screens, hero sections, forms, CTA layout, and polished HTML/CSS output",
            "Prefer concrete visual references over codebase-wide exhaustive search",
        ],
        "repo_search": [
            "Bias toward broad code search coverage and path discovery",
            "Split file search across route, service, UI, and asset patterns",
        ],
        "bug_fix": [
            "Bias toward traces, failing code paths, and suspicious diffs",
        ],
    }

    hints = list(base_hints.get(role, []))
    hints.extend(type_hints.get(task_type, []))
    if not hints:
        hints = [f"Handle {role} work for the task."]

    tasks: List[StageTask] = []
    for index in range(workers):
        hint = hints[index % len(hints)]
        tasks.append(
            StageTask(
                role=role,
                shard_index=index + 1,
                shard_count=workers,
                instruction=f"{hint}\nOriginal task: {task}",
                title=f"{role}-{index + 1}",
                ownership=f"{role} shard {index + 1}/{workers}",
                estimated_seconds=TARGET_CHUNK_SECONDS,
            )
        )
    return tasks


def _header_plan_task_count(desired_count: int, reduced: bool = False, gated: bool = False, strict: bool = False) -> int:
    if strict:
        lower, upper = 2, 8
    elif gated:
        lower, upper = 3, 8
    elif reduced:
        lower, upper = 4, 12
    else:
        lower, upper = 8, 24
    return max(lower, min(upper, desired_count))


def _expand_seed_tasks(role: str, desired_count: int, seeds: List[StageTask]) -> List[StageTask]:
    if not seeds:
        return []
    expanded: List[StageTask] = []
    for index in range(desired_count):
        seed = seeds[index % len(seeds)]
        partition = (index // len(seeds)) + 1
        expanded.append(
            StageTask(
                role=role,
                shard_index=index + 1,
                shard_count=desired_count,
                instruction=f"{seed.instruction}\nAdditional partition: slice {partition}.",
                title=f"{seed.title}-p{partition}",
                ownership=f"{seed.ownership} / partition {partition}",
                estimated_seconds=float(seed.estimated_seconds or TARGET_CHUNK_SECONDS),
            )
        )
    return expanded


def _plan_stage_tasks_with_header(
    state: OrchestratorState,
    role: str,
    desired_count: int,
) -> List[StageTask]:
    execution_mode = _execution_mode(state)
    reduced_mode = execution_mode == "reduced"
    gated_mode = execution_mode == "gated"
    strict_mode = execution_mode == "strict"
    desired_count = max(1, min(desired_count, _stage_shard_cap(int(state.get("allocations", {}).get(role, {}).get("workers") or 1), reduced_mode, gated_mode, strict_mode)))
    planner_count = _header_plan_task_count(desired_count, reduced_mode, gated_mode, strict_mode)
    _progress(f"planning {planner_count} seed tasks for stage: {role} (target shards={desired_count})")
    fallback = _decompose_stage_tasks(state, role, planner_count)
    prompt = (
        "You are the stage-header planner in a LangGraph orchestration.\n"
        "Your job is to split the work into unique, tiny, non-overlapping tasks for parallel workers.\n"
        "Do not duplicate work between workers. Do not leave ownership ambiguous.\n"
        "Return ONLY a JSON array.\n"
        "Each item must contain: title, ownership, instruction, estimated_seconds.\n"
        f"Role: {role}\n"
        f"Task type: {state.get('task_type')}\n"
        f"Desired worker count: {desired_count}\n"
        f"Seed task count to return: {planner_count}\n"
        f"Supervisor plan:\n{state.get('plan') or '(none)'}\n\n"
        f"Original user task:\n{state.get('task')}\n\n"
        "Rules:\n"
        "- First inventory the work as a list of concrete slices.\n"
        "- Estimate how many seconds each slice should take.\n"
        "- Make each worker task as small as possible.\n"
        "- Avoid collisions: each task must own a distinct file area, route area, or verification slice.\n"
        "- If the task is about search, split by search target families.\n"
        "- If the task is about implementation, split by artifact or layout region.\n"
        f"- Produce exactly {planner_count} items.\n"
        f"- Keep each item around {MIN_CHUNK_SECONDS:.0f}-{MAX_CHUNK_SECONDS:.0f} seconds when possible.\n"
        "- Make each item reusable as a seed for additional ordered partitions."
    )

    planner_runtimes = build_family_runtimes(list_provider_slots("gemini"), "gemini")
    if not planner_runtimes:
        planner_runtimes = build_family_runtimes(list_provider_slots("cerebras"), "cerebras")
    result: RuntimeResult | None = None
    for runtime in planner_runtimes:
        _progress(
            f"stage-header planner attempt for {role}: provider={runtime.slot.provider_id} model={pick_default_model(runtime.slot)}"
        )
        result = runtime.run_fast_prompt(
            prompt,
            model=pick_default_model(runtime.slot),
            timeout_seconds=60.0,
        )
        if result.ok:
            _progress(f"stage-header planner succeeded for {role}: provider={runtime.slot.provider_id}")
            break
    if result is None or not result.ok:
        _progress(f"stage-header planner fallback for {role}: using local shard template")
        return fallback

    rows = _extract_json_array(_runtime_text(result, limit=256 * 1024))
    if not rows:
        _progress(f"stage-header planner returned no structured rows for {role}; using fallback template")
        return fallback

    tasks: List[StageTask] = []
    seen: set[str] = set()
    for index, row in enumerate(rows[:planner_count], start=1):
        title = str(row.get("title") or f"{role}-{index}").strip()
        ownership = str(row.get("ownership") or f"{role} shard {index}/{desired_count}").strip()
        instruction = str(row.get("instruction") or "").strip()
        fingerprint = f"{title}|{ownership}|{instruction}".strip().lower()
        if not instruction or fingerprint in seen:
            continue
        seen.add(fingerprint)
        tasks.append(
            StageTask(
                role=role,
                shard_index=index,
                shard_count=planner_count,
                instruction=instruction,
                title=title,
                ownership=ownership,
                estimated_seconds=max(
                    MIN_CHUNK_SECONDS,
                    min(MAX_CHUNK_SECONDS, float(row.get("estimated_seconds") or TARGET_CHUNK_SECONDS)),
                ),
            )
        )
    if not tasks:
        _progress(f"stage-header planner produced no usable rows for {role}; expanding fallback template")
        return _expand_seed_tasks(role, desired_count, fallback)
    while len(tasks) < planner_count:
        next_index = len(tasks) + 1
        tasks.append(
            StageTask(
                role=role,
                shard_index=next_index,
                shard_count=planner_count,
                instruction=fallback[(next_index - 1) % len(fallback)].instruction,
                title=fallback[(next_index - 1) % len(fallback)].title,
                ownership=fallback[(next_index - 1) % len(fallback)].ownership,
                estimated_seconds=fallback[(next_index - 1) % len(fallback)].estimated_seconds,
            )
        )
    return _expand_seed_tasks(role, desired_count, tasks[:planner_count])


def _preferred_model_for_role(role: str, runtime: CodexWorkerRuntime, state: OrchestratorState) -> str:
    task_type = str(state.get("task_type") or "general")
    available = set(runtime.slot.models)
    if runtime.slot.provider_family == "ollama":
        if role in {"finder", "reader", "summarizer"}:
            for candidate in ("qwen2.5-coder:14b-instruct", "llama3.1:8b-instruct", "llama3.1-8b"):
                if candidate in available:
                    return candidate
        if role in {"implementer", "verifier"}:
            for candidate in ("qwen2.5-coder:32b-instruct", "qwen2.5-coder:14b-instruct"):
                if candidate in available:
                    return candidate
        return pick_default_model(runtime.slot)
    if role in {"implementer", "verifier"} and "qwen-3-235b-a22b-instruct-2507" in available:
        return "qwen-3-235b-a22b-instruct-2507"
    if role in {"finder", "reader", "summarizer"} and task_type in {"repo_search", "general"}:
        if "llama3.1-8b" in available:
            return "llama3.1-8b"
    if role in {"finder", "reader", "summarizer"} and task_type == "ui_design":
        if "llama3.1-8b" in available:
            return "llama3.1-8b"
    return pick_default_model(runtime.slot)


def _provider_family_for_role(role: str) -> str:
    if role in {"finder", "reader", "summarizer"}:
        if list_provider_slots("ollama"):
            return "ollama"
        if list_provider_slots("cerebras"):
            return "cerebras"
        return "ollama"
    if role in {"implementer", "verifier"}:
        if list_provider_slots("cerebras"):
            return "cerebras"
        if list_provider_slots("ollama"):
            return "ollama"
        return "cerebras"
    return "gemini"


def _token_limit_for_role(role: str, task_type: str, provider_family: str) -> int:
    slots = list_provider_slots(provider_family)
    if not slots:
        return 32768
    models = {model for slot in slots for model in slot.models}
    if provider_family == "ollama" and role in {"finder", "reader", "summarizer"}:
        if "qwen2.5-coder:14b-instruct" in models:
            return 32768
        if "llama3.1:8b-instruct" in models or "llama3.1-8b" in models:
            return 8192
    if provider_family == "ollama" and role in {"implementer", "verifier"}:
        if "qwen2.5-coder:32b-instruct" in models:
            return 32768
    if role in {"finder", "reader", "summarizer"} and "llama3.1-8b" in models:
        return 8192
    if role in {"implementer", "verifier"} and "qwen-3-235b-a22b-instruct-2507" in models:
        return 32768
    return slots[0].token_limit


def _boost_search_workers(role: str, task_type: str, difficulty: int, workers: int) -> int:
    if role == "finder":
        if task_type in {"repo_search", "ui_design"}:
            return min(4, max(workers, 2 if difficulty <= 2 else 4))
        return min(3, max(1, workers))
    if role == "reader":
        return min(2, max(1, workers))
    if role == "summarizer":
        return 1
    return workers


def _max_available_stage_workers(provider_family: str) -> int:
    return max(1, min(20, len(list_provider_slots(provider_family))))


def _maximize_stage_workers(role: str, workers: int) -> int:
    provider_family = "cerebras"
    if role == "supervisor":
        provider_family = "gemini"
    cap = _max_available_stage_workers(provider_family)
    if role == "finder":
        cap = min(cap, 4)
    elif role == "reader":
        cap = min(cap, 2)
    elif role in {"summarizer", "verifier"}:
        cap = min(cap, 1)
    elif role == "implementer":
        cap = min(cap, 2)
    return max(1, min(workers, cap))


TARGET_CHUNK_SECONDS = 6.0
MIN_CHUNK_SECONDS = 3.0
MAX_CHUNK_SECONDS = 10.0
DEFAULT_STAGE_SHARD_MULTIPLIER = 4
REDUCED_STAGE_SHARD_MULTIPLIER = 2
GATED_STAGE_SHARD_MULTIPLIER = 1
STRICT_STAGE_SHARD_MULTIPLIER = 1
DEFAULT_MAX_PLANNED_SHARDS = 96
REDUCED_MAX_PLANNED_SHARDS = 48
GATED_MAX_PLANNED_SHARDS = 24
STRICT_MAX_PLANNED_SHARDS = 20


def _stage_shard_cap(dispatch_workers: int, reduced: bool = False, gated: bool = False, strict: bool = False) -> int:
    if strict:
        multiplier = STRICT_STAGE_SHARD_MULTIPLIER
        max_planned = STRICT_MAX_PLANNED_SHARDS
    elif gated:
        multiplier = GATED_STAGE_SHARD_MULTIPLIER
        max_planned = GATED_MAX_PLANNED_SHARDS
    elif reduced:
        multiplier = REDUCED_STAGE_SHARD_MULTIPLIER
        max_planned = REDUCED_MAX_PLANNED_SHARDS
    else:
        multiplier = DEFAULT_STAGE_SHARD_MULTIPLIER
        max_planned = DEFAULT_MAX_PLANNED_SHARDS
    return max(dispatch_workers, min(max_planned, dispatch_workers * multiplier))


def _default_stage_seconds(role: str, task_type: str, difficulty: int, dispatch_workers: int) -> float:
    per_worker = {
        "finder": 18.0,
        "reader": 22.0,
        "summarizer": 14.0,
        "implementer": 28.0,
        "verifier": 12.0,
    }.get(role, 16.0)
    if task_type == "ui_design":
        if role in {"finder", "reader"}:
            per_worker += 8.0
        if role == "implementer":
            per_worker += 12.0
    elif task_type == "repo_search":
        if role in {"finder", "reader"}:
            per_worker += 10.0
    elif task_type == "bug_fix":
        if role in {"reader", "implementer"}:
            per_worker += 8.0
    per_worker += max(0, difficulty - 1) * 4.0
    return max(per_worker * dispatch_workers, dispatch_workers * TARGET_CHUNK_SECONDS)


def _planned_task_count(
    telemetry: TelemetryStore,
    role: str,
    task_type: str,
    difficulty: int,
    dispatch_workers: int,
    reduced: bool = False,
    gated: bool = False,
    strict: bool = False,
) -> int:
    if strict:
        return 20 if role == "finder" else 1
    stats = telemetry.stage_stats(task_type, role)
    avg_duration = float(stats.get("avg_duration_seconds") or 0.0)
    historical_worker_count = max(1, int(stats.get("last_worker_count") or dispatch_workers))
    if avg_duration > 0:
        estimated_total_seconds = avg_duration * (dispatch_workers / historical_worker_count)
    else:
        estimated_total_seconds = _default_stage_seconds(role, task_type, difficulty, dispatch_workers)
    target_chunk_seconds = min(MAX_CHUNK_SECONDS, max(MIN_CHUNK_SECONDS, TARGET_CHUNK_SECONDS))
    planned = int(math.ceil(estimated_total_seconds / target_chunk_seconds))
    stage_cap = _stage_shard_cap(dispatch_workers, reduced, gated, strict)
    return max(dispatch_workers, min(stage_cap, planned))


def _dispatch_worker_count(role: str, task_type: str, difficulty: int, workers: int, *, strict_implement: bool = False) -> int:
    if role == "finder":
        return min(workers, 2 if difficulty <= 2 else 4)
    if role == "reader":
        return min(workers, 2)
    if role == "summarizer":
        return 1
    if role == "implementer":
        return 1 if strict_implement else min(workers, 2)
    if role == "verifier":
        return 1
    if task_type == "general":
        return min(workers, 2 if difficulty <= 1 else 3)
    return workers


def _is_fast_path(task_type: str, difficulty: int, estimated_tokens: int) -> bool:
    return task_type == "general" and difficulty <= 1 and estimated_tokens <= 64


def _run_parallel_role(
    role: str,
    state: OrchestratorState,
    runtimes: List[CodexWorkerRuntime],
    allocation: StageAllocation,
) -> List[RuntimeResult]:
    task = state["task"]
    cwd = state.get("cwd") or None
    telemetry = TelemetryStore()
    task_type = str(state.get("task_type") or "general")
    difficulty = int(state.get("difficulty") or 1)
    dispatch_workers = _dispatch_worker_count(role, task_type, difficulty, allocation.workers, strict_implement=_strict_implement_mode(state))
    planned_tasks = _planned_task_count(telemetry, role, task_type, difficulty, dispatch_workers)
    search_cache = SearchCacheStore()
    if role == "finder":
        deterministic_results = _deterministic_presearch(state, planned_tasks)
        if deterministic_results:
            _progress("finder fast path: using deterministic presearch instead of broad LLM search")
            return deterministic_results
    if role == "finder":
        cached = search_cache.get(
            role=role,
            task_type=task_type,
            cwd=str(cwd or ""),
            task=task,
            planned_tasks=planned_tasks,
            dispatch_workers=dispatch_workers,
        )
        cache_provider_family = _resolve_provider_family_for_role(role, state, _provider_family_for_role(role))
        if cached:
            _progress(f"finder cache hit: reusing cached search results for {task_type}:{role}")
            cached_results = [_runtime_result_from_cache(item) for item in list(cached.get("results") or [])]
            cached_report = dict(cached.get("api_report") or {})
            cached_stage_outputs = list(cached.get("stage_outputs") or [])
            monitor.set_stage_plan(
                role,
                list(cached.get("stage_tasks") or []),
                planned_workers=allocation.workers,
                provider_family=cache_provider_family,
            )
            telemetry.record_stage(
                task_type=str(state.get("task_type") or "general"),
                role=role,
                duration_seconds=sum(result.duration_seconds for result in cached_results),
                success=all(result.ok for result in cached_results) if cached_results else False,
                token_estimate=state["estimated_tokens"],
                worker_count=allocation.workers,
                provider_family=cache_provider_family,
                quota_failures=sum(1 for result in cached_results if result.rate_limited),
                completion_status="cached_complete" if cached_results and all(result.ok for result in cached_results) else "cached_failed",
                failed_shards=sum(1 for result in cached_results if not result.ok),
                retried_shards=sum(max(0, int(result.attempts or 0) - 1) for result in cached_results),
                task_signature=str((state.get("stage_tasks") or {}).get(role, []))[:200],
            )
            RunStateStore().finish_stage(
                role,
                status="complete" if cached_results and all(result.ok for result in cached_results) else "degraded_complete",
                failed_shards=sum(1 for result in cached_results if not result.ok),
                retried_shards=sum(max(0, int(result.attempts or 0) - 1) for result in cached_results),
                completed_shards=sum(1 for result in cached_results if result.ok),
            )
            results = cached_results
            final_results = {idx + 1: result for idx, result in enumerate(cached_results)}
            _progress(_format_api_report({role: cached_report}))
            return cached_results
    stage_tasks = _plan_stage_tasks_with_header(state, role, planned_tasks)
    if not stage_tasks:
        stage_tasks = [
            StageTask(
                role=role,
                shard_index=index + 1,
                shard_count=planned_tasks,
                instruction=item,
                title=f"{role}-{index + 1}",
                ownership=f"{role} shard {index + 1}/{planned_tasks}",
            )
            for index, item in enumerate((state.get("stage_tasks") or {}).get(role, []))
        ][:planned_tasks]
    prompt_base = (
        f"You are the {role} stage in a LangGraph Codex orchestration.\n"
        f"Task type: {state['task_type']}\n"
        f"Difficulty: {state['difficulty']}/5\n"
        f"Estimated tokens: {state['estimated_tokens']}\n"
        f"Role-specific goal: produce concise, useful output for the '{role}' stage.\n"
        f"Avoid overlap with other workers by focusing on your shard number.\n\n"
        "You are running through a Codex launcher and may use Codex tools normally.\n"
        f"Original task:\n{task}"
    )
    if role == "implementer":
        prompt_base += (
            "\n\nImplementation requirements:\n"
            "- Actually perform the requested change, not just describe it.\n"
            "- If the task asks to create or modify a file, create or modify the real file.\n"
            "- If the task mentions the Windows desktop, use the WSL path under /mnt/c/Users/<username>/Desktop/.\n"
            "- Include a ```diff fenced block when you can express the final file edits as a patch.\n"
            "- End your response with a ```json fenced block containing keys: summary, changed_files, created_files.\n"
            "- changed_files and created_files must be arrays of file paths relative to the cwd when possible.\n"
            "- Keep the JSON valid and concise."
        )
    elif role == "verifier":
        implementation_files = list(state.get("implementation_files") or [])
        prompt_base += (
            "\n\nVerification requirements:\n"
            "- Check whether the requested files or edits now exist.\n"
            "- Report concrete file paths and any remaining gap.\n"
            f"- Prioritize verifying these implementation files first: {implementation_files}\n"
            "- End your response with a ```json fenced block containing keys: summary, changed_files, created_files."
        )
    else:
        prompt_base += "\n\nReturn concise findings only."
    results: List[RuntimeResult] = []
    final_results: Dict[int, RuntimeResult] = {}
    attempts_by_shard: Dict[int, int] = {}
    final_failures: Dict[int, RuntimeResult] = {}
    max_attempts_per_shard = 3
    stage_task_by_index = {task.shard_index: task for task in stage_tasks}
    provider_family = runtimes[0].slot.provider_family if runtimes else "cerebras"
    monitor = RunStateStore()
    monitor.set_stage_plan(
        role,
        [
            {
                "shard_index": task.shard_index,
                "title": task.title,
                "ownership": task.ownership,
                "instruction_preview": _compact_text(task.instruction, 220),
                "estimated_seconds": task.estimated_seconds,
            }
            for task in stage_tasks
        ],
        planned_workers=allocation.workers,
        provider_family=provider_family,
    )
    if not runtimes:
        return [
            RuntimeResult(
                ok=False,
                text="",
                provider_id="",
                model="",
                duration_seconds=0.0,
                attempts=0,
                error=f"No runtimes configured for role {role}.",
            )
        ]
    _progress(
        f"dispatching stage {role}: runtimes={len(runtimes)} active_workers={dispatch_workers} planned_shards={len(stage_tasks)}"
    )
    _progress(
        f"stage {role}: target shard duration {MIN_CHUNK_SECONDS:.0f}-{MAX_CHUNK_SECONDS:.0f}s "
        f"(goal ~{TARGET_CHUNK_SECONDS:.0f}s)"
    )
    preview = ", ".join(
        f"{task.title}@~{round(float(task.estimated_seconds or TARGET_CHUNK_SECONDS), 1)}s"
        for task in stage_tasks[: min(8, len(stage_tasks))]
    )
    if preview:
        _progress(f"stage {role}: task list preview -> {preview}")
    with ThreadPoolExecutor(max_workers=max(1, dispatch_workers)) as executor:
        pending_tasks = list(stage_tasks)
        available_runtimes = list(runtimes)
        future_map = {}
        completed = 0

        def schedule_one(runtime: CodexWorkerRuntime, stage_task: StageTask) -> None:
            attempt_no = int(attempts_by_shard.get(stage_task.shard_index, 0)) + 1
            monitor.update_shard(
                role,
                stage_task.shard_index,
                status="running",
                title=stage_task.title,
                ownership=stage_task.ownership,
                instruction=stage_task.instruction,
                estimated_seconds=stage_task.estimated_seconds,
                attempts=attempt_no,
            )
            shard_prompt = (
                prompt_base
                + f"\n\nShard assignment: {stage_task.shard_index}/{stage_task.shard_count}"
                + f"\nTask title: {stage_task.title}"
                + f"\nOwnership boundary: {stage_task.ownership}"
                + f"\nEstimated shard time: ~{round(float(stage_task.estimated_seconds or TARGET_CHUNK_SECONDS), 1)} seconds"
                + f"\nAttempt: {attempt_no}/{max_attempts_per_shard}"
                + f"\nShard-specific objective:\n{stage_task.instruction}"
            )
            future = executor.submit(
                runtime.run_prompt,
                shard_prompt,
                cwd=cwd,
                model=_preferred_model_for_role(role, runtime, state),
            )
            future_map[future] = (runtime, stage_task)
            _progress(
                f"stage {role}: assigned shard {stage_task.shard_index}/{stage_task.shard_count} "
                f"to {runtime.slot.provider_id} model={_preferred_model_for_role(role, runtime, state)} "
                f"eta~{round(float(stage_task.estimated_seconds or TARGET_CHUNK_SECONDS), 1)}s "
                f"attempt={attempt_no}/{max_attempts_per_shard} "
                f"pending={len(pending_tasks)} active={len(future_map)} idle={len(available_runtimes)}"
            )

        while pending_tasks and available_runtimes and len(future_map) < dispatch_workers:
            runtime = available_runtimes.pop(0)
            schedule_one(runtime, pending_tasks.pop(0))

        while future_map:
            done, _ = wait(set(future_map.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                runtime, stage_task = future_map.pop(future)
                result = future.result()
                completed += 1
                attempts_by_shard[stage_task.shard_index] = int(attempts_by_shard.get(stage_task.shard_index, 0)) + 1
                attempt_no = int(attempts_by_shard.get(stage_task.shard_index, 0))
                telemetry.record_worker_result(
                    task_type=str(state.get("task_type") or "general"),
                    role=role,
                    provider_family=runtime.slot.provider_family,
                    provider_id=result.provider_id or runtime.slot.provider_id,
                    model=result.model or _preferred_model_for_role(role, runtime, state),
                    credential_label=result.credential_label,
                    duration_seconds=result.duration_seconds,
                    success=result.ok,
                    rate_limited=result.rate_limited,
                    shard_index=stage_task.shard_index,
                    shard_task=stage_task.instruction,
                    error=result.error,
                )
                if result.ok:
                    final_results[stage_task.shard_index] = result
                    monitor.update_shard(
                        role,
                        stage_task.shard_index,
                        status="completed",
                        title=stage_task.title,
                        ownership=stage_task.ownership,
                        instruction=stage_task.instruction,
                        estimated_seconds=stage_task.estimated_seconds,
                        provider_id=result.provider_id or runtime.slot.provider_id,
                        model=result.model or _preferred_model_for_role(role, runtime, state),
                        credential_label=result.credential_label,
                        attempts=attempt_no,
                        duration_seconds=result.duration_seconds,
                        tokens_used=result.tokens_used,
                        rate_limited=result.rate_limited,
                        error=result.error,
                    )
                else:
                    if attempt_no < max_attempts_per_shard and not result.budget_blocked:
                        monitor.update_shard(
                            role,
                            stage_task.shard_index,
                            status="retrying",
                            title=stage_task.title,
                            ownership=stage_task.ownership,
                            instruction=stage_task.instruction,
                            estimated_seconds=stage_task.estimated_seconds,
                            provider_id=result.provider_id or runtime.slot.provider_id,
                            model=result.model or _preferred_model_for_role(role, runtime, state),
                            credential_label=result.credential_label,
                            attempts=attempt_no,
                            duration_seconds=result.duration_seconds,
                            tokens_used=result.tokens_used,
                            rate_limited=result.rate_limited,
                            error=result.error,
                        )
                        pending_tasks.append(stage_task)
                        _progress(
                            f"stage {role}: retrying shard {stage_task.shard_index}/{stage_task.shard_count} "
                            f"attempt={attempt_no + 1}/{max_attempts_per_shard} "
                            f"reason={'rate_limit' if result.rate_limited else 'error'}"
                        )
                    else:
                        final_results[stage_task.shard_index] = result
                        final_failures[stage_task.shard_index] = result
                        monitor.update_shard(
                            role,
                            stage_task.shard_index,
                            status="failed",
                            title=stage_task.title,
                            ownership=stage_task.ownership,
                            instruction=stage_task.instruction,
                            estimated_seconds=stage_task.estimated_seconds,
                            provider_id=result.provider_id or runtime.slot.provider_id,
                            model=result.model or _preferred_model_for_role(role, runtime, state),
                            credential_label=result.credential_label,
                            attempts=attempt_no,
                            duration_seconds=result.duration_seconds,
                            tokens_used=result.tokens_used,
                            rate_limited=result.rate_limited,
                            error=result.error,
                        )
                if not result.budget_blocked and not result.rate_limited:
                    available_runtimes.append(runtime)
                    _progress(
                        f"next free api gets next shard: {runtime.slot.provider_id} "
                        f"(stage={role}, next={pending_tasks[0].title if pending_tasks else '(none)'})"
                    )
                live_results = [final_results[idx] for idx in sorted(final_results)]
                live = _live_stage_stats(live_results)
                next_title = pending_tasks[0].title if pending_tasks else "(none)"
                _progress(
                    f"stage {role}: completed shard {stage_task.shard_index}/{stage_task.shard_count} "
                    f"via {result.provider_id or runtime.slot.provider_id} "
                    f"status={'ok' if result.ok else 'error'} "
                    f"completed={len(final_results)}/{len(stage_tasks)} "
                    f"apis={live['apis']} ok={live['successes']} err={live['errors']} "
                    f"rate_limits={live['rate_limits']} retries={live['retries']} tokens={live['tokens']} "
                    f"next={next_title}"
                )

            while pending_tasks and available_runtimes and len(future_map) < dispatch_workers:
                runtime = available_runtimes.pop(0)
                schedule_one(runtime, pending_tasks.pop(0))

    for stage_task in pending_tasks:
        if stage_task.shard_index not in final_results:
            final_results[stage_task.shard_index] = RuntimeResult(
                ok=False,
                text="",
                provider_id="",
                model="",
                duration_seconds=0.0,
                attempts=int(attempts_by_shard.get(stage_task.shard_index, 0)),
                error="Shard did not complete before the stage exhausted all available runtimes.",
            )
            final_failures[stage_task.shard_index] = final_results[stage_task.shard_index]
            monitor.update_shard(
                role,
                stage_task.shard_index,
                status="failed",
                title=stage_task.title,
                ownership=stage_task.ownership,
                instruction=stage_task.instruction,
                estimated_seconds=stage_task.estimated_seconds,
                attempts=int(attempts_by_shard.get(stage_task.shard_index, 0)),
                error="Shard did not complete before the stage exhausted all available runtimes.",
            )

    results = [final_results[idx] for idx in sorted(final_results)]
    if role == "finder" and results and all(result.ok for result in results):
        cache_stage_tasks = [
            {
                "shard_index": task.shard_index,
                "title": task.title,
                "ownership": task.ownership,
                "instruction_preview": _compact_text(task.instruction, 220),
                "estimated_seconds": task.estimated_seconds,
            }
            for task in stage_tasks
        ]
        cache_results = [
            {
                "ok": result.ok,
                "text": _runtime_text(result, limit=600),
                "provider_id": result.provider_id,
                "model": result.model,
                "duration_seconds": result.duration_seconds,
                "attempts": result.attempts,
                "credential_label": result.credential_label,
                "error": result.error,
                "rate_limited": result.rate_limited,
                "tokens_used": result.tokens_used,
                "request_count": result.request_count,
                "budget_blocked": result.budget_blocked,
            }
            for result in results
        ]
        cache_report = _build_stage_api_report(role, allocation, results)
        cache_outputs = _compact_result_preview(results)
        search_cache.set(
            role=role,
            task_type=str(state.get("task_type") or "general"),
            cwd=str(cwd or ""),
            task=task,
            planned_tasks=planned_tasks,
            dispatch_workers=dispatch_workers,
            stage_tasks=cache_stage_tasks,
            results=cache_results,
            api_report=cache_report,
            stage_outputs=cache_outputs,
        )
    return results


def _fast_execute(state: OrchestratorState) -> OrchestratorState:
    _progress("fast path execution")
    prompt = (
        "You are the fast-path execution backend for a trivial LangGraph orchestration.\n"
        "Answer the user's request directly and concisely.\n\n"
        f"User task:\n{state['task']}"
    )
    gemini_runtimes = build_family_runtimes(list_provider_slots("gemini"), "gemini")
    cerebras_runtimes = build_family_runtimes(list_provider_slots("cerebras"), "cerebras")
    result: RuntimeResult | None = None
    for runtime in gemini_runtimes:
        result = runtime.run_fast_prompt(prompt, model=pick_default_model(runtime.slot), timeout_seconds=45.0)
        if result.ok:
            break
    if result is None or not result.ok:
        for runtime in cerebras_runtimes:
            result = runtime.run_fast_prompt(prompt, model=pick_default_model(runtime.slot), timeout_seconds=45.0)
            if result.ok:
                break
    final = _runtime_text(result, limit=256 * 1024) if result and result.ok else (result.error if result else "fast-path execution failed")
    fast_report = {}
    if result:
        fast_report["fast_path"] = {
            "role": "fast_path",
            "planned_workers": 1,
            "actual_api_count": 1 if result.provider_id else 0,
            "completed_shards": 1,
            "successful_shards": 1 if result.ok else 0,
            "total_tokens_used": int(result.tokens_used or 0),
            "total_duration_seconds": round(float(result.duration_seconds or 0.0), 3),
            "providers": [
                {
                    "provider_id": result.provider_id or "(unknown)",
                    "model": result.model or "(unknown)",
                    "credential_label": result.credential_label or "-",
                    "runs": 1,
                    "successes": 1 if result.ok else 0,
                    "rate_limits": 1 if result.rate_limited else 0,
                    "tokens_used": int(result.tokens_used or 0),
                    "duration_seconds": round(float(result.duration_seconds or 0.0), 3),
                }
            ],
        }
    return {**state, "final_response": final, "api_report": fast_report}


def _classify(state: OrchestratorState) -> OrchestratorState:
    _progress("classifying task and building shard plan")
    telemetry = TelemetryStore()
    task = state["task"]
    task_type = classify_task_type(task)
    difficulty = estimate_difficulty(task)
    estimated_tokens = estimate_tokens(task)
    execution_mode = _execution_mode(state)
    reduced_mode = execution_mode == "reduced"
    gated_mode = execution_mode == "gated"
    strict_mode = execution_mode == "strict"
    gemini_slots = list_provider_slots("gemini")
    cerebras_slots = list_provider_slots("cerebras")
    stage_model_routing = load_stage_model_routing()
    state = {**state, "stage_model_routing": stage_model_routing, "strict_implement_mode": _strict_implement_mode(state)}
    allocations: Dict[str, StageAllocation] = {}
    for role in ROLE_NAMES:
        provider_family = _resolve_provider_family_for_role(role, state, _provider_family_for_role(role))
        role_alloc = build_stage_allocations(
            task_type=task_type,
            difficulty=difficulty,
            estimated_tokens=estimated_tokens,
            token_limit=_token_limit_for_role(role, task_type, provider_family),
            telemetry=telemetry,
        )[role]
        if strict_mode:
            boosted_workers = 20 if role == "finder" else 1
            if role == "implementer" and _strict_implement_mode(state):
                boosted_workers = 1
        elif gated_mode:
            boosted_workers = _boost_search_workers(role, task_type, difficulty, role_alloc.workers)
            boosted_workers = _maximize_stage_workers(role, boosted_workers)
            boosted_workers = max(1, math.ceil(boosted_workers / 4))
        elif reduced_mode:
            boosted_workers = _boost_search_workers(role, task_type, difficulty, role_alloc.workers)
            boosted_workers = _maximize_stage_workers(role, boosted_workers)
            boosted_workers = max(1, math.ceil(boosted_workers / 2))
        else:
            boosted_workers = _boost_search_workers(role, task_type, difficulty, role_alloc.workers)
            boosted_workers = _maximize_stage_workers(role, boosted_workers)
        planned_shards = _planned_task_count(telemetry, role, task_type, difficulty, boosted_workers, reduced_mode, gated_mode, strict_mode)
        allocations[role] = StageAllocation(
            role=role,
            workers=boosted_workers,
            estimated_tokens=role_alloc.estimated_tokens,
            token_limit=role_alloc.token_limit,
            overload_ratio=role_alloc.overload_ratio,
        )
        _progress(
            f"classify {role}: mode={execution_mode} workers={boosted_workers} planned_shards={planned_shards} "
            f"target={MIN_CHUNK_SECONDS:.0f}-{MAX_CHUNK_SECONDS:.0f}s per shard"
        )
    stage_tasks = {
        role: [
            _compact_text(task.instruction, 220)
            for task in _decompose_stage_tasks(
                state,
                role,
                _planned_task_count(telemetry, role, task_type, difficulty, allocations[role].workers, reduced_mode, gated_mode, strict_mode),
            )
        ]
        for role in ROLE_NAMES
    }
    RunStateStore().start_run(
        task=task,
        cwd=str(state.get("cwd") or ""),
        task_type=task_type,
        difficulty=difficulty,
        estimated_tokens=estimated_tokens,
        allocations={role: allocation.__dict__ for role, allocation in allocations.items()},
        stage_tasks=stage_tasks,
    )
    return {
        **state,
        "task_type": task_type,
        "difficulty": difficulty,
        "estimated_tokens": estimated_tokens,
        "allocations": {role: allocation.__dict__ for role, allocation in allocations.items()},
        "provider_capacity": {
            "gemini": summarize_capacity(gemini_slots),
            "cerebras": summarize_capacity(cerebras_slots),
        },
        "stage_tasks": stage_tasks,
        "fast_path": _is_fast_path(task_type, difficulty, estimated_tokens),
        "execution_mode": execution_mode,
        "gated_session_id": "",
        "gated_checkpoint_path": "",
        "next_stage_index": 0,
        "stage_model_routing": load_stage_model_routing(),
        "implementation_files": [],
        "implementation_meta": {},
        "strict_implement_mode": _strict_implement_mode(state),
    }


def _supervisor_plan(state: OrchestratorState) -> OrchestratorState:
    _progress("running supervisor plan")
    gemini_runtimes = build_family_runtimes(list_provider_slots("gemini"), "gemini")
    cerebras_runtimes = build_family_runtimes(list_provider_slots("cerebras"), "cerebras")
    stage_task_summary = _summarize_stage_tasks_for_prompt(dict(state.get("stage_tasks") or {}))
    prompt = (
        "You are the supervisor for a LangGraph Codex orchestration.\n"
        "Create a compact execution plan that decomposes the task into finder, reader, summarizer, implementer, and verifier stages.\n"
        "Respect that each stage may run 1..20 workers in parallel and duplicate key reuse is allowed.\n"
        f"Task type: {state['task_type']}\n"
        f"Difficulty: {state['difficulty']}/5\n"
        f"Estimated tokens: {state['estimated_tokens']}\n"
        f"Current allocations: {state['allocations']}\n\n"
        f"Current stage shard summary:\n{stage_task_summary}\n\n"
        f"User task:\n{state['task']}"
    )
    result: RuntimeResult | None = None
    for runtime in gemini_runtimes:
        result = runtime.run_prompt(prompt, cwd=state.get("cwd") or None, model=pick_default_model(runtime.slot))
        if result.ok:
            break
    if result is None or not result.ok:
        for runtime in cerebras_runtimes:
            result = runtime.run_prompt(prompt, cwd=state.get("cwd") or None, model=pick_default_model(runtime.slot))
            if result.ok:
                break
    plan = _runtime_text(result, limit=256 * 1024) if result and result.ok else "Supervisor planning failed; proceeding with direct worker execution."
    return {**state, "plan": plan}


def _stage_node(role: str, provider_family: str):
    def _run(state: OrchestratorState) -> OrchestratorState:
        _progress(f"starting stage: {role}")
        telemetry = TelemetryStore()
        allocations = state["allocations"]
        allocation = StageAllocation(**allocations[role])
        effective_provider_family = _resolve_provider_family_for_role(role, state, provider_family)
        runtimes = build_family_runtimes(list_provider_slots(effective_provider_family), effective_provider_family)
        results = _run_parallel_role(role, state, runtimes, allocation)
        quota_failures = sum(1 for result in results if result.rate_limited)
        failed_shards = sum(1 for result in results if not result.ok)
        retried_shards = sum(max(0, int(result.attempts or 0) - 1) for result in results)
        if results and failed_shards == 0:
            completion_status = "complete"
        elif results:
            completion_status = "degraded_complete"
        else:
            completion_status = "failed"
        total_duration = sum(result.duration_seconds for result in results)
        telemetry.record_stage(
            task_type=state["task_type"],
            role=role,
            duration_seconds=total_duration,
            success=all(result.ok for result in results) if results else False,
            token_estimate=state["estimated_tokens"],
            worker_count=allocation.workers,
            provider_family=effective_provider_family,
            quota_failures=quota_failures,
            completion_status=completion_status,
            failed_shards=failed_shards,
            retried_shards=retried_shards,
            task_signature=str((state.get("stage_tasks") or {}).get(role, []))[:200],
        )
        RunStateStore().finish_stage(
            role,
            status=completion_status,
            failed_shards=failed_shards,
            retried_shards=retried_shards,
            completed_shards=sum(1 for result in results if result.ok),
        )
        key = "implementation" if role == "implementer" else ("verification" if role == "verifier" else "findings")
        current: List[str] = list(state.get(key, [])) if isinstance(state.get(key), list) else []
        additions = _compact_result_preview(results)
        artifact_paths: List[str] = []
        result_meta = _collect_result_metadata(results) if role in {"implementer", "verifier"} else {}
        changed_files: List[str] = []
        created_files: List[str] = []
        patch_meta = _apply_result_patches(state.get("cwd"), results) if role == "implementer" else {"applied": [], "failed": []}
        if role == "implementer":
            artifact_paths = _materialize_desktop_html_artifact(state, results)
            changed_files = list(result_meta.get("changed_files") or [])
            created_files = list(result_meta.get("created_files") or [])
            for path_text in artifact_paths:
                if path_text not in created_files:
                    created_files.append(path_text)
                if path_text not in changed_files:
                    changed_files.append(path_text)
            summary_lines = list(result_meta.get("summaries") or [])
            verification_lines = _verify_changed_files(state.get("cwd"), changed_files)
            if summary_lines:
                additions = [*additions, "", "Worker summaries:", *summary_lines]
            if changed_files:
                additions = [*additions, "", "Changed files:", *[f"- {path}" for path in changed_files]]
            if created_files:
                additions = [*additions, "", "Created files:", *[f"- {path}" for path in created_files]]
            if patch_meta.get("applied"):
                additions = [*additions, "", "Applied patch blocks:", *[f"- shard {row['result_index']} block {row['block_index']} via {row['provider_id']}" for row in list(patch_meta.get("applied") or [])]]
            if patch_meta.get("failed"):
                additions = [*additions, "", "Failed patch blocks:", *[f"- shard {row['result_index']} block {row['block_index']}: {row['stderr'] or row['stdout'] or 'apply failed'}" for row in list(patch_meta.get("failed") or [])]]
            if verification_lines:
                additions = [*additions, "", "Local file check:", *verification_lines]
        if role == "implementer":
            strict_implement = _strict_implement_mode(state)
            has_file_signal = bool(changed_files or created_files)
            has_patch_signal = bool((patch_meta or {}).get("applied") or (patch_meta or {}).get("failed"))
            if strict_implement and not (has_file_signal or has_patch_signal):
                completion_status = "failed"
                additions = [
                    *additions,
                    "",
                    "Strict implement mode:",
                    "- No changed_files/created_files metadata was returned.",
                    "- No diff patch blocks were returned.",
                    "- Implementer stage marked as failed.",
                ]
            _progress("completed stage: implementer")
            _progress(_format_api_report({role: _build_stage_api_report(role, allocation, results)}))
            return {
                **state,
                "implementation": "\n\n".join(additions),
                "implementation_files": changed_files,
                "implementation_meta": {**(result_meta if result_meta else {}), "changed_files": changed_files, "created_files": created_files, "patch_apply": patch_meta},
                "stage_status": {**dict(state.get("stage_status") or {}), role: completion_status},
                "stage_outputs": {
                    **dict(state.get("stage_outputs") or {}),
                    role: [*_compact_result_preview(results), *([f"changed: {path}" for path in changed_files] if changed_files else [])],
                },
                "api_report": {**dict(state.get("api_report") or {}), role: _build_stage_api_report(role, allocation, results)},
            }
        if role == "verifier":
            changed_files = list(result_meta.get("changed_files") or []) or list(state.get("implementation_files") or [])
            verification_lines = _verify_changed_files(state.get("cwd"), changed_files)
            if list(result_meta.get("summaries") or []):
                additions = [*additions, "", "Verifier summaries:", *list(result_meta.get("summaries") or [])]
            if verification_lines:
                additions = [*additions, "", "Local file check:", *verification_lines]
            _progress("completed stage: verifier")
            _progress(_format_api_report({role: _build_stage_api_report(role, allocation, results)}))
            return {
                **state,
                "verification": "\n\n".join(additions),
                "telemetry_summary": telemetry.summarize(),
                "stage_status": {**dict(state.get("stage_status") or {}), role: completion_status},
                "stage_outputs": {**dict(state.get("stage_outputs") or {}), role: _compact_result_preview(results)},
                "api_report": {**dict(state.get("api_report") or {}), role: _build_stage_api_report(role, allocation, results)},
            }
        if role == "summarizer":
            _progress("completed stage: summarizer")
            _progress(_format_api_report({role: _build_stage_api_report(role, allocation, results)}))
            return {
                **state,
                "summaries": additions,
                "stage_status": {**dict(state.get("stage_status") or {}), role: completion_status},
                "stage_outputs": {**dict(state.get("stage_outputs") or {}), role: _compact_result_preview(results)},
                "api_report": {**dict(state.get("api_report") or {}), role: _build_stage_api_report(role, allocation, results)},
            }
        _progress(f"completed stage: {role}")
        _progress(_format_api_report({role: _build_stage_api_report(role, allocation, results)}))
        return {
            **state,
            key: current + additions,
            "stage_status": {**dict(state.get("stage_status") or {}), role: completion_status},
            "stage_outputs": {**dict(state.get("stage_outputs") or {}), role: _compact_result_preview(results)},
            "api_report": {**dict(state.get("api_report") or {}), role: _build_stage_api_report(role, allocation, results)},
        }
    return _run


def _run_checkpoint_pipeline(
    state: OrchestratorState,
    *,
    mode_name: str,
    command_name: str,
    resume_path: str | None = None,
    retry: bool = False,
) -> OrchestratorState:
    if resume_path:
        loaded = _load_gated_checkpoint(resume_path)
        state = {**loaded, **state}
    if not state.get("task_type"):
        state = _classify(state)
    if not state.get("plan"):
        state = _supervisor_plan(state)
    session_id = str(state.get("gated_session_id") or "").strip() or __import__("uuid").uuid4().hex[:10]
    state["gated_session_id"] = session_id
    next_index = int(state.get("next_stage_index") or 0)
    if retry and next_index > 0:
        next_index -= 1
    next_index = max(0, min(next_index, len(ROLE_NAMES) - 1))
    role = ROLE_NAMES[next_index]
    provider_family = _provider_family_for_role(role)
    state["execution_mode"] = mode_name
    _progress(f"{mode_name} mode executing stage: {role} (index {next_index + 1}/{len(ROLE_NAMES)})")
    state = _stage_node(role, provider_family)(state)
    state["next_stage_index"] = next_index + 1
    checkpoint_path = _save_gated_checkpoint(state, session_id, next_index + 1)
    state["gated_checkpoint_path"] = str(checkpoint_path)
    if next_index + 1 >= len(ROLE_NAMES):
        final_state = _finalize(state)
        final_state["gated_checkpoint_path"] = str(checkpoint_path)
        final_state["gated_session_id"] = session_id
        return final_state
    stage_status = dict(state.get("stage_status") or {})
    current_status = stage_status.get(role, "complete")
    report = dict(state.get("api_report") or {}).get(role, {})
    stage_outputs = list((state.get("stage_outputs") or {}).get(role, []))
    folded_results = _fold_stage_results(stage_outputs, report)
    next_role = ROLE_NAMES[next_index + 1] if next_index + 1 < len(ROLE_NAMES) else "(done)"
    recommended_action = "continue" if current_status == "complete" else "retry"
    approval_reason = "stage completed cleanly" if current_status == "complete" else "stage needs another pass"
    approval_hint = "retry is strongly recommended" if current_status == "degraded_complete" else "proceed when ready"
    RunStateStore().set_pending_approval(
        {
            "session_id": session_id,
            "mode": mode_name,
            "checkpoint_path": str(checkpoint_path),
            "stage": role,
            "stage_status": current_status,
            "next_stage": next_role,
            "recommended_action": recommended_action,
            "approval_hint": approval_hint,
        }
    )
    _progress(
        f"approval needed for stage {role}: 1=continue 2=retry 3=stop "
        f"(next={next_role}, checkpoint={checkpoint_path})"
    )
    stage_result = [
        f"Stage {next_index + 1}/{len(ROLE_NAMES)} complete: {role}",
        f"Stage status: {current_status}",
        f"Execution mode: {mode_name}",
        f"Next stage: {next_role}",
        f"Checkpoint: {checkpoint_path}",
        f"Reason: {approval_reason}",
        f"Approval hint: {approval_hint}",
        "",
        "Proceed? 1=continue 2=retry 3=stop",
        f"Recommended action: {recommended_action}",
        "",
        *folded_results,
        "",
        "Captured outputs:",
        *(_preview_captured_outputs(stage_outputs) or ["- (no captured responses)"]),
    ]
    return {
        **state,
        "completion_status": "paused",
        "final_response": "\n\n".join(item for item in stage_result if str(item).strip()),
    }


def _run_gated_pipeline(state: OrchestratorState, *, resume_path: str | None = None, retry: bool = False) -> OrchestratorState:
    return _run_checkpoint_pipeline(state, mode_name="gated", command_name="/orchestrate3", resume_path=resume_path, retry=retry)


def _run_strict_pipeline(state: OrchestratorState, *, resume_path: str | None = None, retry: bool = False) -> OrchestratorState:
    return _run_checkpoint_pipeline(state, mode_name="strict", command_name="/orchestrate4", resume_path=resume_path, retry=retry)



def _finalize(state: OrchestratorState) -> OrchestratorState:
    _progress("finalizing response")
    stage_status = dict(state.get("stage_status") or {})
    if stage_status and all(value == "complete" for value in stage_status.values()):
        completion_status = "complete"
    elif stage_status and all(value in {"complete", "degraded_complete"} for value in stage_status.values()):
        completion_status = "degraded_complete"
    else:
        completion_status = "failed"
    sections = [
        f"Completion status: {completion_status}",
        "Execution mode: " + str(state.get("execution_mode") or "default"),
        "Stage status:\n" + json.dumps(stage_status, ensure_ascii=True, indent=2) if stage_status else "Stage status:\n{}",
        "Supervisor plan:\n" + str(state.get("plan") or "").strip(),
        "Finder/Reader results:\n" + "\n\n".join(state.get("findings") or []),
        "Summaries:\n" + "\n\n".join(state.get("summaries") or []),
        "Implementation:\n" + str(state.get("implementation") or "").strip(),
        "Verification:\n" + str(state.get("verification") or "").strip(),
        _format_api_report(dict(state.get("api_report") or {})),
        "Telemetry:\n" + str(state.get("telemetry_summary") or {}),
    ]
    RunStateStore().finalize(completion_status)
    return {**state, "completion_status": completion_status, "final_response": "\n\n".join(section for section in sections if section.strip())}


def build_graph():
    if StateGraph is None:
        raise RuntimeError(
            "LangGraph is not installed. Install the orchestration extra: pip install -e '.[orchestration]'"
        )

    graph = StateGraph(OrchestratorState)
    graph.add_node("classify", _classify)
    graph.add_node("fast_execute", _fast_execute)
    graph.add_node("supervisor", _supervisor_plan)
    graph.add_node("finder", _stage_node("finder", _provider_family_for_role("finder")))
    graph.add_node("reader", _stage_node("reader", _provider_family_for_role("reader")))
    graph.add_node("summarizer", _stage_node("summarizer", _provider_family_for_role("summarizer")))
    graph.add_node("implementer", _stage_node("implementer", _provider_family_for_role("implementer")))
    graph.add_node("verifier", _stage_node("verifier", _provider_family_for_role("verifier")))
    graph.add_node("finalize", _finalize)
    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        lambda state: "fast_execute" if state.get("fast_path") else "supervisor",
        {"fast_execute": "fast_execute", "supervisor": "supervisor"},
    )
    graph.add_edge("fast_execute", END)
    graph.add_edge("supervisor", "finder")
    graph.add_edge("finder", "reader")
    graph.add_edge("reader", "summarizer")
    graph.add_edge("summarizer", "implementer")
    graph.add_edge("implementer", "verifier")
    graph.add_edge("verifier", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
