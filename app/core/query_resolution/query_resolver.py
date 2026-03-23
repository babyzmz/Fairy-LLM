from __future__ import annotations

import copy
import re
from typing import Any

from app.core.perception import EntityExtractor
from app.core.perception.perception_models import DetectedEntity

from .capability_contracts import CapabilityContractRegistry
from .capability_arbitrator import CapabilityArbitrator
from .capability_candidates import CapabilityCandidateGenerator
from .clarification_policy import ClarificationPolicy
from .contract_validator import ContractValidator
from .resolution_models import CapabilityIntent, QueryResolutionResult, ResolutionTraceEvent
from .semantic_slot_validator import SemanticSlotValidator
from .slot_normalizer import SlotNormalizer
from .slot_filler import SlotFiller
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
        aggregate_secondary_trace: list[ResolutionTraceEvent] = []
        local_previous_structured = dict(previous_structured or {})

        for index, segment in enumerate(segments[:3]):
            segment_entities = self.segment_entity_extractor.extract(segment)
            segment_followup_target, segment_focus = self._infer_segment_followup(segment, previous_intent=intents[-1] if intents else None)
            segment_result = self._resolve_single(
                raw_text=segment,
                normalized_text=segment,
                intent_hint=self._infer_segment_intent_hint(segment, intents[-1] if intents else None),
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
            aggregate_secondary_trace.extend(segment_result.trace)
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

        primary = intents[0]
        secondary = intents[1:]
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
        base.primary_intent = primary
        base.secondary_intents = secondary
        base.execution_mode = "sequential" if secondary and not any(item.requires_clarification for item in intents) else "single"
        base.trace = trace + aggregate_secondary_trace
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
        default_capability = self._resolve_capability(
            normalized_text=normalized_text,
            intent_hint=intent_hint,
            session_context=session_context,
            followup_target=followup_target,
        )
        resolution_trace = [
            ResolutionTraceEvent(
                stage="resolution_start",
                text=f"Intent guess: {intent_hint or default_capability or 'generic_search'}",
                data={"normalized_text": normalized_text},
            )
        ]
        candidates = self.candidate_generator.generate(
            normalized_text=normalized_text,
            intent_hint=default_capability or intent_hint,
            entities=entities,
            session_context=session_context,
            followup_target=followup_target,
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
                explicit_slot_overrides=candidate.extracted_slots,
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
                    data={
                        "capability": candidate.capability,
                        "result": semantic.to_dict(),
                    },
                )
            )
            evaluations.append(
                {
                    "candidate": candidate,
                    "resolution": candidate_resolution,
                    "semantic": semantic,
                }
            )
            candidate_meta.append(
                {
                    **candidate.to_dict(),
                    "resolved_slots": dict(candidate_resolution.resolved_slots),
                    "validated_slots": dict(candidate_resolution.validated_slots),
                    "missing_slots": list(candidate_resolution.missing_slots),
                    "clarification_needed": candidate_resolution.clarification_needed,
                }
            )

        arbitration = self.capability_arbitrator.select(
            evaluations=evaluations,
            intent_hint=default_capability or intent_hint,
            followup_target=followup_target,
            session_context=session_context,
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
                },
            )
        )

        resolution = selected["resolution"]
        resolution.capability = arbitration.selected_capability
        resolution.selected_capability = arbitration.selected_capability
        resolution.rejected_candidates = list(arbitration.rejected_candidates)
        resolution.arbitration_reason = list(arbitration.arbitration_reason)
        resolution.candidates = candidate_meta
        resolution.semantic_validation = semantic_meta
        resolution.trace = resolution_trace + resolution.trace
        resolution.trace.append(
            ResolutionTraceEvent(
                stage="arbitration_complete",
                text="Capability arbitration complete.",
                data={
                    "selected_capability": arbitration.selected_capability,
                    "arbitration_reason": list(arbitration.arbitration_reason),
                },
            )
        )
        return resolution

    def validate_contracts(self) -> list[str]:
        return self.contract_validator.validate_registry(self.contract_registry)

    def coverage_report(self) -> dict[str, str]:
        return self.contract_validator.coverage_report(self.contract_registry)

    def _normalize_slots(self, *, contract, capability: str, slots: dict[str, str], slot_sources: dict[str, str]):
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

    def _validate_slots(self, *, contract, capability: str, slots: dict[str, str], slot_sources: dict[str, str]):
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
        for slot_name, slot_value in dict(explicit_slot_overrides or {}).items():
            if slot_value in {None, ""}:
                continue
            slots.setdefault(slot_name, str(slot_value).strip())
            slot_sources.setdefault(slot_name, "candidate_hint")

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

        if contract is not None:
            slots, slot_sources, normalized_slots, normalized_trace = self._normalize_slots(
                contract=contract,
                capability=capability,
                slots=slots,
                slot_sources=slot_sources,
            )
            trace.extend(normalized_trace)
            slots, slot_sources, defaults = self.slot_filler.apply_default_sources(
                contract=contract,
                normalized_text=normalized_text,
                slots=slots,
                slot_sources=slot_sources,
                session_context=session_context,
                previous_structured=previous_structured,
                followup_target=followup_target,
                followup_focus_value=followup_focus_value,
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
                slots, slot_sources, retry_defaults = self.slot_filler.apply_default_sources(
                    contract=contract,
                    normalized_text=normalized_text,
                    slots=slots,
                    slot_sources=slot_sources,
                    session_context=session_context,
                    previous_structured=previous_structured,
                    followup_target=followup_target,
                    followup_focus_value=followup_focus_value,
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

    def _resolve_capability(self, *, normalized_text: str, intent_hint: str, session_context: Any, followup_target: str) -> str:
        lowered = str(normalized_text or "").strip().lower()
        clarification_target = str(getattr(session_context, "last_clarification_target", "") or "").strip()
        if clarification_target and self._looks_like_slot_value_only(normalized_text):
            return clarification_target
        last_capability = str(getattr(session_context, "last_capability", "") or "").strip()
        if last_capability == "time_lookup" and self._looks_like_slot_value_only(normalized_text):
            return "time_lookup"
        if last_capability == "weather_lookup" and self._looks_like_slot_value_only(normalized_text):
            return "weather_lookup"
        if last_capability in {"location_lookup", "display_information"} and any(token in lowered for token in ("地图", "map", "显示")):
            return "display_information"
        if last_capability in {"location_lookup", "display_information", "weather_lookup"} and self._looks_like_time_query(normalized_text):
            return "time_lookup"
        if last_capability == "explanation" and self._looks_like_explanation_followup(normalized_text):
            return "explanation"
        if last_capability == "generic_search" and self._looks_like_search_followup(normalized_text):
            return "generic_search"
        if followup_target in {"weather", "location"} and self._looks_like_time_query(normalized_text):
            return "time_lookup"
        if followup_target == "weather":
            return "weather_lookup"
        if followup_target == "location" and any(token in lowered for token in ("地图", "map", "显示")):
            return "display_information"
        if followup_target == "location":
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

    def _looks_like_slot_value_only(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        blocked = ("天气", "地图", "新闻", "解释", "为什么", "几点", "时间", "time", "weather", "map", "news", "explain")
        return bool(cleaned) and len(cleaned) <= 24 and not any(token in cleaned for token in blocked)

    def _looks_like_search_followup(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return any(
            token in lowered
            for token in (
                "价格",
                "price",
                "值不值得买",
                "worth",
                "怎么买",
                "how much",
                "能不能买",
                "功耗",
                "power",
                "性能",
                "performance",
            )
        )

    def _looks_like_explanation_followup(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return any(token in lowered for token in ("关系", "relation", "difference", "区别", "为什么", "what about", "embedding", "向量", "原理"))

    def _looks_like_time_query(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return any(token in lowered for token in ("几点", "时间", "当地时间", "current time", "what time", "local time", "time"))

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
        if last_step_capability in {"location_lookup", "display_information"} and any(token in cleaned for token in ("天气", "weather")):
            return True
        if last_step_capability in {"location_lookup", "display_information", "weather_lookup"} and self._looks_like_time_query(cleaned):
            return True
        if last_step_capability == "weather_lookup" and any(token in cleaned for token in ("新闻", "news")):
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
            if location.lower() in lowered:
                return normalized_text
            suffix = f" {date}" if date else ""
            return f"weather in {location}{suffix}".strip()
        if capability == "time_lookup" and location:
            if location.lower() in lowered and self._looks_like_time_query(text):
                return normalized_text
            return f"current time in {location}".strip()
        if capability in {"location_lookup", "display_information"} and location:
            if "地图" in text or "map" in lowered:
                return f"show map for {location}"
            return f"where is {location}"
        if capability == "news_lookup":
            if topic and date:
                return f"{topic} news {date}".strip()
            return normalized_text or f"{topic} news".strip()
        if capability == "generic_search":
            if query and topic and topic not in query:
                return f"{topic} {query}".strip()
            return query or normalized_text
        if capability == "explanation":
            if topic and topic.lower() not in lowered:
                if text and self._looks_like_explanation_followup(text):
                    return f"Explain {topic}. {text}"
                return f"explain {topic}"
            return normalized_text
        if capability == "system_action":
            return action_name or normalized_text
        return normalized_text

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
        segments = [part.strip(" ，,") for part in normalized.split("|||") if part.strip(" ，,")]
        return segments[:3]

    def _infer_segment_intent_hint(self, segment: str, previous_intent: CapabilityIntent | None) -> str:
        lowered = str(segment or "").strip().lower()
        if self._looks_like_time_query(segment):
            return "time_lookup"
        if any(token in lowered for token in ("天气", "weather")):
            return "weather_lookup"
        if any(token in lowered for token in ("地图", "where is", "在哪", "在哪里", "地址")):
            return "location_lookup"
        if "新闻" in segment or "news" in lowered:
            return "news_lookup"
        if any(token in lowered for token in ("解释", "是什么", "什么意思", "关系", "embedding", "dlss")):
            return "explanation"
        if any(token in lowered for token in ("查", "搜", "看看", "有没有")):
            return "generic_search"
        if previous_intent is not None:
            return previous_intent.capability
        return "generic_search"

    def _infer_segment_followup(self, segment: str, previous_intent: CapabilityIntent | None) -> tuple[str, str]:
        if previous_intent is None:
            return "", ""
        lowered = str(segment or "").strip().lower()
        previous_location = str(previous_intent.slots.get("location") or "").strip()
        previous_topic = str(previous_intent.slots.get("topic") or previous_intent.slots.get("query") or "").strip()
        if any(token in lowered for token in ("天气", "weather")) and previous_location:
            return "weather", previous_location
        if self._looks_like_time_query(segment) and previous_location:
            return "location", previous_location
        if any(token in lowered for token in ("地图", "map", "那里", "那边")) and previous_location:
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
