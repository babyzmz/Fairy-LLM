use fairy_realtime_worker::RealtimeActivityProfile;
use serde::{Deserialize, Serialize};

const GIB: u64 = 1024 * 1024 * 1024;
const MIN_DEDICATED_VRAM_BYTES: u64 = 12_000_000_000;
const MIN_POST_INSTALL_DISK: u64 = 5 * GIB;
const LOW_SYSTEM_MEMORY: u64 = 24 * GIB;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GpuVendor {
    Nvidia,
    Amd,
    Intel,
    Other,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalBetaReadinessReason {
    Eligible,
    ModelMissing,
    RuntimeMissing,
    UnsupportedOs,
    UnsupportedArchitecture,
    UnsupportedVendor,
    VramBelow12gb,
    Avx2Unavailable,
    CudaUnavailable,
    DriverIncompatible,
    AdapterMismatch,
    InsufficientFreeVram,
    InsufficientDisk,
    ModelVerificationFailed,
    SelfTestFailed,
    RuntimeQuarantined,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HardwareCapabilityFacts {
    pub windows_supported: Option<bool>,
    pub architecture_x64: Option<bool>,
    pub gpu_vendor: Option<GpuVendor>,
    pub dedicated_vram_bytes: Option<u64>,
    pub budget_bytes: Option<u64>,
    pub current_usage_bytes: Option<u64>,
    pub cuda_available: Option<bool>,
    pub driver_compatible: Option<bool>,
    pub adapter_luid_matches: Option<bool>,
    pub avx2_available: Option<bool>,
    pub system_total_bytes: Option<u64>,
    pub disk_available_bytes: Option<u64>,
    pub disk_required_bytes: Option<u64>,
    pub model_installed: Option<bool>,
    pub model_verified: Option<bool>,
    pub runtime_installed: Option<bool>,
    pub runtime_self_test_passed: Option<bool>,
    pub runtime_quarantined: Option<bool>,
    pub predicted_model_peak_bytes: Option<u64>,
    pub runtime_headroom_bytes: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HardwareCapabilityReport {
    pub schema_version: u16,
    pub static_eligible: bool,
    pub local_beta_eligible: bool,
    pub reason: LocalBetaReadinessReason,
    pub available_budget_bytes: Option<u64>,
    pub required_budget_bytes: Option<u64>,
    pub warnings: Vec<String>,
}

pub fn evaluate_local_beta_readiness(
    facts: &HardwareCapabilityFacts,
    _profile: RealtimeActivityProfile,
) -> HardwareCapabilityReport {
    let available_budget = facts
        .budget_bytes
        .zip(facts.current_usage_bytes)
        .and_then(|(budget, usage)| budget.checked_sub(usage));
    let required_budget = facts
        .predicted_model_peak_bytes
        .and_then(|peak| peak.checked_add(facts.runtime_headroom_bytes));

    let reason = if facts.windows_supported != Some(true) {
        LocalBetaReadinessReason::UnsupportedOs
    } else if facts.architecture_x64 != Some(true) {
        LocalBetaReadinessReason::UnsupportedArchitecture
    } else if facts.gpu_vendor != Some(GpuVendor::Nvidia) {
        LocalBetaReadinessReason::UnsupportedVendor
    } else if facts
        .dedicated_vram_bytes
        .is_none_or(|value| value < MIN_DEDICATED_VRAM_BYTES)
    {
        LocalBetaReadinessReason::VramBelow12gb
    } else if facts.avx2_available != Some(true) {
        LocalBetaReadinessReason::Avx2Unavailable
    } else if facts.cuda_available != Some(true) {
        LocalBetaReadinessReason::CudaUnavailable
    } else if facts.driver_compatible != Some(true) {
        LocalBetaReadinessReason::DriverIncompatible
    } else if facts.adapter_luid_matches != Some(true) {
        LocalBetaReadinessReason::AdapterMismatch
    } else if facts.model_installed != Some(true) {
        LocalBetaReadinessReason::ModelMissing
    } else if facts.model_verified != Some(true) || facts.predicted_model_peak_bytes.is_none() {
        LocalBetaReadinessReason::ModelVerificationFailed
    } else if facts.runtime_installed != Some(true) {
        LocalBetaReadinessReason::RuntimeMissing
    } else if facts.runtime_quarantined != Some(false) {
        LocalBetaReadinessReason::RuntimeQuarantined
    } else if facts.runtime_self_test_passed != Some(true) {
        LocalBetaReadinessReason::SelfTestFailed
    } else if facts
        .disk_available_bytes
        .zip(facts.disk_required_bytes)
        .and_then(|(available, required)| {
            required
                .checked_add(MIN_POST_INSTALL_DISK)
                .map(|minimum| available >= minimum)
        })
        != Some(true)
    {
        LocalBetaReadinessReason::InsufficientDisk
    } else if available_budget
        .zip(required_budget)
        .is_none_or(|(available, required)| available < required)
    {
        LocalBetaReadinessReason::InsufficientFreeVram
    } else {
        LocalBetaReadinessReason::Eligible
    };

    let static_eligible = facts.windows_supported == Some(true)
        && facts.architecture_x64 == Some(true)
        && facts.gpu_vendor == Some(GpuVendor::Nvidia)
        && facts
            .dedicated_vram_bytes
            .is_some_and(|value| value >= MIN_DEDICATED_VRAM_BYTES)
        && facts.avx2_available == Some(true)
        && facts.cuda_available == Some(true)
        && facts.driver_compatible == Some(true)
        && facts.adapter_luid_matches == Some(true);
    let warnings = if facts
        .system_total_bytes
        .is_some_and(|value| value < LOW_SYSTEM_MEMORY)
    {
        vec!["system_memory_below_24_gib".to_owned()]
    } else {
        Vec::new()
    };

    HardwareCapabilityReport {
        schema_version: 1,
        static_eligible,
        local_beta_eligible: reason == LocalBetaReadinessReason::Eligible,
        reason,
        available_budget_bytes: available_budget,
        required_budget_bytes: required_budget,
        warnings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fairy_realtime_worker::RealtimeActivityProfile;

    const MIB: u64 = 1024 * 1024;
    const GIB: u64 = 1024 * 1024 * 1024;

    fn eligible_facts() -> HardwareCapabilityFacts {
        HardwareCapabilityFacts {
            windows_supported: Some(true),
            architecture_x64: Some(true),
            gpu_vendor: Some(GpuVendor::Nvidia),
            dedicated_vram_bytes: Some(24 * GIB),
            budget_bytes: Some(20 * GIB),
            current_usage_bytes: Some(2 * GIB),
            cuda_available: Some(true),
            driver_compatible: Some(true),
            adapter_luid_matches: Some(true),
            avx2_available: Some(true),
            system_total_bytes: Some(32 * GIB),
            disk_available_bytes: Some(40 * GIB),
            disk_required_bytes: Some(20 * GIB),
            model_installed: Some(true),
            model_verified: Some(true),
            runtime_installed: Some(true),
            runtime_self_test_passed: Some(true),
            runtime_quarantined: Some(false),
            predicted_model_peak_bytes: Some(9 * GIB),
            runtime_headroom_bytes: 512 * MIB,
        }
    }

    #[test]
    fn eligible_facts_pass_the_focus_budget() {
        let report =
            evaluate_local_beta_readiness(&eligible_facts(), RealtimeActivityProfile::Focus);
        assert!(report.static_eligible);
        assert!(report.local_beta_eligible);
        assert_eq!(report.reason, LocalBetaReadinessReason::Eligible);
    }

    #[test]
    fn nominal_twelve_gb_is_in_the_supported_hardware_class() {
        let mut facts = eligible_facts();
        facts.dedicated_vram_bytes = Some(12_000_000_000);
        facts.budget_bytes = Some(12_000_000_000);
        facts.current_usage_bytes = Some(1_000_000_000);
        facts.predicted_model_peak_bytes = Some(9_500 * MIB);
        facts.runtime_headroom_bytes = 512 * MIB;
        let report = evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Focus);
        assert!(report.static_eligible);
        assert!(report.local_beta_eligible);
        assert_eq!(report.reason, LocalBetaReadinessReason::Eligible);
        assert_eq!(report.required_budget_bytes, Some(10_012 * MIB));
    }

    #[test]
    fn value_below_twelve_decimal_gb_fails_the_static_gate() {
        let mut facts = eligible_facts();
        facts.dedicated_vram_bytes = Some(11_999_999_999);
        let report = evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Focus);
        assert!(!report.static_eligible);
        assert_eq!(
            serde_json::to_string(&report.reason).expect("reason"),
            "\"vram_below12gb\""
        );
    }

    #[test]
    fn windows_reported_nominal_sixteen_gb_is_supported() {
        let mut facts = eligible_facts();
        facts.dedicated_vram_bytes = Some(16_829_644_800);
        let report = evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Focus);
        assert!(report.static_eligible);
        assert!(report.local_beta_eligible);
    }

    #[test]
    fn unknown_or_failed_requirements_fail_closed() {
        let cases = [
            (
                {
                    let mut facts = eligible_facts();
                    facts.windows_supported = None;
                    facts
                },
                LocalBetaReadinessReason::UnsupportedOs,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.gpu_vendor = Some(GpuVendor::Amd);
                    facts
                },
                LocalBetaReadinessReason::UnsupportedVendor,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.avx2_available = Some(false);
                    facts
                },
                LocalBetaReadinessReason::Avx2Unavailable,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.cuda_available = Some(false);
                    facts
                },
                LocalBetaReadinessReason::CudaUnavailable,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.adapter_luid_matches = Some(false);
                    facts
                },
                LocalBetaReadinessReason::AdapterMismatch,
            ),
        ];
        for (facts, reason) in cases {
            assert_eq!(
                evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Auto).reason,
                reason
            );
        }
    }

    #[test]
    fn artifact_and_resource_failures_have_specific_reasons() {
        let cases = [
            (
                {
                    let mut facts = eligible_facts();
                    facts.model_installed = Some(false);
                    facts
                },
                LocalBetaReadinessReason::ModelMissing,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.model_verified = Some(false);
                    facts
                },
                LocalBetaReadinessReason::ModelVerificationFailed,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.runtime_installed = Some(false);
                    facts
                },
                LocalBetaReadinessReason::RuntimeMissing,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.runtime_self_test_passed = Some(false);
                    facts
                },
                LocalBetaReadinessReason::SelfTestFailed,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.runtime_quarantined = Some(true);
                    facts
                },
                LocalBetaReadinessReason::RuntimeQuarantined,
            ),
            (
                {
                    let mut facts = eligible_facts();
                    facts.disk_available_bytes = Some(1);
                    facts
                },
                LocalBetaReadinessReason::InsufficientDisk,
            ),
        ];
        for (facts, reason) in cases {
            let report = evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Auto);
            assert_eq!(report.reason, reason);
            assert!(
                report.static_eligible,
                "artifact/runtime state must not erase hardware eligibility"
            );
        }
    }

    #[test]
    fn activity_profiles_do_not_duplicate_live_vram_reserves() {
        let facts = eligible_facts();
        let required = [
            RealtimeActivityProfile::Focus,
            RealtimeActivityProfile::Auto,
            RealtimeActivityProfile::Game,
        ]
        .map(|profile| evaluate_local_beta_readiness(&facts, profile).required_budget_bytes);
        assert_eq!(required, [required[0]; 3]);
    }

    #[test]
    fn supported_adapter_can_be_temporarily_short_of_live_vram() {
        let mut facts = eligible_facts();
        facts.dedicated_vram_bytes = Some(12_000_000_000);
        facts.predicted_model_peak_bytes = Some(9_500 * MIB);
        facts.runtime_headroom_bytes = 512 * MIB;
        facts.budget_bytes = Some(11_000 * MIB);
        facts.current_usage_bytes = Some(989 * MIB);
        let report = evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Game);
        assert!(report.static_eligible);
        assert!(!report.local_beta_eligible);
        assert_eq!(
            report.reason,
            LocalBetaReadinessReason::InsufficientFreeVram
        );
        assert_eq!(report.available_budget_bytes, Some(10_011 * MIB));
        assert_eq!(report.required_budget_bytes, Some(10_012 * MIB));
    }
}
