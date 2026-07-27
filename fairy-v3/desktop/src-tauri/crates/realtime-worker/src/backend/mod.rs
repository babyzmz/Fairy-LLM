use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

mod cloud;
mod local_omni;

pub use cloud::{CloudBackendLaunch, CloudLiveBackend};
pub use local_omni::LocalOmniBackend;

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
pub struct LocalOmniLaunch {
    pub runtime_path: PathBuf,
    pub manifest_path: PathBuf,
    pub model_root: PathBuf,
    pub manifest_digest: String,
    pub model_version: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BackendCaptionSpeaker {
    User,
    Assistant,
}

#[derive(Clone, Debug, PartialEq)]
pub enum BackendEvent {
    Ready,
    PerceptionCandidate {
        public_summary: String,
    },
    Audio(Vec<u8>),
    PublicCaption {
        text: String,
        stable: bool,
        speaker: BackendCaptionSpeaker,
    },
    SpeechStarted,
    SpeechStopped,
    ToolCall {
        call_id: String,
        name: String,
        arguments: Value,
    },
    Usage(Value),
    GoAway,
}

#[derive(Debug, Error)]
pub enum BackendError {
    #[error("the realtime cloud backend failed")]
    Cloud(#[from] crate::transport::ProviderTransportError),
    #[error("the local Omni backend is unavailable")]
    LocalUnavailable,
    #[error("the local Omni backend protocol failed")]
    LocalProtocol,
    #[error("the local Omni backend operation timed out")]
    LocalTimeout,
    #[error("the local Omni backend process failed")]
    LocalIo(#[from] std::io::Error),
}

impl BackendError {
    pub const fn public_code(&self) -> &'static str {
        match self {
            Self::Cloud(error) => error.public_code(),
            Self::LocalUnavailable => "LOCAL_BACKEND_NOT_READY",
            Self::LocalProtocol => "LOCAL_BACKEND_PROTOCOL_FAILED",
            Self::LocalTimeout => "LOCAL_BACKEND_TIMEOUT",
            Self::LocalIo(_) => "LOCAL_BACKEND_INTERRUPTED",
        }
    }
}

pub trait RealtimeBackend: Send {
    fn push_microphone(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError>;
    fn push_application_audio(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError>;
    fn push_video(&mut self, jpeg: &[u8]) -> Result<(), BackendError>;
    fn push_text(&mut self, text: &str) -> Result<(), BackendError>;
    fn push_assistance_result(
        &mut self,
        call_id: &str,
        public_summary: &str,
    ) -> Result<(), BackendError>;
    fn poll(&mut self) -> Result<Vec<BackendEvent>, BackendError>;
    fn pause(&mut self) -> Result<(), BackendError>;
    fn stop(&mut self) -> Result<(), BackendError>;
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
