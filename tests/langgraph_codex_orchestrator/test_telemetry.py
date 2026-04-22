from langgraph_codex_orchestrator.telemetry import (
    TelemetryStore,
    classify_task_type,
    estimate_difficulty,
    estimate_tokens,
)


def test_task_heuristics_classify_ui_design():
    assert classify_task_type("Find the login page and make an HTML design mockup") == "ui_design"
    assert estimate_difficulty("parallel langgraph repo search across many files") >= 3
    assert estimate_tokens("abcd" * 100) > 0


def test_telemetry_record_and_stats(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.json")
    store.record_stage(
        task_type="repo_search",
        role="finder",
        duration_seconds=75.0,
        success=True,
        token_estimate=1000,
        worker_count=8,
        provider_family="cerebras",
    )
    stats = store.stage_stats("repo_search", "finder")
    assert stats["runs"] == 1
    assert stats["avg_duration_seconds"] == 75.0
    assert stats["last_worker_count"] == 8
