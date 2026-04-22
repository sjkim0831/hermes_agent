"""LangGraph workflow for supervisor/worker Codex orchestration."""

from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Dict, List, TypedDict

from .config import ROLE_NAMES, list_provider_slots, pick_default_model, summarize_capacity
from .runtimes import CodexWorkerRuntime, RuntimeResult, build_family_runtimes
from .scheduler import StageAllocation, build_stage_allocations
from .telemetry import TelemetryStore, classify_task_type, estimate_difficulty, estimate_tokens

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


@dataclass(frozen=True)
class StageTask:
    role: str
    shard_index: int
    shard_count: int
    instruction: str
    title: str = ""
    ownership: str = ""


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
        preview = (result.text if result.ok else result.error).strip()
        if len(preview) > 500:
            preview = preview[:500].rstrip() + "\n...[truncated]"
        lines.append(
            f"[{status}] provider={result.provider_id} model={result.model} credential={result.credential_label or '-'} "
            f"attempts={result.attempts} duration={result.duration_seconds:.2f}s\n{preview}"
        )
    return lines


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
            )
        )
    return tasks


def _plan_stage_tasks_with_header(
    state: OrchestratorState,
    role: str,
    desired_count: int,
) -> List[StageTask]:
    fallback = _decompose_stage_tasks(state, role, desired_count)
    prompt = (
        "You are the stage-header planner in a LangGraph orchestration.\n"
        "Your job is to split the work into unique, tiny, non-overlapping tasks for parallel workers.\n"
        "Do not duplicate work between workers. Do not leave ownership ambiguous.\n"
        "Return ONLY a JSON array.\n"
        "Each item must contain: title, ownership, instruction.\n"
        f"Role: {role}\n"
        f"Task type: {state.get('task_type')}\n"
        f"Desired worker count: {desired_count}\n"
        f"Supervisor plan:\n{state.get('plan') or '(none)'}\n\n"
        f"Original user task:\n{state.get('task')}\n\n"
        "Rules:\n"
        "- Make each worker task as small as possible.\n"
        "- Avoid collisions: each task must own a distinct file area, route area, or verification slice.\n"
        "- If the task is about search, split by search target families.\n"
        "- If the task is about implementation, split by artifact or layout region.\n"
        "- Produce exactly the requested number of items when possible."
    )

    planner_runtimes = build_family_runtimes(list_provider_slots("gemini"), "gemini")
    if not planner_runtimes:
        planner_runtimes = build_family_runtimes(list_provider_slots("cerebras"), "cerebras")
    result: RuntimeResult | None = None
    for runtime in planner_runtimes:
        result = runtime.run_fast_prompt(
            prompt,
            model=pick_default_model(runtime.slot),
            timeout_seconds=60.0,
        )
        if result.ok:
            break
    if result is None or not result.ok:
        return fallback

    rows = _extract_json_array(result.text)
    if not rows:
        return fallback

    tasks: List[StageTask] = []
    seen: set[str] = set()
    for index, row in enumerate(rows[:desired_count], start=1):
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
                shard_count=desired_count,
                instruction=instruction,
                title=title,
                ownership=ownership,
            )
        )
    if not tasks:
        return fallback
    while len(tasks) < desired_count:
        next_index = len(tasks) + 1
        tasks.append(
            StageTask(
                role=role,
                shard_index=next_index,
                shard_count=desired_count,
                instruction=fallback[(next_index - 1) % len(fallback)].instruction,
                title=fallback[(next_index - 1) % len(fallback)].title,
                ownership=fallback[(next_index - 1) % len(fallback)].ownership,
            )
        )
    return tasks[:desired_count]


def _preferred_model_for_role(role: str, runtime: CodexWorkerRuntime, state: OrchestratorState) -> str:
    task_type = str(state.get("task_type") or "general")
    available = set(runtime.slot.models)
    if role in {"implementer", "verifier"} and "qwen-3-235b-a22b-instruct-2507" in available:
        return "qwen-3-235b-a22b-instruct-2507"
    if role in {"finder", "reader", "summarizer"} and task_type in {"repo_search", "general"}:
        if "llama3.1-8b" in available:
            return "llama3.1-8b"
    if role in {"finder", "reader", "summarizer"} and task_type == "ui_design":
        if "llama3.1-8b" in available:
            return "llama3.1-8b"
    return pick_default_model(runtime.slot)


def _token_limit_for_role(role: str, task_type: str) -> int:
    cerebras_slots = list_provider_slots("cerebras")
    if not cerebras_slots:
        return 32768
    models = set(cerebras_slots[0].models)
    if role in {"finder", "reader", "summarizer"} and "llama3.1-8b" in models:
        return 8192
    if role in {"implementer", "verifier"} and "qwen-3-235b-a22b-instruct-2507" in models:
        return 32768
    return cerebras_slots[0].token_limit


def _boost_search_workers(role: str, task_type: str, difficulty: int, workers: int) -> int:
    if role not in {"finder", "reader", "summarizer"}:
        return workers
    if task_type in {"repo_search", "ui_design"}:
        if difficulty >= 4:
            return 20
        if difficulty >= 2:
            return max(workers, 12)
    return workers


def _max_available_stage_workers(provider_family: str) -> int:
    return max(1, min(20, len(list_provider_slots(provider_family))))


def _maximize_stage_workers(role: str, workers: int) -> int:
    provider_family = "cerebras"
    if role == "supervisor":
        provider_family = "gemini"
    return max(workers, _max_available_stage_workers(provider_family))


def _planned_task_count(dispatch_workers: int) -> int:
    return max(dispatch_workers, min(240, dispatch_workers * 6))


def _dispatch_worker_count(role: str, task_type: str, difficulty: int, workers: int) -> int:
    if task_type == "general":
        return min(workers, 2 if difficulty <= 1 else 4)
    if role in {"finder", "reader", "summarizer"}:
        return workers
    if role == "implementer":
        return workers
    if role == "verifier":
        return workers
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
    dispatch_workers = _dispatch_worker_count(role, str(state.get("task_type") or "general"), int(state.get("difficulty") or 1), allocation.workers)
    planned_tasks = _planned_task_count(dispatch_workers)
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
            "- Return a short final summary that names the changed file paths."
        )
    elif role == "verifier":
        prompt_base += (
            "\n\nVerification requirements:\n"
            "- Check whether the requested files or edits now exist.\n"
            "- Report concrete file paths and any remaining gap."
        )
    else:
        prompt_base += "\n\nReturn concise findings only."
    results: List[RuntimeResult] = []
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
    with ThreadPoolExecutor(max_workers=max(1, dispatch_workers)) as executor:
        pending_tasks = list(stage_tasks)
        available_runtimes = list(runtimes)
        future_map = {}

        def schedule_one(runtime: CodexWorkerRuntime, stage_task: StageTask) -> None:
            shard_prompt = (
                prompt_base
                + f"\n\nShard assignment: {stage_task.shard_index}/{stage_task.shard_count}"
                + f"\nTask title: {stage_task.title}"
                + f"\nOwnership boundary: {stage_task.ownership}"
                + f"\nShard-specific objective:\n{stage_task.instruction}"
            )
            future = executor.submit(
                runtime.run_prompt,
                shard_prompt,
                cwd=cwd,
                model=_preferred_model_for_role(role, runtime, state),
            )
            future_map[future] = (runtime, stage_task)

        while pending_tasks and available_runtimes and len(future_map) < dispatch_workers:
            runtime = available_runtimes.pop(0)
            schedule_one(runtime, pending_tasks.pop(0))

        while future_map:
            done, _ = wait(set(future_map.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                runtime, stage_task = future_map.pop(future)
                result = future.result()
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
                results.append(result)
                if not result.budget_blocked and not result.rate_limited:
                    available_runtimes.append(runtime)

            while pending_tasks and available_runtimes and len(future_map) < dispatch_workers:
                runtime = available_runtimes.pop(0)
                schedule_one(runtime, pending_tasks.pop(0))
    return results


def _fast_execute(state: OrchestratorState) -> OrchestratorState:
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
    final = result.text if result and result.ok else (result.error if result else "fast-path execution failed")
    return {**state, "final_response": final}


def _classify(state: OrchestratorState) -> OrchestratorState:
    telemetry = TelemetryStore()
    task = state["task"]
    task_type = classify_task_type(task)
    difficulty = estimate_difficulty(task)
    estimated_tokens = estimate_tokens(task)
    gemini_slots = list_provider_slots("gemini")
    cerebras_slots = list_provider_slots("cerebras")
    allocations: Dict[str, StageAllocation] = {}
    for role in ROLE_NAMES:
        role_alloc = build_stage_allocations(
            task_type=task_type,
            difficulty=difficulty,
            estimated_tokens=estimated_tokens,
            token_limit=_token_limit_for_role(role, task_type),
            telemetry=telemetry,
        )[role]
        boosted_workers = _boost_search_workers(role, task_type, difficulty, role_alloc.workers)
        boosted_workers = _maximize_stage_workers(role, boosted_workers)
        allocations[role] = StageAllocation(
            role=role,
            workers=boosted_workers,
            estimated_tokens=role_alloc.estimated_tokens,
            token_limit=role_alloc.token_limit,
            overload_ratio=role_alloc.overload_ratio,
        )
    stage_tasks = {
        role: [task.instruction for task in _decompose_stage_tasks(state, role, _planned_task_count(allocations[role].workers))]
        for role in ROLE_NAMES
    }
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
    }


def _supervisor_plan(state: OrchestratorState) -> OrchestratorState:
    gemini_runtimes = build_family_runtimes(list_provider_slots("gemini"), "gemini")
    cerebras_runtimes = build_family_runtimes(list_provider_slots("cerebras"), "cerebras")
    prompt = (
        "You are the supervisor for a LangGraph Codex orchestration.\n"
        "Create a compact execution plan that decomposes the task into finder, reader, summarizer, implementer, and verifier stages.\n"
        "Respect that each stage may run 1..20 workers in parallel and duplicate key reuse is allowed.\n"
        f"Task type: {state['task_type']}\n"
        f"Difficulty: {state['difficulty']}/5\n"
        f"Estimated tokens: {state['estimated_tokens']}\n"
        f"Current allocations: {state['allocations']}\n\n"
        f"Current stage shards: {state.get('stage_tasks')}\n\n"
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
    plan = result.text if result and result.ok else "Supervisor planning failed; proceeding with direct worker execution."
    return {**state, "plan": plan}


def _stage_node(role: str, provider_family: str):
    def _run(state: OrchestratorState) -> OrchestratorState:
        telemetry = TelemetryStore()
        allocations = state["allocations"]
        allocation = StageAllocation(**allocations[role])
        runtimes = build_family_runtimes(list_provider_slots(provider_family), provider_family)
        results = _run_parallel_role(role, state, runtimes, allocation)
        quota_failures = sum(1 for result in results if result.rate_limited)
        total_duration = sum(result.duration_seconds for result in results)
        telemetry.record_stage(
            task_type=state["task_type"],
            role=role,
            duration_seconds=total_duration,
            success=all(result.ok for result in results) if results else False,
            token_estimate=state["estimated_tokens"],
            worker_count=allocation.workers,
            provider_family=provider_family,
            quota_failures=quota_failures,
            task_signature=str((state.get("stage_tasks") or {}).get(role, []))[:200],
        )
        key = "implementation" if role == "implementer" else ("verification" if role == "verifier" else "findings")
        current: List[str] = list(state.get(key, [])) if isinstance(state.get(key), list) else []
        additions = _summarize_results(results)
        if role == "implementer":
            return {**state, "implementation": "\n\n".join(additions)}
        if role == "verifier":
            return {**state, "verification": "\n\n".join(additions), "telemetry_summary": telemetry.summarize()}
        if role == "summarizer":
            return {**state, "summaries": additions}
        return {**state, key: current + additions}
    return _run


def _finalize(state: OrchestratorState) -> OrchestratorState:
    sections = [
        "Supervisor plan:\n" + str(state.get("plan") or "").strip(),
        "Finder/Reader results:\n" + "\n\n".join(state.get("findings") or []),
        "Summaries:\n" + "\n\n".join(state.get("summaries") or []),
        "Implementation:\n" + str(state.get("implementation") or "").strip(),
        "Verification:\n" + str(state.get("verification") or "").strip(),
        "Telemetry:\n" + str(state.get("telemetry_summary") or {}),
    ]
    return {**state, "final_response": "\n\n".join(section for section in sections if section.strip())}


def build_graph():
    if StateGraph is None:
        raise RuntimeError(
            "LangGraph is not installed. Install the orchestration extra: pip install -e '.[orchestration]'"
        )

    graph = StateGraph(OrchestratorState)
    graph.add_node("classify", _classify)
    graph.add_node("fast_execute", _fast_execute)
    graph.add_node("supervisor", _supervisor_plan)
    graph.add_node("finder", _stage_node("finder", "cerebras"))
    graph.add_node("reader", _stage_node("reader", "cerebras"))
    graph.add_node("summarizer", _stage_node("summarizer", "cerebras"))
    graph.add_node("implementer", _stage_node("implementer", "cerebras"))
    graph.add_node("verifier", _stage_node("verifier", "cerebras"))
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
