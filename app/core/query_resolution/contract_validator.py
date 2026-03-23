from __future__ import annotations

from .capability_contracts import CapabilityContractRegistry


class ContractValidator:
    _ACTIVE_RUNTIME_CAPABILITIES = {
        "weather_lookup",
        "time_lookup",
        "location_lookup",
        "display_information",
        "news_lookup",
        "generic_search",
        "explanation",
        "system_action",
    }

    def validate_registry(self, registry: CapabilityContractRegistry) -> list[str]:
        issues: list[str] = []
        for capability, contract in registry.all().items():
            if not contract.required_slots:
                issues.append(f"{capability}: missing required_slots")
            if not contract.clarification_messages:
                issues.append(f"{capability}: missing clarification_messages")
            if contract.default_slot_sources is None:
                issues.append(f"{capability}: missing default_slot_sources")
            if not contract.slot_normalizers:
                issues.append(f"{capability}: missing slot_normalizers")
            if not contract.slot_validators:
                issues.append(f"{capability}: missing slot_validators")
            if not contract.carryover_safe_slots:
                issues.append(f"{capability}: missing carryover_safe_slots")
            if contract.supports_followup and not contract.carryover_sources:
                issues.append(f"{capability}: supports_followup=true but missing carryover_sources")
            for slot in contract.required_slots:
                if slot not in contract.clarification_messages:
                    issues.append(f"{capability}: missing clarification message for required slot '{slot}'")
                if slot not in contract.slot_validators:
                    issues.append(f"{capability}: missing validator for required slot '{slot}'")
            for slot, safe in contract.carryover_safe_slots.items():
                if safe and slot not in contract.carryover_sources:
                    issues.append(f"{capability}: carryover-safe slot '{slot}' missing carryover_sources")
        return issues

    def coverage_report(self, registry: CapabilityContractRegistry) -> dict[str, str]:
        report: dict[str, str] = {}
        contracts = registry.all()
        for name in self._ACTIVE_RUNTIME_CAPABILITIES:
            if name not in contracts:
                report[name] = "missing"
                continue
            issues = [
                issue
                for issue in self.validate_registry(registry)
                if issue.startswith(f"{name}:")
            ]
            report[name] = "partially_integrated" if issues else "registered"
        for name in contracts:
            report.setdefault(name, "registered")
        return report
