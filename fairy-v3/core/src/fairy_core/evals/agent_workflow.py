from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


@dataclass(frozen=True, slots=True)
class EvalScenario:
    name: str
    title: str
    tests: tuple[str, ...]
    evidence_required: bool = False
    recovery_required: bool = False
    safety_required: bool = False
    intent_required: bool = False
    clarification_required: bool = False
    literal_isolation_required: bool = False
    cross_mode_required: bool = False


SCENARIOS = (
    EvalScenario(
        "request_interpretation",
        "Request interpretation and literal input isolation",
        (
            "tests/assistant/test_interpretation.py::"
            "test_classifier_envelope_preserves_prompt_like_text_as_json_data",
            "tests/assistant/test_interpretation.py::"
            "test_literal_segments_cannot_trigger_lexical_specialized_routes",
            "tests/assistant/test_interpretation.py::"
            "test_high_impact_missing_target_forces_clarification",
        ),
        safety_required=True,
        intent_required=True,
        clarification_required=True,
        literal_isolation_required=True,
    ),
    EvalScenario(
        "classifier_mode_consistency",
        "Auto and Manual interpretation contract consistency",
        (
            "tests/assistant/test_interpretation.py::"
            "test_auto_and_manual_classifiers_share_the_interpretation_schema",
        ),
        intent_required=True,
        cross_mode_required=True,
    ),
    EvalScenario(
        "clarification_recovery",
        "Clarification waits and resumes one durable Turn",
        (
            "tests/assistant/test_workflow_controls.py::"
            "test_clarification_waits_and_resumes_the_same_turn_idempotently",
            "tests/assistant/test_workflow_controls.py::"
            "test_clarification_wait_survives_core_restart",
        ),
        recovery_required=True,
        safety_required=True,
        intent_required=True,
        clarification_required=True,
    ),
    EvalScenario(
        "evidence_first",
        "Evidence-first completion",
        (
            "tests/assistant/test_evidence_completion.py::"
            "test_completion_requires_same_turn_unexpired_receipts_for_every_requirement",
        ),
        evidence_required=True,
        safety_required=True,
    ),
    EvalScenario(
        "parallel_research",
        "Safe parallel research and deterministic fan-in",
        (
            "tests/assistant/test_tool_dispatch.py::"
            "test_independent_read_tools_execute_in_parallel_and_join_in_call_order",
        ),
        evidence_required=True,
    ),
    EvalScenario(
        "approval_write",
        "Approval-gated write resumes exactly once",
        (
            "tests/assistant/test_approval_resume.py::"
            "test_standard_profile_approval_resumes_one_tool_effect_once",
        ),
        safety_required=True,
    ),
    EvalScenario(
        "failure_recovery",
        "Expired claim recovery",
        (
            "tests/assistant/test_durable_turn_worker.py::"
            "test_started_turn_recovers_from_an_expired_durable_work_claim",
        ),
        recovery_required=True,
        safety_required=True,
    ),
    EvalScenario(
        "steering",
        "Mid-run steering and revision replay",
        (
            "tests/assistant/test_workflow_controls.py::"
            "test_steering_revises_one_turn_and_replays_idempotently",
        ),
        recovery_required=True,
    ),
    EvalScenario(
        "browser_research",
        "Governed Browser research",
        (
            "tests/browser/test_browser_service.py::"
            "test_agent_action_is_fenced_by_the_current_page_revision",
            "tests/browser/test_browser_service.py::"
            "test_agent_can_manage_scoped_tabs_without_a_second_scheduler",
            "tests/browser/test_browser_service.py::"
            "test_governed_download_is_task_scoped_hashed_and_recorded",
            "tests/browser/test_browser_service.py::"
            "test_browser_rejects_credential_urls_before_worker_call",
        ),
        evidence_required=True,
        safety_required=True,
    ),
    EvalScenario(
        "long_task_restart",
        "Graceful close and long-task restart",
        (
            "tests/assistant/test_durable_turn_worker.py::"
            "test_active_turn_survives_graceful_core_close",
        ),
        recovery_required=True,
    ),
    EvalScenario(
        "scheduled_turn_boundary",
        "Scheduled instruction creates one governed turn",
        (
            "tests/assistant/test_schedule_service.py::"
            "test_run_now_creates_one_normal_message_turn_and_workflow",
            "tests/assistant/test_schedule_service.py::"
            "test_pending_schedule_waits_for_the_active_turn_in_the_same_chat",
            "tests/assistant/test_schedule_service.py::"
            "test_changed_permission_snapshot_pauses_before_creating_a_message",
        ),
        evidence_required=True,
        safety_required=True,
    ),
    EvalScenario(
        "schedule_trigger_recovery",
        "Local schedule trigger recovery and fencing",
        (
            "tests/assistant/test_schedule_trigger.py::"
            "test_one_shot_trigger_is_idempotent_across_restart_style_sweeps",
            "tests/assistant/test_schedule_trigger.py::"
            "test_recurring_catch_up_and_overlap_keep_only_latest_pending_occurrence",
            "tests/assistant/test_schedule_trigger.py::"
            "test_claim_fence_rejects_a_stale_worker_after_lease_expiry",
            "tests/assistant/test_schedule_trigger.py::"
            "test_three_consecutive_occurrence_failures_pause_a_recurring_schedule",
        ),
        recovery_required=True,
        safety_required=True,
    ),
    EvalScenario(
        "schedule_time_semantics",
        "DST and offline compensation semantics",
        (
            "tests/assistant/test_schedule_recurrence.py::"
            "test_daily_schedule_skips_to_first_valid_time_during_spring_dst",
            "tests/assistant/test_schedule_recurrence.py::"
            "test_ambiguous_fall_dst_time_runs_only_the_first_occurrence",
            "tests/assistant/test_schedule_recurrence.py::"
            "test_catch_up_keeps_only_the_latest_due_instant_and_missed_count",
        ),
        recovery_required=True,
    ),
)


def parse_junit_cases(path: Path) -> dict[str, dict[str, Any]]:
    root = ElementTree.parse(path).getroot()
    cases: dict[str, dict[str, Any]] = {}
    for element in root.iter("testcase"):
        name = str(element.attrib.get("name", ""))
        status = "passed"
        detail: str | None = None
        for child_status in ("failure", "error", "skipped"):
            child = element.find(child_status)
            if child is not None:
                status = "failed" if child_status in {"failure", "error"} else "skipped"
                detail = str(child.attrib.get("message") or child.text or child_status)[:500]
                break
        cases[name] = {
            "status": status,
            "duration_seconds": round(float(element.attrib.get("time", "0")), 6),
            "detail": detail,
        }
    return cases


def build_report(
    cases: Mapping[str, Mapping[str, Any]],
    *,
    pytest_exit_code: int,
    live_provider: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    scenario_results = []
    for scenario in SCENARIOS:
        test_results = []
        for node_id in scenario.tests:
            test_name = node_id.rsplit("::", 1)[-1]
            result = dict(cases.get(test_name, {"status": "missing", "duration_seconds": 0.0}))
            result["node_id"] = node_id
            test_results.append(result)
        statuses = {str(item["status"]) for item in test_results}
        status = "passed" if statuses == {"passed"} else "failed"
        scenario_results.append(
            {
                "name": scenario.name,
                "title": scenario.title,
                "status": status,
                "duration_seconds": round(
                    sum(float(item["duration_seconds"]) for item in test_results), 6
                ),
                "tests": test_results,
                "evidence_required": scenario.evidence_required,
                "recovery_required": scenario.recovery_required,
                "safety_required": scenario.safety_required,
                "intent_required": scenario.intent_required,
                "clarification_required": scenario.clarification_required,
                "literal_isolation_required": scenario.literal_isolation_required,
                "cross_mode_required": scenario.cross_mode_required,
            }
        )
    passed = [item for item in scenario_results if item["status"] == "passed"]
    evidence = [item for item in scenario_results if item["evidence_required"]]
    recovery = [item for item in scenario_results if item["recovery_required"]]
    safety = [item for item in scenario_results if item["safety_required"]]
    intent = [item for item in scenario_results if item["intent_required"]]
    clarification = [item for item in scenario_results if item["clarification_required"]]
    literal_isolation = [
        item for item in scenario_results if item["literal_isolation_required"]
    ]
    cross_mode = [item for item in scenario_results if item["cross_mode_required"]]
    deterministic_status = (
        "passed" if pytest_exit_code == 0 and len(passed) == len(scenario_results) else "failed"
    )
    normalized_live = _normalize_live_provider(live_provider)
    return {
        "schema_version": 2,
        "report_kind": "fairy.agent_workflow_eval",
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "overall_status": deterministic_status,
        "deterministic": {
            "status": deterministic_status,
            "pytest_exit_code": pytest_exit_code,
            "scenarios": scenario_results,
            "metrics": {
                "success_rate": _rate(passed, scenario_results),
                "evidence_coverage_rate": _passed_rate(evidence),
                "recovery_success_rate": _passed_rate(recovery),
                "safety_scenario_pass_rate": _passed_rate(safety),
                "intent_match_gate_rate": _passed_rate(intent),
                "clarification_gate_rate": _passed_rate(clarification),
                "quoted_content_isolation_rate": _passed_rate(literal_isolation),
                "auto_manual_consistency_rate": _passed_rate(cross_mode),
                "high_impact_unintended_execution_count": (
                    0 if clarification and _passed_rate(clarification) == 1.0 else None
                ),
                "parallel_median_improvement_gate": "at_least_25_percent",
            },
        },
        "live_provider": normalized_live,
        "verification_boundaries": {
            "scripted_provider_proves_model_tool_selection": False,
            "sqlite_proves_postgresql_locking_or_rls": False,
            "fixture_browser_proves_native_edge_recovery": False,
            "scripted_clock_proves_os_clock_service_behavior": False,
            "renderer_fixture_proves_windows_toast_activation": False,
        },
    }


def write_report(report: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    deterministic = report["deterministic"]
    live = report["live_provider"]
    lines = [
        "# Fairy Agent Workflow Evaluation",
        "",
        f"- Deterministic gate: **{deterministic['status']}**",
        f"- Live Provider: **{live['status']}**",
        f"- Generated: `{report['generated_at']}`",
        "",
        "## Deterministic scenarios",
        "",
        "| Scenario | Status | Duration (s) |",
        "|---|---:|---:|",
    ]
    for scenario in deterministic["scenarios"]:
        lines.append(
            f"| {scenario['title']} | {scenario['status']} | {scenario['duration_seconds']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Verification boundary",
            "",
            "Scripted Provider results validate scheduling and hard gates only. "
            "Real model tool selection, PostgreSQL/S3, WSL Sandbox, and native WebView2 "
            "remain unverified unless separately recorded. Scripted clocks and renderer fixtures "
            "do not prove OS clock-service behavior or Windows Toast activation.",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path


def load_live_provider(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("live Provider report must be a JSON object")
    return payload


def _normalize_live_provider(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "unverified",
            "reason": "No real Provider result file was supplied.",
            "metrics": None,
        }
    status = str(payload.get("status", "failed"))
    if status not in {"verified", "failed", "unverified"}:
        raise ValueError("live Provider status must be verified, failed, or unverified")
    metrics = payload.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("live Provider metrics must be an object")
    normalized_metrics = {}
    for name in (
        "success_rate",
        "tool_accuracy",
        "evidence_coverage",
        "recovery_success",
        "intent_match",
        "clarification_precision",
        "clarification_recall",
        "over_clarification_rate",
        "auto_manual_consistency",
        "quoted_content_isolation",
    ):
        value = metrics.get(name)
        if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError(f"live Provider metric {name} must be between 0 and 1")
        normalized_metrics[name] = float(value)
    return {"status": status, "metrics": normalized_metrics}


def _rate(numerator: Sequence[Any], denominator: Sequence[Any]) -> float:
    return round(len(numerator) / len(denominator), 4) if denominator else 0.0


def _passed_rate(items: Sequence[Mapping[str, Any]]) -> float:
    return _rate([item for item in items if item["status"] == "passed"], items)


__all__ = [
    "SCENARIOS",
    "EvalScenario",
    "build_report",
    "load_live_provider",
    "parse_junit_cases",
    "write_report",
]
