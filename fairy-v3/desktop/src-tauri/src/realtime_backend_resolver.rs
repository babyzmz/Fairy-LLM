use fairy_realtime_worker::{
    RealtimeActivityProfile, RealtimeBackendKind, RealtimeCloudProviderKind, RealtimeVoiceOutput,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::desktop_preferences::{
    DesktopPreferences, RealtimeBackendPreference, RealtimeCloudProviderPreference,
};
use crate::hardware_capabilities::LocalBetaReadinessReason;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RealtimeBackendResolutionInput {
    pub activity_profile: RealtimeActivityProfile,
    pub voice_output: RealtimeVoiceOutput,
    pub cloud_microphone_upload_consent: bool,
    pub cloud_screen_upload_consent: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeBackendResolutionFacts {
    pub local_ready: bool,
    pub local_reason: LocalBetaReadinessReason,
    pub cloud_credential_ready: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeBackendResolution {
    pub schema_version: u16,
    pub resolution_token: String,
    pub available: bool,
    pub backend: Option<RealtimeBackendKind>,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub reason: Option<String>,
    pub requires_cloud_upload_consent: bool,
    pub preference_revision: u64,
}

#[derive(Serialize)]
struct ResolutionTokenPayload<'a> {
    preference_revision: u64,
    preference: RealtimeBackendPreference,
    cloud_provider: RealtimeCloudProviderKind,
    allow_cloud_fallback: bool,
    input: RealtimeBackendResolutionInput,
    facts: RealtimeBackendResolutionFacts,
    outcome: &'a ResolutionOutcome,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
struct ResolutionOutcome {
    available: bool,
    backend: Option<RealtimeBackendKind>,
    cloud_provider: Option<RealtimeCloudProviderKind>,
    reason: Option<&'static str>,
    requires_cloud_upload_consent: bool,
}

pub fn resolve_realtime_backend(
    preferences: &DesktopPreferences,
    input: RealtimeBackendResolutionInput,
    facts: RealtimeBackendResolutionFacts,
) -> RealtimeBackendResolution {
    let cloud_provider = map_cloud_provider(preferences.realtime_cloud_provider);
    let local_voice_compatible = input.voice_output != RealtimeVoiceOutput::ProviderNativeVoice;
    let upload_consented =
        input.cloud_microphone_upload_consent && input.cloud_screen_upload_consent;

    let outcome = if !preferences.realtime_beta_enabled {
        unavailable("REALTIME_BETA_DISABLED", false)
    } else {
        match preferences.realtime_backend {
            RealtimeBackendPreference::LocalMiniCpmO45 => {
                if !local_voice_compatible {
                    unavailable("LOCAL_VOICE_OUTPUT_INCOMPATIBLE", false)
                } else if facts.local_ready {
                    available(RealtimeBackendKind::LocalMiniCpmO45, None, false)
                } else {
                    unavailable(local_reason_code(facts.local_reason), false)
                }
            }
            RealtimeBackendPreference::CloudLive => resolve_cloud(
                cloud_provider,
                facts.cloud_credential_ready,
                upload_consented,
            ),
            RealtimeBackendPreference::Auto => {
                if local_voice_compatible && facts.local_ready {
                    available(RealtimeBackendKind::LocalMiniCpmO45, None, false)
                } else if !preferences.realtime_allow_cloud_fallback {
                    unavailable("AUTO_CLOUD_FALLBACK_DISABLED", false)
                } else {
                    resolve_cloud(
                        cloud_provider,
                        facts.cloud_credential_ready,
                        upload_consented,
                    )
                }
            }
        }
    };

    let payload = ResolutionTokenPayload {
        preference_revision: preferences.revision,
        preference: preferences.realtime_backend,
        cloud_provider,
        allow_cloud_fallback: preferences.realtime_allow_cloud_fallback,
        input,
        facts,
        outcome: &outcome,
    };
    let token = serde_json::to_vec(&payload)
        .map(|bytes| format!("{:x}", Sha256::digest(bytes)))
        .unwrap_or_default();
    RealtimeBackendResolution {
        schema_version: 1,
        resolution_token: token,
        available: outcome.available,
        backend: outcome.backend,
        cloud_provider: outcome.cloud_provider,
        reason: outcome.reason.map(str::to_owned),
        requires_cloud_upload_consent: outcome.requires_cloud_upload_consent,
        preference_revision: preferences.revision,
    }
}

fn resolve_cloud(
    provider: RealtimeCloudProviderKind,
    credential_ready: bool,
    upload_consented: bool,
) -> ResolutionOutcome {
    if !credential_ready {
        unavailable("REALTIME_CREDENTIAL_MISSING", true)
    } else if !upload_consented {
        unavailable("CLOUD_UPLOAD_CONSENT_REQUIRED", true)
    } else {
        available(RealtimeBackendKind::CloudLive, Some(provider), true)
    }
}

fn available(
    backend: RealtimeBackendKind,
    cloud_provider: Option<RealtimeCloudProviderKind>,
    requires_cloud_upload_consent: bool,
) -> ResolutionOutcome {
    ResolutionOutcome {
        available: true,
        backend: Some(backend),
        cloud_provider,
        reason: None,
        requires_cloud_upload_consent,
    }
}

fn unavailable(reason: &'static str, cloud_consent: bool) -> ResolutionOutcome {
    ResolutionOutcome {
        available: false,
        backend: None,
        cloud_provider: None,
        reason: Some(reason),
        requires_cloud_upload_consent: cloud_consent,
    }
}

pub const fn map_cloud_provider(
    provider: RealtimeCloudProviderPreference,
) -> RealtimeCloudProviderKind {
    match provider {
        RealtimeCloudProviderPreference::GeminiLive => RealtimeCloudProviderKind::GeminiLive,
        RealtimeCloudProviderPreference::GlmRealtimeFlash => {
            RealtimeCloudProviderKind::GlmRealtimeFlash
        }
        RealtimeCloudProviderPreference::GlmRealtimeAir => {
            RealtimeCloudProviderKind::GlmRealtimeAir
        }
    }
}

const fn local_reason_code(reason: LocalBetaReadinessReason) -> &'static str {
    match reason {
        LocalBetaReadinessReason::Eligible => "LOCAL_BACKEND_NOT_READY",
        LocalBetaReadinessReason::ModelMissing => "LOCAL_MODEL_MISSING",
        LocalBetaReadinessReason::RuntimeMissing => "LOCAL_RUNTIME_MISSING",
        LocalBetaReadinessReason::UnsupportedOs => "LOCAL_OS_UNSUPPORTED",
        LocalBetaReadinessReason::UnsupportedArchitecture => "LOCAL_ARCHITECTURE_UNSUPPORTED",
        LocalBetaReadinessReason::UnsupportedVendor => "LOCAL_GPU_UNSUPPORTED",
        LocalBetaReadinessReason::VramBelow16gb => "LOCAL_VRAM_INSUFFICIENT",
        LocalBetaReadinessReason::Avx2Unavailable => "LOCAL_AVX2_UNAVAILABLE",
        LocalBetaReadinessReason::CudaUnavailable => "LOCAL_CUDA_UNAVAILABLE",
        LocalBetaReadinessReason::DriverIncompatible => "LOCAL_DRIVER_INCOMPATIBLE",
        LocalBetaReadinessReason::AdapterMismatch => "LOCAL_ADAPTER_MISMATCH",
        LocalBetaReadinessReason::InsufficientFreeVram => "LOCAL_FREE_VRAM_INSUFFICIENT",
        LocalBetaReadinessReason::InsufficientDisk => "LOCAL_DISK_INSUFFICIENT",
        LocalBetaReadinessReason::ModelVerificationFailed => "LOCAL_MODEL_UNVERIFIED",
        LocalBetaReadinessReason::SelfTestFailed => "LOCAL_SELF_TEST_FAILED",
        LocalBetaReadinessReason::RuntimeQuarantined => "LOCAL_RUNTIME_QUARANTINED",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn preferences(preference: RealtimeBackendPreference) -> DesktopPreferences {
        DesktopPreferences {
            revision: 7,
            realtime_beta_enabled: true,
            realtime_backend: preference,
            ..DesktopPreferences::default()
        }
    }

    fn input() -> RealtimeBackendResolutionInput {
        RealtimeBackendResolutionInput {
            activity_profile: RealtimeActivityProfile::Auto,
            voice_output: RealtimeVoiceOutput::FairyVoice,
            cloud_microphone_upload_consent: true,
            cloud_screen_upload_consent: true,
        }
    }

    fn facts(local_ready: bool, cloud_ready: bool) -> RealtimeBackendResolutionFacts {
        RealtimeBackendResolutionFacts {
            local_ready,
            local_reason: if local_ready {
                LocalBetaReadinessReason::Eligible
            } else {
                LocalBetaReadinessReason::ModelMissing
            },
            cloud_credential_ready: cloud_ready,
        }
    }

    #[test]
    fn auto_prefers_local_without_cloud_consent_or_credentials() {
        let mut request = input();
        request.cloud_microphone_upload_consent = false;
        request.cloud_screen_upload_consent = false;
        let resolved = resolve_realtime_backend(
            &preferences(RealtimeBackendPreference::Auto),
            request,
            facts(true, false),
        );
        assert!(resolved.available);
        assert_eq!(resolved.backend, Some(RealtimeBackendKind::LocalMiniCpmO45));
        assert_eq!(resolved.cloud_provider, None);
    }

    #[test]
    fn auto_never_uses_cloud_without_policy_credential_and_session_consent() {
        let mut settings = preferences(RealtimeBackendPreference::Auto);
        let blocked = resolve_realtime_backend(&settings, input(), facts(false, true));
        assert_eq!(
            blocked.reason.as_deref(),
            Some("AUTO_CLOUD_FALLBACK_DISABLED")
        );

        settings.realtime_allow_cloud_fallback = true;
        let missing_key = resolve_realtime_backend(&settings, input(), facts(false, false));
        assert_eq!(
            missing_key.reason.as_deref(),
            Some("REALTIME_CREDENTIAL_MISSING")
        );

        let mut no_upload = input();
        no_upload.cloud_screen_upload_consent = false;
        let no_upload = resolve_realtime_backend(&settings, no_upload, facts(false, true));
        assert_eq!(
            no_upload.reason.as_deref(),
            Some("CLOUD_UPLOAD_CONSENT_REQUIRED")
        );
    }

    #[test]
    fn explicit_local_rejects_provider_native_voice() {
        let mut request = input();
        request.voice_output = RealtimeVoiceOutput::ProviderNativeVoice;
        let resolved = resolve_realtime_backend(
            &preferences(RealtimeBackendPreference::LocalMiniCpmO45),
            request,
            facts(true, true),
        );
        assert_eq!(
            resolved.reason.as_deref(),
            Some("LOCAL_VOICE_OUTPUT_INCOMPATIBLE")
        );
    }

    #[test]
    fn resolution_token_changes_with_authoritative_inputs() {
        let settings = preferences(RealtimeBackendPreference::CloudLive);
        let first = resolve_realtime_backend(&settings, input(), facts(false, true));
        let mut next = input();
        next.cloud_screen_upload_consent = false;
        let second = resolve_realtime_backend(&settings, next, facts(false, true));
        assert_ne!(first.resolution_token, second.resolution_token);
    }
}
