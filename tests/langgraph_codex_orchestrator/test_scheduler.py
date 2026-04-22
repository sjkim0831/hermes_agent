from langgraph_codex_orchestrator.scheduler import build_stage_allocations
from langgraph_codex_orchestrator.telemetry import TelemetryStore


def test_build_stage_allocations_respects_bounds(tmp_path):
    telemetry = TelemetryStore(tmp_path / "telemetry.json")
    allocations = build_stage_allocations(
        task_type="repo_search",
        difficulty=5,
        estimated_tokens=120000,
        token_limit=8192,
        telemetry=telemetry,
    )
    assert allocations["finder"].workers >= 1
    assert allocations["finder"].workers <= 20
    assert allocations["implementer"].workers >= 1
    assert allocations["implementer"].workers <= 20


def test_build_stage_allocations_finder_heavier_for_repo_search(tmp_path):
    telemetry = TelemetryStore(tmp_path / "telemetry.json")
    allocations = build_stage_allocations(
        task_type="repo_search",
        difficulty=3,
        estimated_tokens=20000,
        token_limit=32768,
        telemetry=telemetry,
    )
    assert allocations["finder"].workers >= allocations["verifier"].workers
