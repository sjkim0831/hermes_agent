"""CLI entrypoint for the LangGraph Codex orchestrator."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .graph import _classify, build_graph
from .quota import QuotaStore
from .telemetry import RunStateStore, TelemetryStore


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Hermes LangGraph Codex orchestrator.")
    parser.add_argument("task", nargs="*", help="Task prompt for the orchestrator.")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for worker execution.")
    parser.add_argument(
        "--mode",
        choices=("default", "reduced", "gated", "strict"),
        default="default",
        help="Execution mode; reduced lowers shard fan-out and worker pressure, gated pauses after each stage.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the final state as JSON instead of plain text.")
    parser.add_argument("--dry-run", action="store_true", help="Print dynamic allocation and capacity without running workers.")
    parser.add_argument(
        "--quota-reset",
        choices=("all", "gemini", "cerebras"),
        help="Reset recorded quota usage for the current quota window.",
    )
    parser.add_argument("--quota-show", action="store_true", help="Show current recorded quota usage and exit.")
    parser.add_argument("--resume", help="Resume a gated run from a checkpoint path.")
    parser.add_argument("--retry", action="store_true", help="Retry the current stage when resuming a gated run.")
    parser.add_argument(
        "--approve",
        choices=("continue", "retry", "stop"),
        help="Approve or reject the latest pending checkpoint without supplying a checkpoint path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    quota = QuotaStore()
    if args.quota_reset:
        families = None if args.quota_reset == "all" else [args.quota_reset]
        payload = quota.reset(families)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0
    if args.quota_show:
        sys.stdout.write(json.dumps(quota.summary(), ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0
    task = " ".join(args.task).strip()
    if not task and not args.approve:
        sys.stderr.write("error: task is required unless --quota-show or --quota-reset is used.\n")
        return 2
    if args.dry_run:
        payload = _classify({"task": task, "cwd": args.cwd, "execution_mode": args.mode})
        telemetry = TelemetryStore()
        payload["log_paths"] = {
            "telemetry": str(telemetry.path),
            "events": str(telemetry.event_log_path),
            "quota": str(quota.path),
        }
        payload["execution_mode"] = args.mode
        payload["quota_summary"] = quota.summary()
        if args.json:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")
            return 0
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0
    if args.mode in {"gated", "strict"}:
        from .graph import _run_gated_pipeline, _run_strict_pipeline
        payload = {"cwd": args.cwd, "execution_mode": args.mode}
        if task:
            payload["task"] = task
        resume_path = args.resume
        retry = args.retry
        if args.approve:
            monitor = RunStateStore()
            pending = monitor.pending_approval() or {}
            if args.approve == "stop":
                monitor.clear_pending_approval()
                sys.stdout.write("approval stopped\n")
                return 0
            resume_path = resume_path or str(pending.get("checkpoint_path") or "").strip() or None
            if not resume_path:
                sys.stderr.write("error: no pending checkpoint found; run /orchestrate4 start <task> first.\n")
                return 2
            retry = args.approve == "retry"
        if args.mode == "strict":
            state = _run_strict_pipeline(payload, resume_path=resume_path, retry=retry)
        else:
            state = _run_gated_pipeline(payload, resume_path=resume_path, retry=retry)
    else:
        graph = build_graph()
        state = graph.invoke({"task": task, "cwd": args.cwd, "execution_mode": args.mode})
    if args.json:
        sys.stdout.write(json.dumps(state, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0
    sys.stdout.write(str(state.get("final_response") or "").strip())
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
