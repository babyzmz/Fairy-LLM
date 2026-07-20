use std::io::{Read, Write};

use serde::{de::DeserializeOwned, Deserialize, Serialize};
use thiserror::Error;
use zeroize::Zeroizing;

pub const MAX_CONTROL_FRAME_BYTES: usize = 256 * 1024;

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
        credential: String,
    },
    Stop {
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
    let mut payload = vec![0_u8; length];
    reader.read_exact(&mut payload)?;
    Ok(Some(serde_json::from_slice(&payload)?))
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
