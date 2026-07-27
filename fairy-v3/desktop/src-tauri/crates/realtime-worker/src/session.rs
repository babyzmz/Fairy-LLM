//! Worker-side validation for realtime start requests.
//!
//! The live worker binary (`main.rs`) drives [`crate::runtime::RealtimeRuntime`]
//! directly. This module is the single authority that rejects a malformed Start
//! request *before* any provider connection is dialed or any capture device is
//! opened, so an empty credential or an inconsistent capture scope fails fast
//! with a clear reason instead of surfacing as an opaque connection error.

use crate::backend::{
    validate_backend_start, BackendStartRequest, RealtimeActivityProfile, RealtimeBackendKind,
    RealtimeCloudProviderKind, RealtimeInteractionIntensity, RealtimeVoiceOutput,
};

pub use crate::backend::StartValidationError;

/// Validate the invariants a Start request must satisfy before the worker dials
/// a provider or opens any capture device:
///
/// - a non-empty session id and a non-blank credential,
/// - a known voice mode (`native` or `fairy`),
/// - screen capture and a selected window are enabled together, and
/// - game audio never escapes a selected-window scope.
pub fn validate_start(
    session_id: &str,
    voice_mode: &str,
    source_id: Option<u64>,
    screen_enabled: bool,
    game_audio_enabled: bool,
    credential: &str,
) -> Result<(), StartValidationError> {
    let voice_output = match voice_mode {
        "native" => RealtimeVoiceOutput::ProviderNativeVoice,
        "fairy" => RealtimeVoiceOutput::FairyVoice,
        _ => return Err(StartValidationError::Invalid),
    };
    validate_backend_start(&BackendStartRequest {
        session_id: session_id.to_owned(),
        segment_id: "legacy-cloud-segment".to_owned(),
        context_epoch: 1,
        backend: RealtimeBackendKind::CloudLive,
        cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
        cloud_credential_present: !credential.trim().is_empty(),
        persona_snapshot_present: true,
        activity_profile: RealtimeActivityProfile::Game,
        interaction_intensity: RealtimeInteractionIntensity::Standard,
        voice_output,
        source_id,
        microphone_enabled: true,
        screen_enabled,
        application_audio_enabled: game_audio_enabled,
        online_assistance_enabled: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_a_well_formed_request() {
        assert!(
            validate_start("session-1", "native", Some(42), true, false, "private-key").is_ok()
        );
        assert!(validate_start("session-1", "fairy", None, false, false, "private-key").is_ok());
    }

    #[test]
    fn rejects_empty_or_blank_credential() {
        assert_eq!(
            validate_start("session-1", "native", Some(42), true, false, ""),
            Err(StartValidationError::Invalid)
        );
        assert_eq!(
            validate_start("session-1", "native", Some(42), true, false, "   \t"),
            Err(StartValidationError::Invalid)
        );
    }

    #[test]
    fn rejects_empty_session_id() {
        assert_eq!(
            validate_start("", "native", Some(42), true, false, "key"),
            Err(StartValidationError::Invalid)
        );
    }

    #[test]
    fn rejects_unknown_voice_mode() {
        assert_eq!(
            validate_start("session-1", "whisper", Some(42), true, false, "key"),
            Err(StartValidationError::Invalid)
        );
    }

    #[test]
    fn screen_capture_and_selected_window_must_agree() {
        assert_eq!(
            validate_start("session-1", "native", None, true, false, "key"),
            Err(StartValidationError::Invalid)
        );
        assert_eq!(
            validate_start("session-1", "native", Some(1), false, false, "key"),
            Err(StartValidationError::Invalid)
        );
    }

    #[test]
    fn game_audio_cannot_escape_the_selected_window_scope() {
        assert_eq!(
            validate_start("session-1", "native", None, false, true, "key"),
            Err(StartValidationError::Invalid)
        );
    }
}
