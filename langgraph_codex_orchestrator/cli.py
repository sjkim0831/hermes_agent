"""CLI entrypoint for the LangGraph Codex orchestrator."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .graph import _classify, build_graph


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
        payload = _classify({"task": task, "cwd": args.cwd})
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
