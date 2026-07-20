use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use serde_json::{json, Value};
use thiserror::Error;

const MAX_AUDIO_CHUNK_BYTES: usize = 64 * 1024;
const MAX_VIDEO_FRAME_BYTES: usize = 2 * 1024 * 1024;
const MAX_PUBLIC_TEXT_CHARS: usize = 2_000;
const MAX_TEXT_INPUT_CHARS: usize = 4_000;

#[derive(Clone, Debug, PartialEq)]
pub enum ProviderOutput {
    Ready,
    Audio(Vec<u8>),
    PublicCaption {
        text: String,
        stable: bool,
        speaker: CaptionSpeaker,
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CaptionSpeaker {
    User,
    Assistant,
}

#[derive(Debug, Error)]
pub enum ProviderProtocolError {
    #[error("provider media payload exceeds its limit")]
    MediaTooLarge,
    #[error("provider event is invalid")]
    InvalidEvent,
    #[error("provider text input is invalid")]
    InvalidText,
    #[error("provider payload is not valid base64")]
    InvalidBase64,
}

pub trait RealtimeProtocol {
    fn setup(&self) -> Value;
    fn text(&self, text: &str) -> Result<Vec<Value>, ProviderProtocolError>;
    fn audio(&self, pcm16: &[u8]) -> Result<Value, ProviderProtocolError>;
    fn video(&self, jpeg: &[u8]) -> Result<Value, ProviderProtocolError>;
    fn tool_result(&self, call_id: &str, output: &str) -> Value;
    fn parse(&self, event: &Value) -> Result<Vec<ProviderOutput>, ProviderProtocolError>;
}

pub struct GeminiProtocol {
    pub model: String,
    pub system_instruction: String,
    pub native_audio: bool,
}

impl RealtimeProtocol for GeminiProtocol {
    fn setup(&self) -> Value {
        json!({
            "setup": {
                "model": format!("models/{}", self.model),
                "generationConfig": {
                    "responseModalities": [if self.native_audio {"AUDIO"} else {"TEXT"}]
                },
                "systemInstruction": {
                    "parts": [{"text": self.system_instruction}]
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
                "sessionResumption": {},
                "contextWindowCompression": {"slidingWindow": {}},
                "realtimeInputConfig": {
                    "automaticActivityDetection": {
                        "disabled": false,
                        "prefixPaddingMs": 120,
                        "silenceDurationMs": 320
                    },
                    "activityHandling": "START_OF_ACTIVITY_INTERRUPTS"
                }
            }
        })
    }

    fn text(&self, text: &str) -> Result<Vec<Value>, ProviderProtocolError> {
        let text = bounded_text_input(text)?;
        Ok(vec![json!({
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": text}]}],
                "turnComplete": true
            }
        })])
    }

    fn audio(&self, pcm16: &[u8]) -> Result<Value, ProviderProtocolError> {
        media_limit(pcm16, MAX_AUDIO_CHUNK_BYTES)?;
        Ok(json!({
            "realtimeInput": {
                "audio": {
                    "data": BASE64.encode(pcm16),
                    "mimeType": "audio/pcm;rate=16000"
                }
            }
        }))
    }

    fn video(&self, jpeg: &[u8]) -> Result<Value, ProviderProtocolError> {
        media_limit(jpeg, MAX_VIDEO_FRAME_BYTES)?;
        Ok(json!({
            "realtimeInput": {
                "video": {"data": BASE64.encode(jpeg), "mimeType": "image/jpeg"}
            }
        }))
    }

    fn tool_result(&self, call_id: &str, output: &str) -> Value {
        json!({
            "toolResponse": {
                "functionResponses": [{
                    "id": call_id,
                    "response": {"output": bounded_public_text(output)}
                }]
            }
        })
    }

    fn parse(&self, event: &Value) -> Result<Vec<ProviderOutput>, ProviderProtocolError> {
        let mut outputs = Vec::new();
        if event.get("setupComplete").is_some() {
            outputs.push(ProviderOutput::Ready);
        }
        if event.get("goAway").is_some() {
            outputs.push(ProviderOutput::GoAway);
        }
        if let Some(usage) = event.get("usageMetadata") {
            outputs.push(ProviderOutput::Usage(usage.clone()));
        }
        if let Some(content) = event.get("serverContent") {
            if content
                .get("interrupted")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                outputs.push(ProviderOutput::SpeechStarted);
            }
            for field in ["inputTranscription", "outputTranscription"] {
                if let Some(text) = content
                    .get(field)
                    .and_then(|value| value.get("text"))
                    .and_then(Value::as_str)
                {
                    outputs.push(ProviderOutput::PublicCaption {
                        text: bounded_public_text(text),
                        stable: content
                            .get("turnComplete")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                        speaker: if field == "inputTranscription" {
                            CaptionSpeaker::User
                        } else {
                            CaptionSpeaker::Assistant
                        },
                    });
                }
            }
            if let Some(parts) = content
                .pointer("/modelTurn/parts")
                .and_then(Value::as_array)
            {
                for part in parts {
                    if let Some(text) = part.get("text").and_then(Value::as_str) {
                        outputs.push(ProviderOutput::PublicCaption {
                            text: bounded_public_text(text),
                            stable: content
                                .get("turnComplete")
                                .and_then(Value::as_bool)
                                .unwrap_or(false),
                            speaker: CaptionSpeaker::Assistant,
                        });
                    }
                    if let Some(data) = part.pointer("/inlineData/data").and_then(Value::as_str) {
                        outputs.push(ProviderOutput::Audio(
                            BASE64
                                .decode(data)
                                .map_err(|_| ProviderProtocolError::InvalidBase64)?,
                        ));
                    }
                }
            }
        }
        if let Some(calls) = event
            .pointer("/toolCall/functionCalls")
            .and_then(Value::as_array)
        {
            for call in calls {
                outputs.push(ProviderOutput::ToolCall {
                    call_id: required_string(call, "id")?,
                    name: required_string(call, "name")?,
                    arguments: call.get("args").cloned().unwrap_or_else(|| json!({})),
                });
            }
        }
        Ok(outputs)
    }
}

#[cfg(test)]
mod safety_tests {
    use super::*;

    #[test]
    fn realtime_provider_setup_does_not_advertise_tools() {
        let gemini = GeminiProtocol {
            model: "gemini-test".to_owned(),
            system_instruction: "test".to_owned(),
            native_audio: true,
        }
        .setup();
        let glm = GlmProtocol {
            model: "glm-test".to_owned(),
            system_instruction: "test".to_owned(),
            video_enabled: true,
            native_audio: true,
        }
        .setup();

        assert!(gemini.pointer("/setup/tools").is_none());
        assert!(glm.pointer("/session/tools").is_none());
    }
}

pub struct GlmProtocol {
    pub model: String,
    pub system_instruction: String,
    pub video_enabled: bool,
    pub native_audio: bool,
}

impl RealtimeProtocol for GlmProtocol {
    fn setup(&self) -> Value {
        json!({
            "type": "session.update",
            "session": {
                "model": self.model,
                "modalities": if self.native_audio {
                    json!(["audio", "text"])
                } else {
                    json!(["text"])
                },
                "instructions": self.system_instruction,
                "voice": "tongtong",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm",
                "input_audio_noise_reduction": {"type": "near_field"},
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": true,
                    "interrupt_response": true,
                    "prefix_padding_ms": 120,
                    "silence_duration_ms": 320
                },
                "max_response_output_tokens": "inf",
                "beta_fields": {
                    "chat_mode": if self.video_enabled {"video_passive"} else {"audio"},
                    "tts_source": "e2e",
                    "auto_search": false,
                    "greeting_config": {"enable": false}
                }
            }
        })
    }

    fn text(&self, text: &str) -> Result<Vec<Value>, ProviderProtocolError> {
        let text = bounded_text_input(text)?;
        Ok(vec![
            json!({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "object": "realtime.item",
                    "status": "completed",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}]
                }
            }),
            json!({"type": "response.create"}),
        ])
    }

    fn audio(&self, pcm16: &[u8]) -> Result<Value, ProviderProtocolError> {
        media_limit(pcm16, MAX_AUDIO_CHUNK_BYTES)?;
        Ok(json!({"type": "input_audio_buffer.append", "audio": BASE64.encode(pcm16)}))
    }

    fn video(&self, jpeg: &[u8]) -> Result<Value, ProviderProtocolError> {
        media_limit(jpeg, MAX_VIDEO_FRAME_BYTES)?;
        Ok(json!({
            "type": "input_audio_buffer.append_video_frame",
            "video_frame": BASE64.encode(jpeg)
        }))
    }

    fn tool_result(&self, call_id: &str, output: &str) -> Value {
        json!({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "object": "realtime.item",
                "call_id": call_id,
                "output": bounded_public_text(output)
            }
        })
    }

    fn parse(&self, event: &Value) -> Result<Vec<ProviderOutput>, ProviderProtocolError> {
        let kind = event
            .get("type")
            .and_then(Value::as_str)
            .ok_or(ProviderProtocolError::InvalidEvent)?;
        let output = match kind {
            "session.created" => None,
            "session.updated" => Some(ProviderOutput::Ready),
            "input_audio_buffer.speech_started" => Some(ProviderOutput::SpeechStarted),
            "input_audio_buffer.speech_stopped" => Some(ProviderOutput::SpeechStopped),
            "response.audio.delta" => Some(ProviderOutput::Audio(
                BASE64
                    .decode(required_string(event, "delta")?)
                    .map_err(|_| ProviderProtocolError::InvalidBase64)?,
            )),
            "response.text.delta" | "response.audio_transcript.delta" => {
                Some(ProviderOutput::PublicCaption {
                    text: bounded_public_text(&required_string(event, "delta")?),
                    stable: false,
                    speaker: CaptionSpeaker::Assistant,
                })
            }
            "response.text.done" => Some(ProviderOutput::PublicCaption {
                text: bounded_public_text(&required_string(event, "text")?),
                stable: true,
                speaker: CaptionSpeaker::Assistant,
            }),
            "response.audio_transcript.done" => Some(ProviderOutput::PublicCaption {
                text: bounded_public_text(&required_string(event, "transcript")?),
                stable: true,
                speaker: CaptionSpeaker::Assistant,
            }),
            "conversation.item.input_audio_transcription.completed" => {
                Some(ProviderOutput::PublicCaption {
                    text: bounded_public_text(&required_string(event, "transcript")?),
                    stable: true,
                    speaker: CaptionSpeaker::User,
                })
            }
            "response.function_call_arguments.done" => Some(ProviderOutput::ToolCall {
                call_id: required_string(event, "call_id")?,
                name: required_string(event, "name")?,
                arguments: serde_json::from_str(&required_string(event, "arguments")?)
                    .map_err(|_| ProviderProtocolError::InvalidEvent)?,
            }),
            "response.done" => event
                .pointer("/response/usage")
                .cloned()
                .map(ProviderOutput::Usage),
            "heartbeat" => None,
            _ => None,
        };
        Ok(output.into_iter().collect())
    }
}

fn media_limit(bytes: &[u8], maximum: usize) -> Result<(), ProviderProtocolError> {
    if bytes.is_empty() || bytes.len() > maximum {
        Err(ProviderProtocolError::MediaTooLarge)
    } else {
        Ok(())
    }
}

fn required_string(value: &Value, field: &str) -> Result<String, ProviderProtocolError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty() && text.len() <= 64 * 1024)
        .map(str::to_owned)
        .ok_or(ProviderProtocolError::InvalidEvent)
}

fn bounded_public_text(value: &str) -> String {
    value.chars().take(MAX_PUBLIC_TEXT_CHARS).collect()
}

fn bounded_text_input(value: &str) -> Result<String, ProviderProtocolError> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > MAX_TEXT_INPUT_CHARS {
        return Err(ProviderProtocolError::InvalidText);
    }
    Ok(value.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gemini_uses_official_realtime_audio_and_video_shapes() {
        let protocol = GeminiProtocol {
            model: "gemini-3.1-flash-live-preview".to_owned(),
            system_instruction: "Be concise.".to_owned(),
            native_audio: true,
        };
        assert_eq!(
            protocol
                .audio(&[1, 2])
                .expect("audio")
                .pointer("/realtimeInput/audio/mimeType"),
            Some(&json!("audio/pcm;rate=16000"))
        );
        assert_eq!(
            protocol
                .video(&[3, 4])
                .expect("video")
                .pointer("/realtimeInput/video/mimeType"),
            Some(&json!("image/jpeg"))
        );
        assert!(protocol
            .setup()
            .pointer("/setup/sessionResumption")
            .is_some());
    }

    #[test]
    fn glm_uses_server_vad_and_video_passive_mode() {
        let protocol = GlmProtocol {
            model: "glm-realtime-flash".to_owned(),
            system_instruction: "Be concise.".to_owned(),
            video_enabled: true,
            native_audio: true,
        };
        let setup = protocol.setup();
        assert_eq!(
            setup.pointer("/session/turn_detection/type"),
            Some(&json!("server_vad"))
        );
        assert_eq!(
            setup.pointer("/session/beta_fields/chat_mode"),
            Some(&json!("video_passive"))
        );
        assert_eq!(
            protocol.audio(&[1, 2]).expect("audio").get("type"),
            Some(&json!("input_audio_buffer.append"))
        );
        let text = protocol.text("hello").expect("text");
        assert_eq!(
            text[0].pointer("/item/content/0/text"),
            Some(&json!("hello"))
        );
        assert_eq!(text[1].get("type"), Some(&json!("response.create")));
        assert!(protocol
            .parse(&json!({"type": "session.created"}))
            .expect("created")
            .is_empty());
        assert_eq!(
            protocol
                .parse(&json!({"type": "session.updated"}))
                .expect("updated"),
            vec![ProviderOutput::Ready]
        );
    }

    #[test]
    fn provider_events_normalize_without_persisting_conversation_items() {
        let protocol = GlmProtocol {
            model: "glm-realtime-air".to_owned(),
            system_instruction: String::new(),
            video_enabled: false,
            native_audio: false,
        };
        assert_eq!(
            protocol
                .parse(&json!({"type": "response.text.delta", "delta": "hello"}))
                .expect("parse"),
            vec![ProviderOutput::PublicCaption {
                text: "hello".to_owned(),
                stable: false,
                speaker: CaptionSpeaker::Assistant,
            }]
        );
    }

    #[test]
    fn glm_transcription_completion_uses_the_official_transcript_field() {
        let protocol = GlmProtocol {
            model: "glm-realtime-flash".to_owned(),
            system_instruction: String::new(),
            video_enabled: false,
            native_audio: true,
        };
        assert_eq!(
            protocol
                .parse(&json!({
                    "type": "response.audio_transcript.done",
                    "transcript": "assistant words"
                }))
                .expect("assistant transcript"),
            vec![ProviderOutput::PublicCaption {
                text: "assistant words".to_owned(),
                stable: true,
                speaker: CaptionSpeaker::Assistant,
            }]
        );
        assert_eq!(
            protocol
                .parse(&json!({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "user words"
                }))
                .expect("user transcript"),
            vec![ProviderOutput::PublicCaption {
                text: "user words".to_owned(),
                stable: true,
                speaker: CaptionSpeaker::User,
            }]
        );
    }

    #[test]
    fn gemini_distinguishes_user_transcription_from_assistant_text() {
        let protocol = GeminiProtocol {
            model: "gemini-test".to_owned(),
            system_instruction: String::new(),
            native_audio: false,
        };
        let outputs = protocol
            .parse(&json!({
                "serverContent": {
                    "inputTranscription": {"text": "user words"},
                    "modelTurn": {"parts": [{"text": "assistant words"}]},
                    "turnComplete": true
                }
            }))
            .expect("parse");
        assert!(outputs.contains(&ProviderOutput::PublicCaption {
            text: "user words".to_owned(),
            stable: true,
            speaker: CaptionSpeaker::User,
        }));
        assert!(outputs.contains(&ProviderOutput::PublicCaption {
            text: "assistant words".to_owned(),
            stable: true,
            speaker: CaptionSpeaker::Assistant,
        }));
    }
}
