"""Opt-in live classifier probe, launched by the desktop secure credential bridge.

Never prints credentials, messages, response bodies, reasoning, or exception text.
Uses one request per invocation; no fallback, retries, tools, or database writes.
"""

import json
import os
from datetime import UTC, datetime

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.routing import (
    build_manual_evidence_request,
    build_router_request,
    parse_evidence_classification,
    parse_router_output,
)
from fairy_core.model_catalog.models import ModelSelectionMode, ModelSelectionSnapshot
from fairy_core.providers import CancellationToken, ModelDeltaKind, ProviderCapability

from fairy_capabilities.composition import build_provider_registry


def main() -> int:
    model_id = os.environ["FAIRY_PROVIDER_PROBE_MODEL"]
    mode = os.environ.get("FAIRY_PROVIDER_PROBE_MODE", "manual")
    registry = build_provider_registry()
    report = {
        "model": model_id,
        "http_status": None,
        "frames": 0,
        "finish": [],
        "usage": {},
    }
    try:
        profile = registry.profile_for_model(model_id)
        # Instrument this adapter only. No normal Core service or user database is opened.
        provider = registry._require_provider(profile.id)

        def inspect_response(response):
            report["http_status"] = response.status_code
            if response.status_code >= 400:
                try:
                    message = str(response.read().decode("utf-8")).lower()
                    report["rejection_hints"] = [
                        label
                        for phrase, label in (
                            ("data policy", "data_policy"),
                            ("privacy", "privacy"),
                            ("structured", "structured_output"),
                            ("parameters", "parameters"),
                            ("no endpoints", "no_endpoints"),
                            ("not a valid model", "invalid_model"),
                            ("not found", "not_found"),
                        )
                        if phrase in message
                    ]
                except (ValueError, UnicodeError):
                    report["rejection_hints"] = []
            original = response.iter_lines

            def lines():
                for line in original():
                    if line.startswith("data:") and line[5:].strip() != "[DONE]":
                        try:
                            frame = json.loads(line[5:])
                            report["frames"] += 1
                            for choice in frame.get("choices", []):
                                reason = choice.get("finish_reason")
                                if reason in {
                                    "stop",
                                    "length",
                                    "tool_calls",
                                    "content_filter",
                                    "error",
                                }:
                                    report["finish"].append(reason)
                            usage = frame.get("usage") or {}
                            for key in (
                                "prompt_tokens",
                                "completion_tokens",
                                "total_tokens",
                            ):
                                if isinstance(usage.get(key), int):
                                    report["usage"][key] = usage[key]
                        except (ValueError, TypeError, AttributeError):
                            report["malformed_frame"] = True
                    yield line

            response.iter_lines = lines

        provider._client.event_hooks["response"].append(inspect_response)
        selection = ModelSelectionSnapshot(
            mode=ModelSelectionMode(mode),
            model_id=model_id if mode == "manual" else None,
            allow_free_fallback=False,
            zero_data_retention=False,
            revision=0,
            captured_at=datetime.now(UTC),
        )
        common = dict(
            profile_id=profile.id,
            user_request="Please only say hello. Do not call tools or read or write any files.",
            selection=selection,
        )
        structured = ProviderCapability.STRUCTURED_OUTPUT in profile.capabilities
        request = (
            build_router_request(**common, attachment_count=0, fallback_profile_ids=())
            if mode == "auto"
            else build_manual_evidence_request(**common, use_structured_output=structured)
        )
        report["mode"] = mode
        report["contract"] = "structured" if mode == "auto" or structured else "tool"
        chunks = []
        candidates = {}
        count = 0
        for _delta in provider.stream(request, CancellationToken()):
            count += 1
            if _delta.text:
                chunks.append(_delta.text)
            if _delta.kind == ModelDeltaKind.TOOL_CALL and _delta.tool_call_id:
                candidate = candidates.setdefault(
                    _delta.tool_call_id, ToolCandidate(call_id=_delta.tool_call_id)
                )
                candidate.append(_delta)
        parser = parse_router_output if mode == "auto" else parse_evidence_classification
        if mode == "auto" or structured:
            parser("".join(chunks))
        else:
            assert len(candidates) == 1
            candidate = next(iter(candidates.values()))
            assert candidate.name == "evidence.classify"
            parser(json.dumps(candidate.raw_arguments()))
        report.update(result="success", delta_count=count)
    except Exception as error:
        # Adapter exception messages may change; expose only allowlisted static reasons.
        reasons = {
            "provider completed without a usable response": "empty_response",
            "provider stopped before producing a complete response": "incomplete_response",
            "provider stream ended before completion": "missing_completion",
            "provider frame choices must be a list": "choices_shape",
            "provider request failed (PROVIDER_REQUEST_REJECTED)": "request_rejected",
        }
        report.update(
            result="failed",
            error_type=type(error).__name__,
            reason=reasons.get(str(error), "other_sanitized_error"),
        )
    finally:
        registry.close()
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["result"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
