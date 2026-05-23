from __future__ import annotations

import copy
import re
from typing import Any, Callable

from app.core.perception import EntityExtractor
from app.core.perception.perception_models import DetectedEntity

from .capability_arbitrator import CapabilityArbitrator
from .capability_candidates import CapabilityCandidateGenerator
from .capability_contracts import CapabilityContractRegistry
from .clarification_policy import ClarificationPolicy
from .contract_validator import ContractValidator
from .resolution_models import CapabilityIntent, QueryResolutionResult, ResolutionTraceEvent
from .rules import RulePostValidator, RulePreClassifier
from .semantic_slot_validator import SemanticSlotValidator
from .slot_filler import SlotFiller
from .slot_normalizer import SlotNormalizer
from .slot_validator import SlotValidator


class QueryResolver:
    def __init__(
        self,
        *,
        contract_registry: CapabilityContractRegistry | None = None,
        slot_filler: SlotFiller | None = None,
        slot_normalizer: SlotNormalizer | None = None,
        slot_validator: SlotValidator | None = None,
        candidate_generator: CapabilityCandidateGenerator | None = None,
        semantic_slot_validator: SemanticSlotValidator | None = None,
        capability_arbitrator: CapabilityArbitrator | None = None,
        clarification_policy: ClarificationPolicy | None = None,
        contract_validator: ContractValidator | None = None,
        semantic_arbitration_callback: Callable[..., Any] | None = None,
        web_browse_decision_callback: Callable[..., Any] | None = None,
    ) -> None:
        self.contract_registry = contract_registry or CapabilityContractRegistry()
        self.slot_filler = slot_filler or SlotFiller()
        self.slot_normalizer = slot_normalizer or SlotNormalizer()
        self.slot_validator = slot_validator or SlotValidator()
        self.candidate_generator = candidate_generator or CapabilityCandidateGenerator()
        self.semantic_slot_validator = semantic_slot_validator or SemanticSlotValidator()
        self.capability_arbitrator = capability_arbitrator or CapabilityArbitrator()
        self.segment_entity_extractor = EntityExtractor()
        self.clarification_policy = clarification_policy or ClarificationPolicy()
        self.contract_validator = contract_validator or ContractValidator()
        self.rule_pre_classifier = RulePreClassifier()
        self.rule_post_validator = RulePostValidator()
        self.semantic_arbitration_callback = semantic_arbitration_callback
        self.web_browse_decision_callback = web_browse_decision_callback

    def resolve(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        intent_hint: str,
        entities: list[DetectedEntity],
        session_context: Any,
        previous_structured: dict[str, Any] | None = None,
        followup_target: str = "",
        followup_focus_value: str = "",
    ) -> QueryResolutionResult:
        followup_focus_value = self._sanitize_followup_focus_value(
            normalized_text=normalized_text,
            followup_target=followup_target,
            followup_focus_value=followup_focus_value,
        )
        active_plan_state = dict(getattr(session_context, "active_execution_plan_state", {}) or {})
        segments = self._split_multi_intent_segments(normalized_text)
        if len(segments) <= 1:
            single = self._resolve_single(
                raw_text=raw_text,
                normalized_text=normalized_text,
                intent_hint=intent_hint,
                entities=entities,
                session_context=session_context,
                previous_structured=previous_structured,
                followup_target=followup_target,
                followup_focus_value=followup_focus_value,
            )
            if self._should_continue_active_plan(normalized_text, session_context, active_plan_state):
                single.plan_continuation = True
                single.continuation_plan_id = str(active_plan_state.get("plan_id") or "")
                single.route_hints["plan_continuation"] = "true"
                single.route_hints["continuation_plan_id"] = single.continuation_plan_id
                single.trace.append(
                    ResolutionTraceEvent(
                        stage="plan_continuation_detected",
                        text="Attached follow-up query to active execution plan.",
                        data={
                            "plan_id": single.continuation_plan_id,
                            "last_step_capability": str(getattr(session_context, "last_step_capability", "") or ""),
                        },
                    )
                )
            return single

        trace = [
            ResolutionTraceEvent(
                stage="resolution_start",
                text=f"Intent guess: {intent_hint or 'generic_search'}",
                data={"normalized_text": normalized_text},
            ),
            ResolutionTraceEvent(
                stage="orchestration_plan_built",
                text=f"Detected sequential multi-intent query with {len(segments)} segment(s).",
                data={"segments": list(segments)},
            ),
        ]
        shadow_context = copy.copy(session_context)
        intents: list[CapabilityIntent] = []
        aggregate_trace: list[ResolutionTraceEvent] = []
        local_previous_structured = dict(previous_structured or {})

        for segment in segments[:3]:
            previous_intent = intents[-1] if intents else None
            segment_entities = self.segment_entity_extractor.extract(segment)
            segment_followup_target, segment_focus = self._infer_segment_followup(segment, previous_intent=previous_intent)
            segment_result = self._resolve_single(
                raw_text=segment,
                normalized_text=segment,
                intent_hint=self._infer_segment_intent_hint(segment, previous_intent),
                entities=segment_entities,
                session_context=shadow_context,
                previous_structured=local_previous_structured,
                followup_target=segment_followup_target,
                followup_focus_value=segment_focus,
            )
            capability_intent = CapabilityIntent(
                capability=segment_result.capability,
                slots=dict(segment_result.validated_slots or segment_result.resolved_slots),
                confidence=1.0 if not segment_result.clarification_needed else 0.66,
                normalized_query=segment_result.normalized_query,
                requires_clarification=segment_result.clarification_needed,
                missing_slots=list(segment_result.missing_slots),
            )
            intents.append(capability_intent)
            aggregate_trace.extend(segment_result.trace)
            self._apply_shadow_context(shadow_context, capability_intent)
            local_previous_structured = self._shadow_structured_from_intent(capability_intent)

        if not intents:
            return self._resolve_single(
                raw_text=raw_text,
                normalized_text=normalized_text,
                intent_hint=intent_hint,
                entities=entities,
                session_context=session_context,
                previous_structured=previous_structured,
                followup_target=followup_target,
                followup_focus_value=followup_focus_value,
            )

        base = self._resolve_single(
            raw_text=segments[0],
            normalized_text=segments[0],
            intent_hint=self._infer_segment_intent_hint(segments[0], None),
            entities=self.segment_entity_extractor.extract(segments[0]),
            session_context=session_context,
            previous_structured=previous_structured,
            followup_target=followup_target,
            followup_focus_value=followup_focus_value,
        )
        base.primary_intent = intents[0]
        base.secondary_intents = intents[1:]
        base.execution_mode = "sequential" if base.secondary_intents and not any(item.requires_clarification for item in intents) else "single"
        base.trace = trace + aggregate_trace
        return base

    def _resolve_single(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        intent_hint: str,
        entities: list[DetectedEntity],
        session_context: Any,
        previous_structured: dict[str, Any] | None = None,
        followup_target: str = "",
        followup_focus_value: str = "",
    ) -> QueryResolutionResult:
        sticky_before = str(getattr(session_context, "last_capability", "") or "").strip()
        default_capability = self._resolve_capability(
            normalized_text=normalized_text,
            intent_hint=intent_hint,
            session_context=session_context,
            followup_target=followup_target,
        )
        rule_context = self.rule_pre_classifier.classify(
            normalized_text=normalized_text,
            entities=entities,
            followup_target=followup_target,
            followup_focus_value=followup_focus_value,
            session_context=session_context,
        )
        llm_capability_candidate = self._infer_semantic_candidate(
            normalized_text=normalized_text,
            intent_hint=intent_hint,
            default_capability=default_capability,
            rule_forced_capability=rule_context.forced_capability,
            followup_target=followup_target,
        )
        resolution_trace = [
            ResolutionTraceEvent(
                stage="resolution_start",
                text=f"Intent guess: {intent_hint or default_capability or 'generic_search'}",
                data={"normalized_text": normalized_text},
            ),
            ResolutionTraceEvent(
                stage="rule_preclassified",
                text="Applied rule pre-classification.",
                data={
                    "matched_rules": list(rule_context.matched_rules),
                    "forced_capability": rule_context.forced_capability,
                    "sticky_capability_before": sticky_before,
                    "sticky_action": rule_context.sticky_action,
                    "unstick_reasons": list(rule_context.unstick_reasons),
                    "followup_type": rule_context.inferred_followup_type,
                    "slot_overrides": dict(rule_context.slot_overrides),
                },
            ),
        ]

        candidates = self.candidate_generator.generate(
            normalized_text=normalized_text,
            intent_hint=default_capability or intent_hint,
            entities=entities,
            session_context=session_context,
            followup_target=followup_target,
            forced_capability=rule_context.forced_capability,
            capability_boosts=rule_context.capability_boosts,
            allow_sticky_bonus=rule_context.allow_sticky_bonus,
        )
        evaluations: list[dict[str, Any]] = []
        candidate_meta: list[dict[str, Any]] = []
        semantic_meta: dict[str, Any] = {}

        for candidate in candidates:
            resolution_trace.append(
                ResolutionTraceEvent(
                    stage="candidate_generated",
                    text=f"Generated candidate {candidate.capability}.",
                    data=candidate.to_dict(),
                )
            )
            explicit_slot_overrides = self._merge_slot_overrides(
                candidate_slots=candidate.extracted_slots,
                rule_slots=rule_context.slot_overrides,
                strict_override_slots=rule_context.strict_override_slots,
            )
            candidate_resolution = self._resolve_for_capability(
                raw_text=raw_text,
                normalized_text=normalized_text,
                capability=candidate.capability,
                intent_hint=intent_hint,
                entities=entities,
                session_context=session_context,
                previous_structured=previous_structured,
                followup_target=followup_target,
                followup_focus_value=followup_focus_value,
                explicit_slot_overrides=explicit_slot_overrides,
                strict_override_slots=rule_context.strict_override_slots,
            )
            semantic = self.semantic_slot_validator.validate(
                capability=candidate.capability,
                normalized_text=normalized_text,
                slots=dict(candidate_resolution.validated_slots or candidate_resolution.resolved_slots),
                followup_target=followup_target,
            )
            semantic_meta[candidate.capability] = semantic.to_dict()
            resolution_trace.append(
                ResolutionTraceEvent(
                    stage="semantic_consistency_checked",
                    text=f"Checked semantic consistency for {candidate.capability}.",
                    data={"capability": candidate.capability, "result": semantic.to_dict()},
                )
            )
            evaluations.append({"candidate": candidate, "resolution": candidate_resolution, "semantic": semantic})
            candidate_meta.append(
                {
                    **candidate.to_dict(),
                    "resolved_slots": dict(candidate_resolution.resolved_slots),
                    "validated_slots": dict(candidate_resolution.validated_slots),
                    "missing_slots": list(candidate_resolution.missing_slots),
                    "clarification_needed": candidate_resolution.clarification_needed,
                }
            )

        llm_arbitration_reason = ""
        if self._should_consult_semantic_arbitration_model(
            normalized_text=normalized_text,
            followup_target=followup_target,
            rule_context=rule_context,
            candidate_meta=candidate_meta,
        ):
            resolution_trace.append(
                ResolutionTraceEvent(
                    stage="llm_arbitration_requested",
                    text="Requested semantic arbitration from LLM lane.",
                    data={
                        "candidate_capabilities": [item.get("capability", "") for item in candidate_meta],
                        "matched_rules": list(rule_context.matched_rules),
                        "sticky_capability_before": sticky_before,
                    },
                )
            )
            llm_choice, llm_arbitration_reason = self._call_semantic_arbitration_callback(
                raw_text=raw_text,
                normalized_text=normalized_text,
                intent_hint=intent_hint,
                default_capability=default_capability,
                followup_target=followup_target,
                session_context=session_context,
                rule_context=rule_context,
                candidate_meta=candidate_meta,
            )
            if llm_choice:
                llm_capability_candidate = llm_choice
            resolution_trace.append(
                ResolutionTraceEvent(
                    stage="llm_arbitration_received",
                    text="Received semantic arbitration result.",
                    data={
                        "llm_capability_candidate": llm_choice,
                        "reason": llm_arbitration_reason,
                    },
                )
            )

        arbitration = self.capability_arbitrator.select(
            evaluations=evaluations,
            intent_hint=default_capability or intent_hint,
            followup_target=followup_target,
            session_context=session_context,
            allow_sticky_bonus=rule_context.allow_sticky_bonus,
            forced_capability=rule_context.forced_capability,
            llm_capability_candidate=llm_capability_candidate,
        )
        selected = next(
            (item for item in evaluations if item["candidate"].capability == arbitration.selected_capability),
            evaluations[0],
        )
        for rejected in arbitration.rejected_candidates:
            resolution_trace.append(
                ResolutionTraceEvent(
                    stage="candidate_rejected",
                    text=f"Rejected candidate {rejected}.",
                    data={"capability": rejected},
                )
            )
        resolution_trace.append(
            ResolutionTraceEvent(
                stage="capability_arbitrated",
                text=f"Selected capability: {arbitration.selected_capability}.",
                data={
                    "selected_capability": arbitration.selected_capability,
                    "rejected_candidates": list(arbitration.rejected_candidates),
                    "llm_capability_candidate": arbitration.llm_capability_candidate,
                    "llm_arbitration_reason": llm_arbitration_reason,
                },
            )
        )

        selected_capability = arbitration.selected_capability
        correction = self.rule_post_validator.validate(
            normalized_text=normalized_text,
            selected_capability=selected_capability,
            context=rule_context,
        )
        correction_applied = correction.correction_applied and correction.final_capability != selected_capability
        if correction_applied:
            resolution_trace.append(
                ResolutionTraceEvent(
                    stage="arbitration_correction_applied",
                    text=f"Corrected capability from {selected_capability} to {correction.final_capability}.",
                    data=correction.to_dict(),
                )
            )
            selected_capability = correction.final_capability
            selected = next(
                (item for item in evaluations if item["candidate"].capability == selected_capability),
                None,
            )
            if selected is None:
                corrected_resolution = self._resolve_for_capability(
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    capability=selected_capability,
                    intent_hint=intent_hint,
                    entities=entities,
                    session_context=session_context,
                    previous_structured=previous_structured,
                    followup_target=followup_target,
                    followup_focus_value=followup_focus_value,
                    explicit_slot_overrides=rule_context.slot_overrides,
                    strict_override_slots=rule_context.strict_override_slots,
                )
                selected = {
                    "candidate": None,
                    "resolution": corrected_resolution,
                    "semantic": semantic_meta.get(selected_capability, {}),
                }

        resolution = selected["resolution"]
        resolution.capability = selected_capability
        resolution.selected_capability = selected_capability
        resolution.rejected_candidates = list(arbitration.rejected_candidates)
        resolution.arbitration_reason = list(arbitration.arbitration_reason)
        resolution.candidates = candidate_meta
        resolution.semantic_validation = semantic_meta
        resolution.matched_rules = list(rule_context.matched_rules)
        resolution.inferred_followup_type = rule_context.inferred_followup_type
        resolution.sticky_capability_before = sticky_before
        resolution.sticky_capability_after = self._sticky_after(
            sticky_before=sticky_before,
            selected_capability=selected_capability,
            sticky_action=rule_context.sticky_action,
            allow_sticky_bonus=rule_context.allow_sticky_bonus,
        )
        resolution.slot_override_applied = bool(rule_context.slot_overrides)
        resolution.llm_capability_candidate = arbitration.llm_capability_candidate or llm_capability_candidate
        resolution.final_capability_decision = selected_capability
        resolution.arbitration_correction_applied = correction_applied
        web_plan = self._resolve_web_browse_plan(
            raw_text=raw_text,
            normalized_text=normalized_text,
            selected_capability=selected_capability,
            intent_hint=intent_hint,
            followup_target=followup_target,
            session_context=session_context,
            resolution=resolution,
        )
        web_task_type = str(web_plan.get("task_type") or "").strip().lower()
        if web_task_type:
            resolution.route_hints["web_task_type"] = web_task_type
            resolution.route_hints["force_web_browse"] = True
            resolution.route_hints["forced_bundle"] = "web-research"
            resolution.route_hints["web_intent_plan"] = dict(web_plan)
            resolution.trace.append(
                ResolutionTraceEvent(
                    stage="web_task_typed",
                    text=f"Typed forced web browse task as {web_task_type}.",
                    data={
                        "task_type": web_task_type,
                        "force_web_browse": True,
                        "forced_bundle": "web-research",
                        "reason": str(web_plan.get("reason") or "").strip(),
                        "search_query": str(web_plan.get("search_query") or "").strip(),
                    },
                )
            )
        resolution.trace = resolution_trace + resolution.trace
        resolution.trace.append(
            ResolutionTraceEvent(
                stage="arbitration_complete",
                text="Capability arbitration complete.",
                data={
                    "selected_capability": selected_capability,
                    "arbitration_reason": list(arbitration.arbitration_reason),
                    "matched_rules": list(rule_context.matched_rules),
                    "sticky_capability_before": sticky_before,
                    "sticky_capability_after": resolution.sticky_capability_after,
                    "llm_capability_candidate": resolution.llm_capability_candidate,
                    "llm_arbitration_reason": llm_arbitration_reason,
                },
            )
        )
        return resolution

    def validate_contracts(self) -> list[str]:
        return self.contract_validator.validate_registry(self.contract_registry)

    def coverage_report(self) -> dict[str, str]:
        return self.contract_validator.coverage_report(self.contract_registry)

    def _normalize_slots(
        self,
        *,
        contract: Any,
        capability: str,
        slots: dict[str, str],
        slot_sources: dict[str, str],
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], list[ResolutionTraceEvent]]:
        normalized_slots: dict[str, str] = {}
        trace: list[ResolutionTraceEvent] = []
        for slot_name, slot_value in list(slots.items()):
            normalizer_name = contract.slot_normalizers.get(slot_name)
            if not normalizer_name:
                normalized_slots[slot_name] = slot_value
                continue
            result = self.slot_normalizer.normalize(slot_name=slot_name, value=slot_value, capability=capability)
            normalized_value = str(result.normalized_value or "").strip()
            if normalized_value:
                slots[slot_name] = normalized_value
                normalized_slots[slot_name] = normalized_value
            else:
                slots.pop(slot_name, None)
                slot_sources.pop(slot_name, None)
            trace.append(
                ResolutionTraceEvent(
                    stage="slot_normalized",
                    text=f"Normalized {slot_name}: {slot_value} -> {normalized_value or '<empty>'}",
                    data={
                        "slot": slot_name,
                        "raw": result.raw_value,
                        "normalized": normalized_value,
                        "confidence": result.confidence,
                        "changed": result.changed,
                    },
                )
            )
        return slots, slot_sources, normalized_slots, trace

    def _validate_slots(
        self,
        *,
        contract: Any,
        capability: str,
        slots: dict[str, str],
        slot_sources: dict[str, str],
    ) -> tuple[
        dict[str, str],
        dict[str, str],
        dict[str, str],
        dict[str, dict[str, Any]],
        list[dict[str, Any]],
        list[ResolutionTraceEvent],
    ]:
        validated_slots: dict[str, str] = {}
        rejected_slots: dict[str, dict[str, Any]] = {}
        carryover_trace: list[dict[str, Any]] = []
        trace: list[ResolutionTraceEvent] = []
        for slot_name, slot_value in list(slots.items()):
            source = slot_sources.get(slot_name, "")
            is_carryover = source in set(contract.carryover_sources.get(slot_name, []))
            if is_carryover and not contract.carryover_safe_slots.get(slot_name, False):
                rejected_slots[slot_name] = {"value": slot_value, "reason": "carryover_not_safe", "source": source}
                slots.pop(slot_name, None)
                slot_sources.pop(slot_name, None)
                carryover_trace.append({"slot": slot_name, "source": source, "accepted": False, "reason": "carryover_not_safe"})
                trace.append(
                    ResolutionTraceEvent(
                        stage="carryover_rejected",
                        text=f"Rejected carryover for {slot_name}.",
                        data={"slot": slot_name, "source": source, "reason": "carryover_not_safe"},
                    )
                )
                continue
            if is_carryover:
                carryover_trace.append({"slot": slot_name, "source": source, "accepted": True})
                trace.append(
                    ResolutionTraceEvent(
                        stage="carryover_applied",
                        text=f"Applied carryover for {slot_name} from {source}.",
                        data={"slot": slot_name, "source": source},
                    )
                )
            validator_name = contract.slot_validators.get(slot_name)
            if not validator_name:
                validated_slots[slot_name] = slot_value
                continue
            result = self.slot_validator.validate(slot_name=slot_name, value=slot_value, capability=capability)
            if result.valid and str(result.normalized_value or "").strip():
                normalized_value = str(result.normalized_value or "").strip()
                slots[slot_name] = normalized_value
                validated_slots[slot_name] = normalized_value
                trace.append(
                    ResolutionTraceEvent(
                        stage="slot_validated",
                        text=f"Validated {slot_name}: {normalized_value}",
                        data={"slot": slot_name, "value": normalized_value, "source": source},
                    )
                )
                continue
            rejected_slots[slot_name] = {
                "value": slot_value,
                "reason": result.reason or "validation_failed",
                "source": source,
            }
            slots.pop(slot_name, None)
            slot_sources.pop(slot_name, None)
            trace.append(
                ResolutionTraceEvent(
                    stage="slot_validation_failed",
                    text=f"Rejected {slot_name}: {result.reason or 'validation_failed'}",
                    data={
                        "slot": slot_name,
                        "value": slot_value,
                        "reason": result.reason or "validation_failed",
                        "requires_clarification": result.requires_clarification,
                        "source": source,
                    },
                )
            )
        return slots, slot_sources, validated_slots, rejected_slots, carryover_trace, trace

    def _resolve_for_capability(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        capability: str,
        intent_hint: str,
        entities: list[DetectedEntity],
        session_context: Any,
        previous_structured: dict[str, Any] | None,
        followup_target: str,
        followup_focus_value: str,
        explicit_slot_overrides: dict[str, Any] | None = None,
        strict_override_slots: set[str] | None = None,
    ) -> QueryResolutionResult:
        trace = [
            ResolutionTraceEvent(
                stage="resolution_start",
                text=f"Intent guess: {intent_hint or capability or 'generic_search'}",
                data={"normalized_text": normalized_text, "candidate_capability": capability},
            )
        ]
        contract = self.contract_registry.get(capability)
        if contract is not None:
            trace.append(
                ResolutionTraceEvent(
                    stage="contract_loaded",
                    text=f"Loaded contract for {capability}.",
                    data={"capability": capability, "required_slots": list(contract.required_slots)},
                )
            )

        slots, slot_sources = self.slot_filler.extract_explicit_slots(
            normalized_text=normalized_text,
            capability=capability,
            entities=entities,
            session_context=session_context,
        )
        direct_location_target = ""
        if capability in {"location_lookup", "display_information"}:
            direct_location_target = self._extract_direct_location_target(raw_text) or self._extract_direct_location_target(normalized_text)
            if direct_location_target and not str(slots.get("location") or "").strip():
                slots["location"] = direct_location_target
                slot_sources["location"] = "direct_location_phrase"
        override_applied = False
        strict_override_slots = set(strict_override_slots or set())
        for slot_name, slot_value in dict(explicit_slot_overrides or {}).items():
            cleaned = str(slot_value or "").strip()
            if not cleaned:
                continue
            if slot_name in strict_override_slots or not str(slots.get(slot_name) or "").strip():
                slots[slot_name] = cleaned
                slot_sources[slot_name] = "rule_override" if slot_name in strict_override_slots else "candidate_hint"
                override_applied = True

        clarification_target = str(getattr(session_context, "last_clarification_target", "") or "").strip()
        if clarification_target == capability and self._looks_like_slot_value_only(normalized_text):
            if capability in {"weather_lookup", "location_lookup", "display_information", "time_lookup"} and not slots.get("location"):
                slots["location"] = normalized_text.strip()
                slot_sources["location"] = "clarification_continuation"
            elif capability in {"news_lookup", "explanation"} and not slots.get("topic"):
                slots["topic"] = normalized_text.strip()
                slot_sources["topic"] = "clarification_continuation"
            elif capability == "generic_search" and not slots.get("query"):
                slots["query"] = normalized_text.strip()
                slot_sources["query"] = "clarification_continuation"

        for slot_name, slot_value in slots.items():
            trace.append(
                ResolutionTraceEvent(
                    stage="slot_extracted",
                    text=f"Extracted {slot_name}: {slot_value}",
                    data={"slot": slot_name, "value": slot_value, "source": slot_sources.get(slot_name, "")},
                )
            )
        if override_applied:
            trace.append(
                ResolutionTraceEvent(
                    stage="slot_override_applied",
                    text="Applied rule/candidate slot overrides.",
                    data={"slot_overrides": dict(explicit_slot_overrides or {}), "strict_override_slots": sorted(strict_override_slots)},
                )
            )

        if contract is not None:
            slots, slot_sources, normalized_slots, normalized_trace = self._normalize_slots(
                contract=contract,
                capability=capability,
                slots=slots,
                slot_sources=slot_sources,
            )
            trace.extend(normalized_trace)
            suppressed_default_slots = {"location"} if direct_location_target else set()
            if capability in {"generic_search", "news_lookup"} and self.slot_filler.should_block_topic_carryover(normalized_text):
                suppressed_default_slots.add("topic")
            slots, slot_sources, defaults = self.slot_filler.apply_default_sources(
                contract=contract,
                normalized_text=normalized_text,
                slots=slots,
                slot_sources=slot_sources,
                session_context=session_context,
                previous_structured=previous_structured,
                followup_target=followup_target,
                followup_focus_value=followup_focus_value,
                suppress_slots=suppressed_default_slots,
            )
            for item in defaults:
                trace.append(
                    ResolutionTraceEvent(
                        stage="slot_default_applied",
                        text=f"Applied default {item['slot']}: {item['value']}",
                        data=item,
                    )
                )
            slots, slot_sources, normalized_slots, default_normalized_trace = self._normalize_slots(
                contract=contract,
                capability=capability,
                slots=slots,
                slot_sources=slot_sources,
            )
            trace.extend(default_normalized_trace)
            slots, slot_sources, validated_slots, rejected_slots, carryover_trace, validation_trace = self._validate_slots(
                contract=contract,
                capability=capability,
                slots=slots,
                slot_sources=slot_sources,
            )
            trace.extend(validation_trace)
            missing_slots = self.slot_filler.missing_required_slots(contract, slots)
            if missing_slots:
                for slot_name in missing_slots:
                    trace.append(
                        ResolutionTraceEvent(
                            stage="missing_required_slot",
                            text=f"Missing required slot: {slot_name}",
                            data={"slot": slot_name},
                        )
                    )
                slots, slot_sources, retry_defaults = self.slot_filler.apply_default_sources(
                    contract=contract,
                    normalized_text=normalized_text,
                    slots=slots,
                    slot_sources=slot_sources,
                    session_context=session_context,
                    previous_structured=previous_structured,
                    followup_target=followup_target,
                    followup_focus_value=followup_focus_value,
                    suppress_slots=suppressed_default_slots,
                )
                for item in retry_defaults:
                    trace.append(
                        ResolutionTraceEvent(
                            stage="slot_default_applied",
                            text=f"Re-applied default {item['slot']}: {item['value']}",
                            data={**item, "retry_after_validation": True},
                        )
                    )
                if retry_defaults:
                    slots, slot_sources, normalized_slots, retry_normalized_trace = self._normalize_slots(
                        contract=contract,
                        capability=capability,
                        slots=slots,
                        slot_sources=slot_sources,
                    )
                    trace.extend(retry_normalized_trace)
                    slots, slot_sources, validated_slots, retry_rejected_slots, carryover_trace, retry_validation_trace = self._validate_slots(
                        contract=contract,
                        capability=capability,
                        slots=slots,
                        slot_sources=slot_sources,
                    )
                    trace.extend(retry_validation_trace)
                    rejected_slots.update(retry_rejected_slots)
                    missing_slots = self.slot_filler.missing_required_slots(contract, slots)
        else:
            normalized_slots = dict(slots)
            validated_slots = dict(slots)
            rejected_slots = {}
            carryover_trace = []
            missing_slots = []

        resolution = QueryResolutionResult(
            capability=capability,
            normalized_query=self._build_executable_query(
                raw_text=raw_text,
                normalized_text=normalized_text,
                capability=capability,
                slots=slots,
            ),
            intent_hint=intent_hint,
            execution_mode="single",
            primary_intent=CapabilityIntent(
                capability=capability,
                slots=dict(validated_slots or slots),
                confidence=1.0,
                normalized_query="",
                missing_slots=list(missing_slots),
            ),
            resolved_slots=slots,
            normalized_slots=normalized_slots,
            validated_slots=validated_slots,
            rejected_slots=rejected_slots,
            missing_slots=missing_slots,
            slot_sources=slot_sources,
            carryover_trace=carryover_trace,
            route_hints={"resolved_slots": dict(validated_slots or slots)},
            trace=trace,
        )
        resolution.primary_intent.normalized_query = resolution.normalized_query
        resolution.primary_intent.requires_clarification = False
        resolution.selected_capability = capability
        resolution = self.clarification_policy.apply(resolution, contract)
        if resolution.primary_intent is not None:
            resolution.primary_intent.requires_clarification = resolution.clarification_needed
        resolution.trace.append(
            ResolutionTraceEvent(
                stage="resolution_complete",
                text="Query resolution complete.",
                data={
                    "capability": capability,
                    "slots": dict(resolution.resolved_slots),
                    "missing_slots": list(resolution.missing_slots),
                    "clarification_needed": resolution.clarification_needed,
                },
            )
        )
        return resolution

    def _resolve_capability(
        self,
        *,
        normalized_text: str,
        intent_hint: str,
        session_context: Any,
        followup_target: str,
    ) -> str:
        clarification_target = str(getattr(session_context, "last_clarification_target", "") or "").strip()
        if clarification_target and self._looks_like_slot_value_only(normalized_text):
            return clarification_target

        direct_location = self._extract_direct_location_target(normalized_text)
        if direct_location:
            return "location_lookup"
        if self._looks_like_time_query(normalized_text):
            return "time_lookup"
        if self._looks_like_weather_query(normalized_text):
            return "weather_lookup"
        if self._looks_like_map_query(normalized_text):
            return "display_information"
        if self._looks_like_location_query(normalized_text):
            return "location_lookup"
        if self._looks_like_news_query(normalized_text):
            return "news_lookup"
        if self._looks_like_compare_query(normalized_text):
            return "generic_search"
        if self._looks_like_explanation_query(normalized_text):
            return "explanation"
        if self._looks_like_search_query(normalized_text):
            return "generic_search"

        last_capability = str(getattr(session_context, "last_capability", "") or "").strip()
        if last_capability == "time_lookup" and self._looks_like_slot_value_only(normalized_text):
            return "time_lookup"
        if last_capability == "weather_lookup" and self._looks_like_slot_value_only(normalized_text):
            return "weather_lookup"
        if last_capability in {"location_lookup", "display_information"} and self._looks_like_slot_value_only(normalized_text):
            return "location_lookup"
        if last_capability == "explanation" and self._looks_like_explanation_followup(normalized_text):
            return "explanation"
        if last_capability == "generic_search" and self._looks_like_search_followup(normalized_text):
            return "generic_search"

        if followup_target == "weather":
            if self._looks_like_time_query(normalized_text):
                return "time_lookup"
            return "weather_lookup"
        if followup_target == "location":
            if self._looks_like_time_query(normalized_text):
                return "time_lookup"
            if direct_location:
                return "location_lookup"
            if self._looks_like_map_query(normalized_text):
                return "display_information"
            return "location_lookup"

        if intent_hint in {
            "weather_lookup",
            "time_lookup",
            "location_lookup",
            "display_information",
            "news_lookup",
            "generic_search",
            "explanation",
            "system_action",
        }:
            return intent_hint
        return "generic_search"

    def _infer_semantic_candidate(
        self,
        *,
        normalized_text: str,
        intent_hint: str,
        default_capability: str,
        rule_forced_capability: str,
        followup_target: str,
    ) -> str:
        if rule_forced_capability:
            return ""
        lowered = str(normalized_text or "").strip().lower()
        compare_tokens = ("区别", "差异", "对比", "比较", "difference", "compare", "comparison", " vs ", "versus")
        if any(token in lowered for token in compare_tokens):
            return "generic_search"
        if intent_hint in {
            "weather_lookup",
            "time_lookup",
            "location_lookup",
            "display_information",
            "news_lookup",
            "generic_search",
            "explanation",
            "system_action",
        }:
            return intent_hint
        if followup_target == "weather" and self._looks_like_slot_value_only(normalized_text):
            return "weather_lookup"
        if followup_target == "location" and self._looks_like_slot_value_only(normalized_text):
            return "location_lookup"
        return default_capability if default_capability != "generic_search" else ""

    def _should_consult_semantic_arbitration_model(
        self,
        *,
        normalized_text: str,
        followup_target: str,
        rule_context: Any,
        candidate_meta: list[dict[str, Any]],
    ) -> bool:
        if self.semantic_arbitration_callback is None:
            return False
        if str(getattr(rule_context, "forced_capability", "") or "").strip():
            return False
        if len(candidate_meta) < 2:
            return False
        strong_rule_markers = {"realtime.weather", "realtime.time", "realtime.map", "realtime.location"}
        matched_rules = set(getattr(rule_context, "matched_rules", []) or [])
        top_score = float(candidate_meta[0].get("score", 0.0) or 0.0)
        second_score = float(candidate_meta[1].get("score", 0.0) or 0.0)
        score_gap = abs(top_score - second_score)
        if score_gap <= 0.18:
            return True
        if followup_target and len(normalized_text.strip()) <= 18:
            return True
        if len(normalized_text.strip()) >= 18 and not (matched_rules & strong_rule_markers):
            return True
        return False

    def _call_semantic_arbitration_callback(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        intent_hint: str,
        default_capability: str,
        followup_target: str,
        session_context: Any,
        rule_context: Any,
        candidate_meta: list[dict[str, Any]],
    ) -> tuple[str, str]:
        callback = self.semantic_arbitration_callback
        if callback is None:
            return "", ""
        try:
            result = callback(
                raw_text=raw_text,
                normalized_text=normalized_text,
                intent_hint=intent_hint,
                default_capability=default_capability,
                followup_target=followup_target,
                session_context=session_context,
                matched_rules=list(getattr(rule_context, "matched_rules", []) or []),
                candidate_meta=candidate_meta,
            )
        except Exception:
            return "", ""
        if isinstance(result, dict):
            capability = str(result.get("capability") or "").strip()
            reason = str(result.get("reason") or "").strip()
            return capability, reason
        return str(result or "").strip(), ""

    def _sanitize_followup_focus_value(
        self,
        *,
        normalized_text: str,
        followup_target: str,
        followup_focus_value: str,
    ) -> str:
        focus = str(followup_focus_value or "").strip()
        if not focus:
            return ""
        lowered_focus = focus.lower()
        lowered_text = str(normalized_text or "").strip().lower()
        generic_focus_tokens = {
            "天气",
            "地图",
            "显示地图",
            "打开地图",
            "看看地图",
            "时间",
            "几点",
            "weather",
            "map",
            "time",
            "open map",
            "show map",
            "display map",
        }
        if lowered_focus in {token.lower() for token in generic_focus_tokens}:
            return ""
        if self._looks_like_noisy_context_value(focus):
            return ""
        if followup_target == "location" and self._looks_like_map_query(lowered_text):
            return ""
        if followup_target == "weather" and self._looks_like_weather_query(lowered_text):
            return ""
        if self._looks_like_time_query(lowered_focus):
            return ""
        return focus

    @staticmethod
    def _looks_like_noisy_context_value(value: str) -> bool:
        cleaned = str(value or "").strip()
        if not cleaned:
            return False
        lowered = cleaned.lower()
        noisy_tokens = (
            "no running session file found",
            "web access failed",
            "runtime_stream_error",
            "error_card",
            "type: error_card",
            "message:",
            "traceback",
            "exception",
            "data\\runtime\\fairy_desktop_dev.json",
        )
        if any(token in lowered for token in noisy_tokens):
            return True
        if re.search(r"[a-z]:\\", lowered):
            return True
        if ".json" in lowered and ("runtime" in lowered or "session" in lowered):
            return True
        return False

    @staticmethod
    def _merge_slot_overrides(
        *,
        candidate_slots: dict[str, Any],
        rule_slots: dict[str, Any],
        strict_override_slots: set[str],
    ) -> dict[str, Any]:
        merged = dict(candidate_slots or {})
        for slot_name, slot_value in dict(rule_slots or {}).items():
            if slot_name in strict_override_slots or not merged.get(slot_name):
                merged[slot_name] = slot_value
        return merged

    @staticmethod
    def _sticky_after(
        *,
        sticky_before: str,
        selected_capability: str,
        sticky_action: str,
        allow_sticky_bonus: bool,
    ) -> str:
        if not sticky_before:
            return ""
        if sticky_action in {"switch", "unstick"} or not allow_sticky_bonus:
            return ""
        return selected_capability if selected_capability == sticky_before else ""

    def _extract_direct_location_target(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        patterns = (
            re.compile(r"^(?:显示|打开|看看|看一看|查看)?\s*(?P<location>.+?)\s*地图$", re.IGNORECASE),
            re.compile(r"^(?P<location>.+?)\s*(?:在哪里|在哪儿|在哪)$", re.IGNORECASE),
            re.compile(r"^(?:导航到|导航去|前往)\s*(?P<location>.+)$", re.IGNORECASE),
            re.compile(r"^(?:看看|看一看|查看)\s*(?P<location>.+?)\s*位置$", re.IGNORECASE),
            re.compile(r"^(?:show|display|open)\s+map\s+for\s+(?P<location>.+)$", re.IGNORECASE),
            re.compile(r"^(?:where\s+is)\s+(?P<location>.+)$", re.IGNORECASE),
        )
        for pattern in patterns:
            match = pattern.match(cleaned)
            if not match:
                continue
            candidate = str(match.group("location") or "").strip()
            if candidate:
                return candidate
        return ""

    def _looks_like_slot_value_only(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        blocked = (
            "天气",
            "地图",
            "新闻",
            "解释",
            "为什么",
            "几点",
            "时间",
            "time",
            "weather",
            "map",
            "news",
            "explain",
        )
        return bool(cleaned) and len(cleaned) <= 24 and not any(token in cleaned.lower() for token in blocked)

    def _looks_like_search_followup(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("价格", "price", "值不值得买", "worth", "怎么买", "how much", "能不能买", "功耗", "power", "性能", "performance")
        return any(token in lowered for token in tokens)

    def _looks_like_explanation_followup(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("关系", "relation", "difference", "区别", "为什么", "what about", "embedding", "向量", "原理")
        return any(token in lowered for token in tokens)

    def _looks_like_time_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("几点", "时间", "当地时间", "current time", "what time", "local time", "time now", "time")
        return any(token in lowered for token in tokens)

    def _looks_like_weather_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("天气", "温度", "气温", "forecast", "weather", "下雨", "冷不冷")
        return any(token in lowered for token in tokens)

    def _looks_like_map_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("地图", "show map", "display map", "open map", "看看地图", "打开地图", "显示地图")
        return any(token in lowered for token in tokens)

    def _looks_like_location_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("在哪里", "在哪", "where is", "地址", "位置", "地点", "在哪个州", "在哪个国家", "在中国吗")
        return any(token in lowered for token in tokens)

    def _looks_like_news_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return any(token in lowered for token in ("新闻", "news", "latest", "快讯", "头条"))

    def _looks_like_explanation_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("解释", "是什么", "什么意思", "为什么", "怎么回事", "what is", "explain", "what does")
        return any(token in lowered for token in tokens)

    def _looks_like_compare_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("区别", "差异", "对比", "比较", "difference", "compare", "comparison", " vs ", "versus")
        return any(token in lowered for token in tokens)

    def _looks_like_search_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        tokens = ("查", "搜", "帮我查", "帮我找", "看看", "有没有", "哪个", "search", "find", "look up", "check")
        return any(token in lowered for token in tokens)

    def _should_continue_active_plan(self, text: str, session_context: Any, active_plan_state: dict[str, Any]) -> bool:
        if not active_plan_state:
            return False
        status = str(active_plan_state.get("status") or "").strip().lower()
        if status not in {"running", "completed"}:
            return False
        cleaned = str(text or "").strip()
        if not cleaned or len(cleaned) > 24:
            return False
        last_step_capability = str(getattr(session_context, "last_step_capability", "") or "").strip()
        plan_steps = list(active_plan_state.get("steps") or [])
        plan_capabilities = {str(item.get("capability") or "").strip() for item in plan_steps}
        if last_step_capability == "generic_search" and self._looks_like_search_followup(cleaned):
            return True
        if "generic_search" in plan_capabilities and self._looks_like_search_followup(cleaned):
            return True
        if last_step_capability == "explanation" and self._looks_like_explanation_followup(cleaned):
            return True
        if last_step_capability in {"location_lookup", "display_information"} and self._looks_like_weather_query(cleaned):
            return True
        if last_step_capability in {"location_lookup", "display_information", "weather_lookup"} and self._looks_like_time_query(cleaned):
            return True
        if last_step_capability == "weather_lookup" and self._looks_like_news_query(cleaned):
            return True
        return False

    def _build_executable_query(self, *, raw_text: str, normalized_text: str, capability: str, slots: dict[str, str]) -> str:
        text = str(raw_text or "").strip()
        lowered = normalized_text.lower()
        location = str(slots.get("location") or "").strip()
        date = str(slots.get("date") or "").strip()
        topic = str(slots.get("topic") or "").strip()
        query = str(slots.get("query") or "").strip()
        action_name = str(slots.get("action_name") or "").strip()

        if capability == "weather_lookup" and location:
            if location.lower() in lowered and self._looks_like_weather_query(text):
                return normalized_text
            suffix = f" {date}" if date else ""
            return f"weather in {location}{suffix}".strip()
        if capability == "time_lookup" and location:
            if location.lower() in lowered and self._looks_like_time_query(text):
                return normalized_text
            return f"current time in {location}".strip()
        if capability in {"location_lookup", "display_information"} and location:
            if self._looks_like_map_query(text) or self._looks_like_location_query(text) or self._extract_direct_location_target(text):
                return normalized_text
            return f"where is {location}"
        if capability == "news_lookup":
            return normalized_text or topic or text
        if capability == "generic_search":
            return normalized_text or query or text
        if capability == "explanation":
            if topic and topic.lower() not in lowered:
                if text and self._looks_like_explanation_followup(text):
                    return f"Explain {topic}. {text}"
                return f"explain {topic}"
            return normalized_text
        if capability == "system_action":
            return action_name or normalized_text
        return normalized_text

    def _resolve_web_browse_plan(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        selected_capability: str,
        intent_hint: str,
        followup_target: str,
        session_context: Any,
        resolution: QueryResolutionResult,
    ) -> dict[str, Any]:
        explicit_url = self._contains_explicit_url(normalized_text)
        allowed_tasks = {"general_info", "specs", "compare", "news", "release", "product_lookup"}
        policy_block_capabilities = {"weather_lookup", "time_lookup", "location_lookup", "display_information", "system_action"}
        if selected_capability in policy_block_capabilities:
            return {}
        if explicit_url:
            return {
                "task_type": "general_info",
                "should_browse": True,
                "reason": "explicit_url",
                "search_query": str(normalized_text or raw_text or "").strip(),
            }
        model_plan = self._call_web_browse_decision_callback(
            raw_text=raw_text,
            normalized_text=normalized_text,
            selected_capability=selected_capability,
            intent_hint=intent_hint,
            followup_target=followup_target,
            session_context=session_context,
            resolution=resolution,
        )
        if model_plan:
            task_type = str(model_plan.get("task_type") or "").strip().lower()
            should_browse = bool(model_plan.get("should_browse"))
            if should_browse and task_type in allowed_tasks:
                return {
                    "task_type": task_type,
                    "should_browse": True,
                    "reason": str(model_plan.get("reason") or "model_web_plan").strip(),
                    "entity": str(model_plan.get("entity") or "").strip(),
                    "search_query": str(model_plan.get("search_query") or normalized_text).strip(),
                    "answer_focus": str(model_plan.get("answer_focus") or "").strip(),
                    "confidence": float(model_plan.get("confidence") or 0.0),
                }
        fallback_task_type = self._infer_forced_web_task_type(normalized_text)
        if fallback_task_type:
            return {
                "task_type": fallback_task_type,
                "should_browse": True,
                "reason": "rule_fallback",
                "search_query": str(normalized_text or raw_text or "").strip(),
            }
        return {}

    def _call_web_browse_decision_callback(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        selected_capability: str,
        intent_hint: str,
        followup_target: str,
        session_context: Any,
        resolution: QueryResolutionResult,
    ) -> dict[str, Any]:
        callback = self.web_browse_decision_callback
        if callback is None:
            return {}
        try:
            result = callback(
                raw_text=raw_text,
                normalized_text=normalized_text,
                selected_capability=selected_capability,
                intent_hint=intent_hint,
                followup_target=followup_target,
                session_context=session_context,
                resolution={
                    "capability": resolution.capability,
                    "resolved_slots": dict(resolution.resolved_slots or {}),
                    "validated_slots": dict(resolution.validated_slots or {}),
                    "route_hints": dict(resolution.route_hints or {}),
                },
            )
        except Exception:
            return {}
        return dict(result or {}) if isinstance(result, dict) else {}

    @staticmethod
    def _contains_explicit_url(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return "http://" in lowered or "https://" in lowered or "www." in lowered

    def _infer_forced_web_task_type(self, text: str) -> str:
        cleaned = str(text or "").strip()
        lowered = cleaned.lower()
        if not cleaned:
            return ""
        release_terms = (
            "发布了吗",
            "什么时候发布",
            "发布时间",
            "发售了吗",
            "发售时间",
            "上市了吗",
            "上市时间",
            "release date",
            "released",
            "announced",
            "launch date",
        )
        specs_terms = (
            "参数",
            "配置",
            "规格",
            "详细配置",
            "技术规格",
            "spec",
            "specs",
            "specifications",
            "technical specifications",
            "configuration",
        )
        news_terms = (
            "新闻",
            "新消息",
            "最近有什么新消息",
            "今天有什么新闻",
            "今天有什么新消息",
            "最新",
            "news",
            "latest",
            "newsroom",
            "what's new",
            "whats new",
            "updates",
        )
        if any(term in cleaned for term in release_terms if not term.isascii()) or any(
            term in lowered for term in release_terms if term.isascii()
        ):
            return "release"
        if self._looks_like_compare_query(cleaned):
            return "compare"
        if any(term in cleaned for term in specs_terms if not term.isascii()) or any(
            term in lowered for term in specs_terms if term.isascii()
        ):
            return "specs"
        if any(term in cleaned for term in news_terms if not term.isascii()) or any(
            term in lowered for term in news_terms if term.isascii()
        ):
            return "news"
        if self._should_force_general_web_browse(cleaned, lowered):
            return "general_info"
        return ""

    def _should_force_general_web_browse(self, cleaned: str, lowered: str) -> bool:
        explicit_web_terms = (
            "官网",
            "文档",
            "docs",
            "guide",
            "教程",
            "指南",
            "帮助",
            "help",
            "网站",
            "网页",
            "url",
            "link",
            "官网是做什么的",
            "official",
            "website",
        )
        general_info_phrases = (
            "是什么",
            "是干嘛的",
            "介绍",
            "介绍一下",
            "怎么用",
            "如何",
            "如何使用",
            "怎么部署",
            "what is",
            "about",
            "overview",
        )
        if "http://" in lowered or "https://" in lowered or "www." in lowered:
            return True
        if any(term in cleaned for term in explicit_web_terms if not term.isascii()) or any(
            term in lowered for term in explicit_web_terms if term.isascii()
        ):
            return True
        if not (
            any(term in cleaned for term in general_info_phrases if not term.isascii())
            or any(term in lowered for term in general_info_phrases if term.isascii())
        ):
            return False
        if self._looks_like_explanation_query(cleaned):
            return False
        product_or_site_terms = (
            "产品",
            "官网",
            "文档",
            "平台",
            "公司",
            "服务",
            "product",
            "platform",
            "company",
            "service",
            "app",
            "sdk",
            "api",
        )
        return any(term in cleaned for term in product_or_site_terms if not term.isascii()) or any(
            term in lowered for term in product_or_site_terms if term.isascii()
        )

    def _split_multi_intent_segments(self, text: str) -> list[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        normalized = cleaned
        connectors = [
            "，再给我",
            ",再给我",
            "，再帮我",
            ",再帮我",
            "，然后",
            ",然后",
            "然后",
            "再给我",
            "再帮我",
            "，并且",
            ",并且",
            "并且",
            "，顺便",
            ",顺便",
            "顺便",
            "，再看看",
            ",再看看",
            "再看看",
            "，再查",
            ",再查",
            "再查",
            "，再解释一下",
            ",再解释一下",
            "再解释一下",
        ]
        for connector in connectors:
            normalized = normalized.replace(connector, "|||")
        segments = [part.strip(" ，。！？?") for part in normalized.split("|||") if part.strip(" ，。！？?")]
        return segments[:3]

    def _infer_segment_intent_hint(self, segment: str, previous_intent: CapabilityIntent | None) -> str:
        lowered = str(segment or "").strip().lower()
        if self._looks_like_time_query(segment):
            return "time_lookup"
        if self._looks_like_weather_query(segment):
            return "weather_lookup"
        if self._looks_like_map_query(segment):
            return "display_information"
        if self._looks_like_location_query(segment):
            return "location_lookup"
        if self._looks_like_news_query(segment):
            return "news_lookup"
        if self._looks_like_explanation_query(segment):
            return "explanation"
        if self._looks_like_search_query(segment):
            return "generic_search"
        if previous_intent is not None and any(token in lowered for token in ("那里", "那边", "那个", "that", "there")):
            return previous_intent.capability
        return previous_intent.capability if previous_intent is not None else "generic_search"

    def _infer_segment_followup(self, segment: str, previous_intent: CapabilityIntent | None) -> tuple[str, str]:
        if previous_intent is None:
            return "", ""
        lowered = str(segment or "").strip().lower()
        previous_location = str(previous_intent.slots.get("location") or "").strip()
        previous_topic = str(previous_intent.slots.get("topic") or previous_intent.slots.get("query") or "").strip()
        if self._looks_like_weather_query(segment) and previous_location:
            return "weather", previous_location
        if self._looks_like_time_query(segment) and previous_location:
            return "location", previous_location
        if self._looks_like_map_query(segment) and previous_location:
            return "location", previous_location
        if any(token in lowered for token in ("那里", "那边", "that", "there")) and previous_location:
            return "location", previous_location
        if previous_intent.capability == "explanation" and previous_topic:
            return "", previous_topic
        if previous_intent.capability == "generic_search" and previous_topic:
            return "", previous_topic
        return "", ""

    def _apply_shadow_context(self, context: Any, capability_intent: CapabilityIntent) -> None:
        context.last_capability = capability_intent.capability
        location = str(capability_intent.slots.get("location") or "").strip()
        topic = str(capability_intent.slots.get("topic") or "").strip()
        query = str(capability_intent.slots.get("query") or "").strip()
        if location:
            context.active_location_target = location
            context.active_city = location
            if capability_intent.capability in {"weather_lookup", "time_lookup"}:
                context.active_weather_location = location
        if topic:
            context.active_topic = topic
        if capability_intent.capability == "generic_search" and query:
            context.last_generic_query = query
            if not topic:
                context.active_topic = query
        if capability_intent.capability == "explanation" and topic:
            context.last_explanation_topic = topic

    def _shadow_structured_from_intent(self, capability_intent: CapabilityIntent) -> dict[str, Any]:
        slots = dict(capability_intent.slots)
        if capability_intent.capability == "weather_lookup":
            return {"type": "weather", "city": slots.get("location", ""), "weather_location": slots.get("location", "")}
        if capability_intent.capability in {"location_lookup", "display_information"}:
            return {"type": "location", "title": slots.get("location", ""), "city": slots.get("location", "")}
        if capability_intent.capability == "news_lookup":
            return {"type": "news_list", "topic": slots.get("topic", ""), "title": slots.get("topic", "")}
        if capability_intent.capability == "time_lookup":
            return {"type": "time", "location": slots.get("location", ""), "title": slots.get("location", "")}
        return {"type": capability_intent.capability, **slots}
