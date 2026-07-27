use std::fmt;
use std::io::{Read, Write};

use serde::{de::DeserializeOwned, Deserialize, Deserializer, Serialize, Serializer};
use thiserror::Error;
use zeroize::Zeroizing;

use crate::backend::{
    LocalOmniLaunch, RealtimeActivityProfile, RealtimeBackendKind, RealtimeCloudProviderKind,
    RealtimeInteractionIntensity, RealtimeVoiceOutput,
};

pub const MAX_CONTROL_FRAME_BYTES: usize = 256 * 1024;

/// A credential carried over the worker's stdin control channel.
///
/// It serializes as a plain JSON string (the wire shape the provider handshake
/// needs) but keeps its in-memory copy in [`Zeroizing`], so the plaintext key is
/// wiped from the host and worker heaps as soon as the owning frame is dropped
/// — the frame bytes themselves are already zeroized by [`write_frame`].
pub struct SecretString(Zeroizing<String>);

impl SecretString {
    /// Borrow the plaintext for the brief window it must be used (e.g. building
    /// the provider `Authorization` header or validating a start request).
    pub fn expose(&self) -> &str {
        self.0.as_str()
    }

    /// Consume into the owning [`Zeroizing`] handle without copying the plaintext.
    pub fn into_zeroizing(self) -> Zeroizing<String> {
        self.0
    }
}

impl From<String> for SecretString {
    fn from(value: String) -> Self {
        Self(Zeroizing::new(value))
    }
}

impl From<Zeroizing<String>> for SecretString {
    fn from(value: Zeroizing<String>) -> Self {
        Self(value)
    }
}

impl fmt::Debug for SecretString {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretString(***)")
    }
}

impl Serialize for SecretString {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(self.0.as_str())
    }
}

impl<'de> Deserialize<'de> for SecretString {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        String::deserialize(deserializer).map(Self::from)
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum HostCommand {
    Start {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        backend: RealtimeBackendKind,
        cloud_provider: Option<RealtimeCloudProviderKind>,
        cloud_credential: Option<SecretString>,
        local_omni: Option<Box<LocalOmniLaunch>>,
        persona_snapshot: SecretString,
        locale: String,
        activity_profile: RealtimeActivityProfile,
        interaction_intensity: RealtimeInteractionIntensity,
        voice_output: RealtimeVoiceOutput,
        source_id: Option<u64>,
        microphone_enabled: bool,
        screen_enabled: bool,
        application_audio_enabled: bool,
        online_assistance_enabled: bool,
    },
    Stop {
        session_id: String,
    },
    SetInput {
        session_id: String,
        microphone: bool,
        video: bool,
    },
    SetProfile {
        session_id: String,
        activity_profile: RealtimeActivityProfile,
        interaction_intensity: RealtimeInteractionIntensity,
    },
    AssistanceResult {
        session_id: String,
        request_id: String,
        public_summary: String,
        succeeded: bool,
    },
    Pause {
        session_id: String,
    },
    Resume {
        session_id: String,
    },
    UpdateUsage {
        session_id: String,
        audio_input_ms: u64,
        audio_output_ms: u64,
        video_frame_count: u64,
        interruption_count: u64,
    },
    ToolResult {
        session_id: String,
        call_id: String,
        public_summary: String,
        succeeded: bool,
    },
    Ping,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerEvent {
    Ready {
        protocol: String,
    },
    BackendState {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        backend: RealtimeBackendKind,
        status: String,
        error_code: Option<String>,
    },
    ModelLoadProgress {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        completed_bytes: u64,
        total_bytes: u64,
    },
    SessionState {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        status: String,
        backend: RealtimeBackendKind,
        cloud_provider: Option<RealtimeCloudProviderKind>,
        error_code: Option<String>,
    },
    PublicCaption {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        text: String,
        stable: bool,
        speaker: String,
    },
    Presence {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        state: String,
        level: Option<u8>,
    },
    BargeIn {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
    },
    PerceptionCandidate {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        public_summary: String,
    },
    AssistanceRequest {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        request_id: String,
        public_intent: String,
    },
    AssistanceState {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        request_id: String,
        status: String,
        error_code: Option<String>,
    },
    ToolRequest {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        call_id: String,
        tool_name: String,
        public_intent: String,
    },
    ResourcePressure {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        code: String,
    },
    ContextRotated {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        reason: String,
    },
    PrivacyPaused {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
    },
    Usage {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        audio_input_ms: u64,
        audio_output_ms: u64,
        video_frame_count: u64,
        interruption_count: u64,
        tool_call_count: u64,
    },
    Diagnostic {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        code: String,
    },
    Pong,
}

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error("realtime control I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("realtime control frame exceeds its size limit")]
    FrameTooLarge,
    #[error("realtime control frame is invalid: {0}")]
    Invalid(#[from] serde_json::Error),
}

pub fn read_frame<T: DeserializeOwned>(reader: &mut impl Read) -> Result<Option<T>, ProtocolError> {
    let mut length = [0_u8; 4];
    match reader.read_exact(&mut length) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(error) => return Err(error.into()),
    }
    let length = u32::from_le_bytes(length) as usize;
    if length == 0 || length > MAX_CONTROL_FRAME_BYTES {
        return Err(ProtocolError::FrameTooLarge);
    }
    // Zeroize the inbound frame bytes on drop, symmetric with write_frame: a
    // Start frame carries the plaintext credential, so the raw buffer must not
    // linger in freed heap after serde copies it into the SecretString.
    let mut payload = Zeroizing::new(vec![0_u8; length]);
    reader.read_exact(payload.as_mut_slice())?;
    Ok(Some(serde_json::from_slice(payload.as_slice())?))
}

pub fn write_frame<T: Serialize>(writer: &mut impl Write, value: &T) -> Result<(), ProtocolError> {
    let payload = Zeroizing::new(serde_json::to_vec(value)?);
    if payload.is_empty() || payload.len() > MAX_CONTROL_FRAME_BYTES {
        return Err(ProtocolError::FrameTooLarge);
    }
    writer.write_all(&(payload.len() as u32).to_le_bytes())?;
    writer.write_all(&payload)?;
    writer.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::{
        RealtimeActivityProfile, RealtimeBackendKind, RealtimeCloudProviderKind,
        RealtimeInteractionIntensity, RealtimeVoiceOutput,
    };

    #[test]
    fn length_prefixed_frames_round_trip_without_line_parsing() {
        let mut bytes = Vec::new();
        write_frame(&mut bytes, &WorkerEvent::Pong).expect("write frame");
        let decoded: serde_json::Value = read_frame(&mut bytes.as_slice())
            .expect("read frame")
            .expect("frame");
        assert_eq!(decoded, serde_json::json!({"type": "pong"}));
    }

    #[test]
    fn oversized_frames_are_rejected_before_allocation() {
        let mut bytes = ((MAX_CONTROL_FRAME_BYTES as u32) + 1)
            .to_le_bytes()
            .to_vec();
        bytes.extend_from_slice(b"{}");
        let result = read_frame::<serde_json::Value>(&mut bytes.as_slice());
        assert!(matches!(result, Err(ProtocolError::FrameTooLarge)));
    }

    #[test]
    fn governed_cloud_start_round_trips_and_redacts_control_secrets() {
        let command = HostCommand::Start {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            backend: RealtimeBackendKind::CloudLive,
            cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
            cloud_credential: Some(SecretString::from("credential-secret".to_owned())),
            local_omni: None,
            persona_snapshot: SecretString::from(valid_snapshot_json()),
            locale: "zh-CN".to_owned(),
            activity_profile: RealtimeActivityProfile::Auto,
            interaction_intensity: RealtimeInteractionIntensity::Standard,
            voice_output: RealtimeVoiceOutput::FairyVoice,
            source_id: Some(42),
            microphone_enabled: true,
            screen_enabled: true,
            application_audio_enabled: false,
            online_assistance_enabled: false,
        };

        let debug = format!("{command:?}");
        assert!(!debug.contains("credential-secret"));
        assert!(!debug.contains("persona-secret-marker"));
        assert!(debug.contains("***"));

        let mut bytes = Vec::new();
        write_frame(&mut bytes, &command).expect("write start");
        let decoded: HostCommand = read_frame(&mut bytes.as_slice())
            .expect("read start")
            .expect("start frame");
        assert!(matches!(
            decoded,
            HostCommand::Start {
                session_id,
                segment_id,
                context_epoch: 1,
                backend: RealtimeBackendKind::CloudLive,
                cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
                ..
            } if session_id == "session-1" && segment_id == "segment-1"
        ));
    }

    #[test]
    fn diagnostics_reject_unknown_content_fields() {
        let value = serde_json::json!({
            "type": "diagnostic",
            "session_id": "session-1",
            "segment_id": "segment-1",
            "context_epoch": 1,
            "code": "SAFE_CODE",
            "provider_payload": {"secret": true}
        });

        assert!(serde_json::from_value::<WorkerEvent>(value).is_err());
    }

    #[test]
    fn every_frozen_worker_event_round_trips() {
        let identity = || ("session-1".to_owned(), "segment-1".to_owned(), 1_u64);
        let mut events = vec![
            WorkerEvent::Ready {
                protocol: "fairy-realtime-worker-v2".to_owned(),
            },
            WorkerEvent::ModelLoadProgress {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                completed_bytes: 5,
                total_bytes: 10,
            },
            WorkerEvent::PublicCaption {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                sequence: 2,
                text: "hello".to_owned(),
                stable: true,
                speaker: "assistant".to_owned(),
            },
            WorkerEvent::Presence {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                state: "listening".to_owned(),
                level: Some(4),
            },
            WorkerEvent::BargeIn {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
            },
            WorkerEvent::PerceptionCandidate {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                sequence: 3,
                public_summary: "A menu is open".to_owned(),
            },
            WorkerEvent::AssistanceRequest {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                request_id: "request-1".to_owned(),
                public_intent: "Look up a public fact".to_owned(),
            },
            WorkerEvent::AssistanceState {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                request_id: "request-1".to_owned(),
                status: "completed".to_owned(),
                error_code: None,
            },
            WorkerEvent::ToolRequest {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                call_id: "call-1".to_owned(),
                tool_name: "observe".to_owned(),
                public_intent: "Observe a public state".to_owned(),
            },
            WorkerEvent::ResourcePressure {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                code: "GPU_BUDGET_LOW".to_owned(),
            },
            WorkerEvent::ContextRotated {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 2,
                reason: "privacy_resume".to_owned(),
            },
            WorkerEvent::PrivacyPaused {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 2,
            },
            WorkerEvent::Usage {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 2,
                audio_input_ms: 1,
                audio_output_ms: 2,
                video_frame_count: 3,
                interruption_count: 4,
                tool_call_count: 5,
            },
            WorkerEvent::Diagnostic {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 2,
                code: "SAFE_CODE".to_owned(),
            },
            WorkerEvent::Pong,
        ];
        for backend in [
            RealtimeBackendKind::CloudLive,
            RealtimeBackendKind::LocalMiniCpmO45,
        ] {
            events.push(WorkerEvent::BackendState {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                backend,
                status: "ready".to_owned(),
                error_code: None,
            });
            events.push(WorkerEvent::SessionState {
                session_id: identity().0,
                segment_id: identity().1,
                context_epoch: 1,
                status: "active".to_owned(),
                backend,
                cloud_provider: (backend == RealtimeBackendKind::CloudLive)
                    .then_some(RealtimeCloudProviderKind::GeminiLive),
                error_code: None,
            });
        }

        for event in events {
            let encoded = serde_json::to_value(&event).expect("serialize event");
            let decoded =
                serde_json::from_value::<WorkerEvent>(encoded).expect("deserialize event");
            assert_eq!(decoded, event);
        }
    }

    fn valid_snapshot_json() -> String {
        serde_json::json!({
            "schema_version": 1,
            "persona_digest": "a".repeat(64),
            "identity": {"name": "Fairy", "marker": "persona-secret-marker"}
        })
        .to_string()
    }
}
