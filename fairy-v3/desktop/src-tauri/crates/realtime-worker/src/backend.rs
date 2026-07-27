use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeBackendKind {
    LocalMiniCpmO45,
    CloudLive,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeCloudProviderKind {
    GeminiLive,
    GlmRealtimeFlash,
    GlmRealtimeAir,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeActivityProfile {
    Auto,
    Game,
    Focus,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeInteractionIntensity {
    Quiet,
    Standard,
    Active,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeVoiceOutput {
    FairyVoice,
    ProviderNativeVoice,
    TextOnly,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BackendStartRequest {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub backend: RealtimeBackendKind,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub cloud_credential_present: bool,
    pub persona_snapshot_present: bool,
    pub activity_profile: RealtimeActivityProfile,
    pub interaction_intensity: RealtimeInteractionIntensity,
    pub voice_output: RealtimeVoiceOutput,
    pub source_id: Option<u64>,
    pub microphone_enabled: bool,
    pub screen_enabled: bool,
    pub application_audio_enabled: bool,
    pub online_assistance_enabled: bool,
}

#[derive(Clone, Copy, Debug, Error, Eq, PartialEq)]
pub enum StartValidationError {
    #[error("the realtime start request is invalid")]
    Invalid,
}

pub fn validate_backend_start(request: &BackendStartRequest) -> Result<(), StartValidationError> {
    let mut valid = !request.session_id.trim().is_empty()
        && !request.segment_id.trim().is_empty()
        && request.context_epoch > 0
        && request.persona_snapshot_present
        && request.screen_enabled == request.source_id.is_some()
        && (request.screen_enabled || !request.application_audio_enabled);

    match request.backend {
        RealtimeBackendKind::LocalMiniCpmO45 => {
            valid &= request.cloud_provider.is_none();
            valid &= !request.cloud_credential_present;
            valid &= request.voice_output != RealtimeVoiceOutput::ProviderNativeVoice;
        }
        RealtimeBackendKind::CloudLive => {
            valid &= request.cloud_provider.is_some();
            valid &= request.cloud_credential_present;
        }
    }

    if valid {
        Ok(())
    } else {
        Err(StartValidationError::Invalid)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn local_request() -> BackendStartRequest {
        BackendStartRequest {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            backend: RealtimeBackendKind::LocalMiniCpmO45,
            cloud_provider: None,
            cloud_credential_present: false,
            persona_snapshot_present: true,
            activity_profile: RealtimeActivityProfile::Auto,
            interaction_intensity: RealtimeInteractionIntensity::Standard,
            voice_output: RealtimeVoiceOutput::FairyVoice,
            source_id: Some(42),
            microphone_enabled: true,
            screen_enabled: true,
            application_audio_enabled: false,
            online_assistance_enabled: false,
        }
    }

    #[test]
    fn local_backend_does_not_require_a_cloud_credential() {
        assert!(validate_backend_start(&local_request()).is_ok());
    }

    #[test]
    fn local_backend_rejects_cloud_fields_and_provider_native_voice() {
        let mut request = local_request();
        request.cloud_provider = Some(RealtimeCloudProviderKind::GeminiLive);
        assert!(validate_backend_start(&request).is_err());

        let mut request = local_request();
        request.cloud_credential_present = true;
        assert!(validate_backend_start(&request).is_err());

        let mut request = local_request();
        request.voice_output = RealtimeVoiceOutput::ProviderNativeVoice;
        assert!(validate_backend_start(&request).is_err());
    }

    #[test]
    fn cloud_backend_requires_provider_and_credential() {
        let mut request = local_request();
        request.backend = RealtimeBackendKind::CloudLive;
        assert!(validate_backend_start(&request).is_err());

        request.cloud_provider = Some(RealtimeCloudProviderKind::GlmRealtimeFlash);
        assert!(validate_backend_start(&request).is_err());

        request.cloud_credential_present = true;
        assert!(validate_backend_start(&request).is_ok());
    }

    #[test]
    fn selected_window_scope_is_required_for_visual_and_application_audio() {
        let mut request = local_request();
        request.source_id = None;
        assert!(validate_backend_start(&request).is_err());

        request.screen_enabled = false;
        request.application_audio_enabled = true;
        assert!(validate_backend_start(&request).is_err());

        request.source_id = Some(42);
        assert!(validate_backend_start(&request).is_err());

        request.screen_enabled = true;
        assert!(validate_backend_start(&request).is_ok());
    }

    #[test]
    fn identity_and_persona_are_required() {
        let mut request = local_request();
        request.session_id.clear();
        assert!(validate_backend_start(&request).is_err());

        let mut request = local_request();
        request.segment_id = " \t".to_owned();
        assert!(validate_backend_start(&request).is_err());

        let mut request = local_request();
        request.context_epoch = 0;
        assert!(validate_backend_start(&request).is_err());

        let mut request = local_request();
        request.persona_snapshot_present = false;
        assert!(validate_backend_start(&request).is_err());
    }
}
