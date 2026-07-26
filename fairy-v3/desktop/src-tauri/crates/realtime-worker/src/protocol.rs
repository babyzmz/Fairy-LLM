use std::fmt;
use std::io::{Read, Write};

use serde::{de::DeserializeOwned, Deserialize, Deserializer, Serialize, Serializer};
use thiserror::Error;
use zeroize::Zeroizing;

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

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderKind {
    GeminiLive,
    GlmRealtimeFlash,
    GlmRealtimeAir,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum HostCommand {
    Start {
        session_id: String,
        provider: ProviderKind,
        voice_mode: String,
        source_id: Option<u64>,
        screen_enabled: bool,
        game_audio_enabled: bool,
        credential: SecretString,
    },
    Stop {
        session_id: String,
    },
    SetInput {
        session_id: String,
        microphone: bool,
        video: bool,
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

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WorkerEvent {
    Ready {
        protocol: &'static str,
    },
    SessionState {
        session_id: String,
        status: &'static str,
        provider: Option<ProviderKind>,
        error_code: Option<&'static str>,
    },
    PublicCaption {
        session_id: String,
        text: String,
        stable: bool,
        speaker: &'static str,
    },
    Presence {
        session_id: String,
        state: &'static str,
        level: Option<u8>,
    },
    BargeIn {
        session_id: String,
    },
    ToolRequest {
        session_id: String,
        call_id: String,
        tool_name: String,
        public_intent: String,
    },
    Usage {
        session_id: String,
        audio_input_ms: u64,
        audio_output_ms: u64,
        video_frame_count: u64,
        interruption_count: u64,
        tool_call_count: u64,
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
}
