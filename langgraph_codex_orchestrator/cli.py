"""CLI entrypoint for the LangGraph Codex orchestrator."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .config import list_provider_slots, summarize_capacity
from .graph import build_graph
from .scheduler import build_stage_allocations
from .telemetry import TelemetryStore, classify_task_type, estimate_difficulty, estimate_tokens


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Hermes LangGraph Codex orchestrator.")
    parser.add_argument("task", nargs="+", help="Task prompt for the orchestrator.")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for worker execution.")
    parser.add_argument("--json", action="store_true", help="Emit the final state as JSON instead of plain text.")
    parser.add_argument("--dry-run", action="store_true", help="Print dynamic allocation and capacity without running workers.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    task = " ".join(args.task).strip()
    if args.dry_run:
        telemetry = TelemetryStore()
        task_type = classify_task_type(task)
        difficulty = estimate_difficulty(task)
        estimated_tokens = estimate_tokens(task)
        gemini_slots = list_provider_slots("gemini")
        cerebras_slots = list_provider_slots("cerebras")
        primary_limit = gemini_slots[0].token_limit if gemini_slots else (cerebras_slots[0].token_limit if cerebras_slots else 32768)
        allocations = build_stage_allocations(
            task_type=task_type,
            difficulty=difficulty,
            estimated_tokens=estimated_tokens,
            token_limit=primary_limit,
            telemetry=telemetry,
        )
        payload = {
            "task_type": task_type,
            "difficulty": difficulty,
            "estimated_tokens": estimated_tokens,
            "allocations": {role: allocation.__dict__ for role, allocation in allocations.items()},
            "capacity": {
                "gemini": summarize_capacity(gemini_slots),
                "cerebras": summarize_capacity(cerebras_slots),
            },
        }
        if args.json:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")
            return 0
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0
    graph = build_graph()
    state = graph.invoke({"task": task, "cwd": args.cwd})
    if args.json:
        sys.stdout.write(json.dumps(state, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0
    sys.stdout.write(str(state.get("final_response") or "").strip())
    sys.stdout.write("\n")
    return 0
