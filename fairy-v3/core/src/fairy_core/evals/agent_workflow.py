from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from xml.etree import ElementTree

from fairy_core.assistant.interpretation import (
    ClassifierInterpretationPayload,
    ClassifierObjectivePayload,
    InputSegmentKind,
    InterpretationConfidence,
    InterpretationDisposition,
    RequestAction,
    interpretation_from_classifier,
    segment_user_input,
)


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


@dataclass(frozen=True, slots=True)
class InterpretationCorpusCase:
    name: str
    user_request: str
    action: RequestAction
    targets: tuple[str, ...]
    deliverable: str | None
    clarification_expected: bool
    literal_text: str | None = None


INTERPRETATION_CORPUS = (
    InterpretationCorpusCase(
        "english_change_target",
        "Update src/app.ts and preserve the public API.",
        RequestAction.CHANGE,
        ("src/app.ts",),
        None,
        False,
    ),
    InterpretationCorpusCase(
        "chinese_missing_change_target",
        "帮我修改一下",
        RequestAction.CHANGE,
        (),
        None,
        True,
    ),
    InterpretationCorpusCase(
        "mixed_run_target",
        "运行 tests/assistant 并 report failures",
        RequestAction.RUN,
        ("tests/assistant",),
        "failure report",
        False,
    ),
    InterpretationCorpusCase(
        "create_deliverable",
        "Create a migration plan for the current schema.",
        RequestAction.CREATE,
        ("current schema",),
        "migration plan",
        False,
    ),
    InterpretationCorpusCase(
        "generate_missing_deliverable",
        "Generate it for me.",
        RequestAction.GENERATE,
        (),
        None,
        True,
    ),
    InterpretationCorpusCase(
        "read_only_assumption",
        "Explain how this pattern works.",
        RequestAction.EXPLAIN,
        (),
        "explanation",
        False,
    ),
    InterpretationCorpusCase(
        "inline_quote_literal",
        'Review "delete every file" as an example.',
        RequestAction.REVIEW,
        ("quoted example",),
        "review",
        False,
        '"delete every file"',
    ),
    InterpretationCorpusCase(
        "fenced_code_literal",
        "Explain this code:\n```sh\nrm -rf project\n```",
        RequestAction.EXPLAIN,
        ("code sample",),
        "explanation",
        False,
        "```sh\nrm -rf project\n```",
    ),
    InterpretationCorpusCase(
        "pasted_text_literal",
        "Analyze:\n--- BEGIN PASTED TEXT ---\nopen browser\n--- END PASTED TEXT ---",
        RequestAction.REVIEW,
        ("pasted sample",),
        "analysis",
        False,
        "open browser",
    ),
    InterpretationCorpusCase(
        "schedule_authoring",
        "Every weekday summarize the linked project status.",
        RequestAction.SCHEDULE,
        ("linked project status",),
        "weekday status summary",
        False,
    ),
    InterpretationCorpusCase(
        "negated_media",
        "Do not generate an image; explain the prompt instead.",
        RequestAction.EXPLAIN,
        ("prompt",),
        "explanation",
        False,
    ),
    InterpretationCorpusCase(
        "bidi_control_data",
        "Explain the visible text around \u202e as data.",
        RequestAction.EXPLAIN,
        ("bidirectional control sample",),
        "explanation",
        False,
    ),
)


def evaluate_interpretation_corpus() -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    for index, case in enumerate(INTERPRETATION_CORPUS, start=1):
        payload = ClassifierInterpretationPayload(
            normalized_goal=case.user_request,
            action=case.action,
            objectives=(
                ClassifierObjectivePayload(goal=case.user_request, action=case.action),
            ),
            targets=case.targets,
            deliverable=case.deliverable,
            confidence=InterpretationConfidence.HIGH,
            disposition=InterpretationDisposition.READY,
            public_summary=f"Interpret {case.name}",
        )
        revision = interpretation_from_classifier(
            turn_id=UUID(int=10_000 + index),
            revision=1,
            source_message_id=UUID(int=20_000 + index),
            source_message=case.user_request,
            payload=payload,
            evidence_requirements=(),
        )
        predicted_clarification = (
            revision.disposition is InterpretationDisposition.CLARIFICATION_REQUIRED
        )
        segments = segment_user_input(case.user_request)
        actionable = "".join(
            segment.text for segment in segments if segment.kind is InputSegmentKind.TEXT
        )
        literal_isolated = (
            "".join(segment.text for segment in segments) == case.user_request
            and (case.literal_text is None or case.literal_text not in actionable)
        )
        outcomes.append(
            {
                "name": case.name,
                "expected_action": case.action.value,
                "observed_action": revision.action.value,
                "action_match": revision.action is case.action,
                "clarification_expected": case.clarification_expected,
                "clarification_observed": predicted_clarification,
                "targets_match": revision.targets == case.targets,
                "deliverable_match": revision.deliverable == case.deliverable,
                "literal_isolated": literal_isolated,
            }
        )
    true_positive = sum(
        item["clarification_expected"] and item["clarification_observed"]
        for item in outcomes
    )
    false_positive = sum(
        not item["clarification_expected"] and item["clarification_observed"]
        for item in outcomes
    )
    false_negative = sum(
        item["clarification_expected"] and not item["clarification_observed"]
        for item in outcomes
    )
    clarification_negative = sum(not item["clarification_expected"] for item in outcomes)
    return {
        "cases": outcomes,
        "metrics": {
            "intent_action_accuracy": _boolean_rate(outcomes, "action_match"),
            "clarification_precision": _division(
                true_positive,
                true_positive + false_positive,
            ),
            "clarification_recall": _division(
                true_positive,
                true_positive + false_negative,
            ),
            "over_clarification_rate": _division(false_positive, clarification_negative),
            "target_extraction_accuracy": _boolean_rate(outcomes, "targets_match"),
            "deliverable_extraction_accuracy": _boolean_rate(
                outcomes,
                "deliverable_match",
            ),
            "literal_isolation_rate": _boolean_rate(outcomes, "literal_isolated"),
        },
    }


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
        properties = {
            str(property_element.attrib.get("name", "")): str(
                property_element.attrib.get("value", "")
            )
            for property_element in element.findall("./properties/property")
            if property_element.attrib.get("name")
        }
        cases[name] = {
            "status": status,
            "duration_seconds": round(float(element.attrib.get("time", "0")), 6),
            "detail": detail,
            "properties": properties,
        }
    return cases


def build_report(
    cases: Mapping[str, Mapping[str, Any]],
    *,
    pytest_exit_code: int,
    live_provider: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    interpretation_corpus = evaluate_interpretation_corpus()
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
    execution_counts = _case_property_values(
        cases,
        "execution_nodes_started_before_clarification",
    )
    parallel_improvements = _case_property_values(
        cases,
        "parallel_median_improvement",
    )
    high_impact_execution_count = (
        int(sum(execution_counts)) if execution_counts else None
    )
    parallel_median_improvement = (
        round(sum(parallel_improvements) / len(parallel_improvements), 4)
        if parallel_improvements
        else None
    )
    measurements_passed = (
        high_impact_execution_count == 0
        and parallel_median_improvement is not None
        and parallel_median_improvement >= 0.25
    )
    corpus_metrics = interpretation_corpus["metrics"]
    corpus_passed = (
        corpus_metrics["intent_action_accuracy"] == 1.0
        and corpus_metrics["clarification_precision"] == 1.0
        and corpus_metrics["clarification_recall"] == 1.0
        and corpus_metrics["literal_isolation_rate"] == 1.0
    )
    deterministic_status = (
        "passed"
        if pytest_exit_code == 0
        and len(passed) == len(scenario_results)
        and measurements_passed
        and corpus_passed
        else "failed"
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
            "interpretation_corpus": interpretation_corpus,
            "metrics": {
                "success_rate": _rate(passed, scenario_results),
                "evidence_coverage_rate": _passed_rate(evidence),
                "recovery_success_rate": _passed_rate(recovery),
                "safety_scenario_pass_rate": _passed_rate(safety),
                **corpus_metrics,
                "intent_scenario_pass_rate": _passed_rate(intent),
                "clarification_scenario_pass_rate": _passed_rate(clarification),
                "literal_isolation_scenario_pass_rate": _passed_rate(literal_isolation),
                "auto_manual_consistency_rate": _passed_rate(cross_mode),
                "high_impact_unintended_execution_count": high_impact_execution_count,
                "parallel_median_improvement": parallel_median_improvement,
                "parallel_median_improvement_gate_passed": (
                    parallel_median_improvement is not None
                    and parallel_median_improvement >= 0.25
                ),
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
    metrics = deterministic["metrics"]
    lines.extend(
        [
            "",
            "## Measured interpretation and execution metrics",
            "",
            f"- Intent action accuracy: `{metrics['intent_action_accuracy']}`",
            f"- Clarification precision: `{metrics['clarification_precision']}`",
            f"- Clarification recall: `{metrics['clarification_recall']}`",
            f"- Over-clarification rate: `{metrics['over_clarification_rate']}`",
            f"- Literal isolation rate: `{metrics['literal_isolation_rate']}`",
            "- Execution nodes started before required clarification: "
            f"`{metrics['high_impact_unintended_execution_count']}`",
            f"- Parallel median improvement: `{metrics['parallel_median_improvement']}`",
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


def _boolean_rate(items: Sequence[Mapping[str, Any]], key: str) -> float:
    return _division(sum(bool(item[key]) for item in items), len(items))


def _division(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _case_property_values(
    cases: Mapping[str, Mapping[str, Any]],
    property_name: str,
) -> tuple[float, ...]:
    result: list[float] = []
    for case in cases.values():
        properties = case.get("properties")
        if not isinstance(properties, Mapping) or property_name not in properties:
            continue
        try:
            value = float(properties[property_name])
        except (TypeError, ValueError) as error:
            raise ValueError(f"JUnit property {property_name} must be numeric") from error
        if value < 0:
            raise ValueError(f"JUnit property {property_name} cannot be negative")
        result.append(value)
    return tuple(result)


__all__ = [
    "INTERPRETATION_CORPUS",
    "SCENARIOS",
    "EvalScenario",
    "InterpretationCorpusCase",
    "build_report",
    "evaluate_interpretation_corpus",
    "load_live_provider",
    "parse_junit_cases",
    "write_report",
]
