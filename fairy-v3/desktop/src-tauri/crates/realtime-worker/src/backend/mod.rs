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

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeCandidateDecision {
    Listen,
    Speak,
    RequestAssistance,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RealtimeDialogueCandidate {
    pub decision: RealtimeCandidateDecision,
    pub activity: RealtimeActivityProfile,
    pub confidence: f64,
    pub intent: String,
    pub grounding: Vec<String>,
    pub text: String,
    pub urgency: f64,
    pub needs_online_assistance: bool,
    pub response_to_user: bool,
    pub stable: bool,
    pub persona_digest: String,
}

impl RealtimeDialogueCandidate {
    pub fn is_valid(&self) -> bool {
        let text_valid = self.text.chars().count() <= 2_000;
        let content_valid = match self.decision {
            RealtimeCandidateDecision::Listen => self.text.trim().is_empty(),
            RealtimeCandidateDecision::Speak | RealtimeCandidateDecision::RequestAssistance => {
                !self.text.trim().is_empty()
            }
        };
        self.confidence.is_finite()
            && (0.0..=1.0).contains(&self.confidence)
            && self.urgency.is_finite()
            && (0.0..=1.0).contains(&self.urgency)
            && valid_public_label(&self.intent)
            && self.grounding.len() <= 8
            && self
                .grounding
                .iter()
                .all(|item| !item.trim().is_empty() && item.chars().count() <= 240)
            && text_valid
            && content_valid
            && valid_digest(&self.persona_digest)
    }
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
    PerceptionCandidate(RealtimeDialogueCandidate),
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

fn valid_public_label(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[derive(Debug, Error)]
pub enum BackendError {
    #[error("the realtime cloud backend failed")]
    Cloud(#[from] crate::transport::ProviderTransportError),
    #[error("the local Omni backend is unavailable")]
    LocalUnavailable,
    #[error("the local Omni backend protocol failed")]
    LocalProtocol,
    #[error("the realtime dialogue candidate protocol failed")]
    DialogueProtocol,
    #[error("the selected application audio scope is unavailable for this backend")]
    ApplicationAudioScopeUnavailable,
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
            Self::DialogueProtocol => "REALTIME_BACKEND_PROTOCOL_FAILED",
            Self::ApplicationAudioScopeUnavailable => "APPLICATION_AUDIO_SCOPE_UNAVAILABLE",
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
    fn rotate_context(
        &mut self,
        next_context_epoch: u64,
        reason: &str,
        public_summary: &str,
    ) -> Result<(), BackendError>;
    fn set_activity_profile(
        &mut self,
        activity_profile: RealtimeActivityProfile,
    ) -> Result<(), BackendError>;
    fn poll(&mut self) -> Result<Vec<BackendEvent>, BackendError>;
    fn pause(&mut self) -> Result<(), BackendError>;
    fn resume(&mut self) -> Result<(), BackendError>;
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

    #[test]
    fn dialogue_candidate_fields_are_bounded_and_finite() {
        let mut candidate = RealtimeDialogueCandidate {
            decision: RealtimeCandidateDecision::Speak,
            activity: RealtimeActivityProfile::Game,
            confidence: 0.9,
            intent: "warn".to_owned(),
            grounding: vec!["current_window: health is low".to_owned()],
            text: "Move back.".to_owned(),
            urgency: 0.7,
            needs_online_assistance: false,
            response_to_user: false,
            stable: true,
            persona_digest: "a".repeat(64),
        };
        assert!(candidate.is_valid());
        candidate.confidence = f64::NAN;
        assert!(!candidate.is_valid());
        candidate.confidence = 0.9;
        candidate.grounding = vec!["x".repeat(241)];
        assert!(!candidate.is_valid());
        candidate.grounding.clear();
        candidate.intent = "Open URL".to_owned();
        assert!(!candidate.is_valid());
    }
}
