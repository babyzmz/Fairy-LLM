from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fairy_core.evals.agent_workflow import SCENARIOS, build_report, parse_junit_cases, write_report


def _junit(path: Path, *, failing: str | None = None) -> None:
    cases = []
    for scenario in SCENARIOS:
        for node_id in scenario.tests:
            name = node_id.rsplit("::", 1)[-1]
            failure = '<failure message="boom" />' if name == failing else ""
            cases.append(f'<testcase name="{name}" time="0.25">{failure}</testcase>')
    path.write_text(f"<testsuite>{''.join(cases)}</testsuite>", encoding="utf-8")


def test_eval_report_is_machine_readable_and_marks_live_provider_unverified(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "junit.xml"
    _junit(junit)

    report = build_report(
        parse_junit_cases(junit),
        pytest_exit_code=0,
        generated_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    json_path, markdown_path = write_report(report, tmp_path / "report")

    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    assert persisted["overall_status"] == "passed"
    assert persisted["deterministic"]["metrics"]["success_rate"] == 1.0
    assert persisted["deterministic"]["metrics"]["intent_match_gate_rate"] == 1.0
    assert persisted["deterministic"]["metrics"]["clarification_gate_rate"] == 1.0
    assert persisted["deterministic"]["metrics"]["quoted_content_isolation_rate"] == 1.0
    assert persisted["deterministic"]["metrics"]["auto_manual_consistency_rate"] == 1.0
    assert persisted["deterministic"]["metrics"][
        "high_impact_unintended_execution_count"
    ] == 0
    assert persisted["live_provider"]["status"] == "unverified"
    assert {
        "scheduled_turn_boundary",
        "schedule_trigger_recovery",
        "schedule_time_semantics",
    }.issubset(
        {scenario["name"] for scenario in persisted["deterministic"]["scenarios"]}
    )
    assert not persisted["verification_boundaries"][
        "renderer_fixture_proves_windows_toast_activation"
    ]
    assert "Real model tool selection" in markdown_path.read_text(encoding="utf-8")


def test_eval_report_fails_when_a_required_case_fails(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    failing = SCENARIOS[0].tests[0].rsplit("::", 1)[-1]
    _junit(junit, failing=failing)

    report = build_report(parse_junit_cases(junit), pytest_exit_code=1)

    assert report["overall_status"] == "failed"
    assert report["deterministic"]["scenarios"][0]["status"] == "failed"


def test_live_provider_metrics_are_bounded() -> None:
    cases = {
        node_id.rsplit("::", 1)[-1]: {"status": "passed", "duration_seconds": 0.1}
        for scenario in SCENARIOS
        for node_id in scenario.tests
    }

    with pytest.raises(ValueError, match="tool_accuracy"):
        build_report(
            cases,
            pytest_exit_code=0,
            live_provider={
                "status": "verified",
                "metrics": {
                    "success_rate": 1,
                    "tool_accuracy": 1.1,
                    "evidence_coverage": 1,
                    "recovery_success": 1,
                },
            },
        )
