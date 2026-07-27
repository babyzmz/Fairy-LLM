use std::io::{Read, Write};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

pub const CONTROL_PROTOCOL_VERSION: u64 = 1;
pub const MAX_CONTROL_PAYLOAD_BYTES: usize = 256 * 1024;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ControlIdentity {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub sequence: u64,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum RuntimeCommand {
    Hello {
        #[serde(flatten)]
        identity: ControlIdentity,
        protocol_version: u64,
    },
    Load {
        #[serde(flatten)]
        identity: ControlIdentity,
        manifest_digest: String,
        model_version: String,
    },
    Stop {
        #[serde(flatten)]
        identity: ControlIdentity,
    },
}

#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum RuntimeEvent {
    Ready {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        protocol_version: u64,
        runtime_compatibility: String,
        build_profile: String,
        backend_ready: bool,
    },
    LoadProgress {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        stage: String,
        completed: u64,
        total: u64,
    },
    ModelReady {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        model_version: String,
        manifest_digest: String,
        backend_ready: bool,
        reason: String,
    },
    Decision {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        decision: String,
        text: String,
        confidence: f64,
        grounding: Vec<Value>,
        backend_ready: bool,
    },
    Diagnostic {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        code: String,
        severity: String,
    },
    Stopped {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        reason: String,
    },
    Pong {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        nonce: String,
    },
}

#[derive(Debug, Error)]
pub enum RuntimeProtocolError {
    #[error("the Omni control frame is truncated")]
    Truncated,
    #[error("the Omni control frame exceeds its bound")]
    Size,
    #[error("the Omni control frame contains invalid JSON")]
    InvalidJson,
    #[error("the Omni control stream failed: {0}")]
    Io(#[from] std::io::Error),
}

pub fn write_command(
    output: &mut impl Write,
    command: &RuntimeCommand,
) -> Result<(), RuntimeProtocolError> {
    let payload = serde_json::to_vec(command).map_err(|_| RuntimeProtocolError::InvalidJson)?;
    write_payload(output, &payload)
}

pub fn write_payload(output: &mut impl Write, payload: &[u8]) -> Result<(), RuntimeProtocolError> {
    if payload.is_empty() || payload.len() > MAX_CONTROL_PAYLOAD_BYTES {
        return Err(RuntimeProtocolError::Size);
    }
    let length = u32::try_from(payload.len()).map_err(|_| RuntimeProtocolError::Size)?;
    output.write_all(&length.to_le_bytes())?;
    output.write_all(payload)?;
    output.flush()?;
    Ok(())
}

pub fn read_event(input: &mut impl Read) -> Result<Option<RuntimeEvent>, RuntimeProtocolError> {
    let mut prefix = [0_u8; 4];
    let mut read = 0;
    while read < prefix.len() {
        let count = input.read(&mut prefix[read..])?;
        if count == 0 {
            return if read == 0 {
                Ok(None)
            } else {
                Err(RuntimeProtocolError::Truncated)
            };
        }
        read += count;
    }
    let length = u32::from_le_bytes(prefix) as usize;
    if length == 0 || length > MAX_CONTROL_PAYLOAD_BYTES {
        return Err(RuntimeProtocolError::Size);
    }
    let mut payload = vec![0_u8; length];
    input
        .read_exact(&mut payload)
        .map_err(|error| match error.kind() {
            std::io::ErrorKind::UnexpectedEof => RuntimeProtocolError::Truncated,
            _ => RuntimeProtocolError::Io(error),
        })?;
    serde_json::from_slice(&payload)
        .map(Some)
        .map_err(|_| RuntimeProtocolError::InvalidJson)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity() -> ControlIdentity {
        ControlIdentity {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            sequence: 1,
        }
    }

    #[test]
    fn commands_use_the_exact_little_endian_frame() {
        let mut bytes = Vec::new();
        write_command(
            &mut bytes,
            &RuntimeCommand::Hello {
                identity: identity(),
                protocol_version: CONTROL_PROTOCOL_VERSION,
            },
        )
        .expect("frame");
        let length = u32::from_le_bytes(bytes[..4].try_into().expect("prefix")) as usize;
        assert_eq!(length, bytes.len() - 4);
        let value: Value = serde_json::from_slice(&bytes[4..]).expect("json");
        assert_eq!(value["type"], "hello");
        assert_eq!(value["protocol_version"], CONTROL_PROTOCOL_VERSION);
    }

    #[test]
    fn event_decoder_is_strict_and_bounded() {
        let payload = br#"{"type":"ready","session_id":"s","segment_id":"g","context_epoch":1,"sequence":1,"protocol_version":1,"runtime_compatibility":"fairy-omni-runtime-v1","build_profile":"production-cuda","backend_ready":true}"#;
        let mut frame = Vec::new();
        write_payload(&mut frame, payload).expect("frame");
        assert!(matches!(
            read_event(&mut frame.as_slice()).expect("event"),
            Some(RuntimeEvent::Ready {
                backend_ready: true,
                ..
            })
        ));

        let unknown = br#"{"type":"stopped","session_id":"s","segment_id":"g","context_epoch":1,"sequence":2,"reason":"requested","extra":true}"#;
        let mut unknown_frame = Vec::new();
        write_payload(&mut unknown_frame, unknown).expect("frame");
        assert!(matches!(
            read_event(&mut unknown_frame.as_slice()),
            Err(RuntimeProtocolError::InvalidJson)
        ));

        let duplicate = br#"{"type":"stopped","session_id":"s","session_id":"other","segment_id":"g","context_epoch":1,"sequence":2,"reason":"requested"}"#;
        let mut duplicate_frame = Vec::new();
        write_payload(&mut duplicate_frame, duplicate).expect("frame");
        assert!(matches!(
            read_event(&mut duplicate_frame.as_slice()),
            Err(RuntimeProtocolError::InvalidJson)
        ));
    }

    #[test]
    fn invalid_lengths_and_truncation_fail_before_payload_use() {
        let oversized = ((MAX_CONTROL_PAYLOAD_BYTES + 1) as u32).to_le_bytes();
        assert!(matches!(
            read_event(&mut oversized.as_slice()),
            Err(RuntimeProtocolError::Size)
        ));
        assert!(matches!(
            read_event(&mut [4, 0, 0, 0, b'{'].as_slice()),
            Err(RuntimeProtocolError::Truncated)
        ));
    }
}
