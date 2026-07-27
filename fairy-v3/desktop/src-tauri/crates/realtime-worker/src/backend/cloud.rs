use zeroize::Zeroizing;

use super::{
    BackendCaptionSpeaker, BackendError, BackendEvent, RealtimeBackend, RealtimeCloudProviderKind,
};
use crate::provider::{CaptionSpeaker, ProviderOutput};
use crate::transport::ProviderSocket;

pub struct CloudBackendLaunch {
    pub provider: RealtimeCloudProviderKind,
    pub credential: Zeroizing<String>,
    pub system_instruction: String,
    pub video_enabled: bool,
    pub native_audio: bool,
}

pub struct CloudLiveBackend {
    socket: Option<ProviderSocket>,
    provider: RealtimeCloudProviderKind,
    credential: Zeroizing<String>,
    system_instruction: String,
    video_enabled: bool,
    native_audio: bool,
}

impl CloudLiveBackend {
    pub fn connect(launch: CloudBackendLaunch) -> Result<Self, BackendError> {
        let socket = ProviderSocket::connect(
            launch.provider,
            launch.credential.clone(),
            launch.system_instruction.clone(),
            launch.video_enabled,
            launch.native_audio,
        )?;
        Ok(Self {
            socket: Some(socket),
            provider: launch.provider,
            credential: launch.credential,
            system_instruction: launch.system_instruction,
            video_enabled: launch.video_enabled,
            native_audio: launch.native_audio,
        })
    }
}

impl RealtimeBackend for CloudLiveBackend {
    fn push_microphone(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError> {
        self.socket_mut()?
            .send_audio(pcm16_le)
            .map_err(BackendError::from)
    }

    fn push_application_audio(&mut self, _pcm16_le: &[u8]) -> Result<(), BackendError> {
        distinct_application_audio_unavailable()
    }

    fn push_video(&mut self, jpeg: &[u8]) -> Result<(), BackendError> {
        self.socket_mut()?
            .send_video(jpeg)
            .map_err(BackendError::from)
    }

    fn push_text(&mut self, text: &str) -> Result<(), BackendError> {
        self.socket_mut()?
            .send_text(text)
            .map_err(BackendError::from)
    }

    fn push_assistance_result(
        &mut self,
        call_id: &str,
        public_summary: &str,
    ) -> Result<(), BackendError> {
        self.socket_mut()?
            .send_tool_result(call_id, public_summary)
            .map_err(BackendError::from)
    }

    fn rotate_context(
        &mut self,
        _next_context_epoch: u64,
        reason: &str,
        public_summary: &str,
    ) -> Result<(), BackendError> {
        if reason.is_empty() || reason.len() > 64 || public_summary.chars().count() > 2_000 {
            return Err(BackendError::DialogueProtocol);
        }
        let system_instruction =
            instruction_with_carryover(&self.system_instruction, public_summary);
        let replacement = ProviderSocket::connect(
            self.provider,
            self.credential.clone(),
            system_instruction,
            self.video_enabled,
            self.native_audio,
        )?;
        self.socket = Some(replacement);
        Ok(())
    }

    fn set_activity_profile(
        &mut self,
        _activity_profile: crate::backend::RealtimeActivityProfile,
    ) -> Result<(), BackendError> {
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<BackendEvent>, BackendError> {
        let Some(socket) = self.socket.as_mut() else {
            return Ok(Vec::new());
        };
        socket
            .receive()?
            .into_iter()
            .map(|output| {
                Ok(match output {
                    ProviderOutput::Ready => BackendEvent::Ready,
                    ProviderOutput::Audio(bytes) => BackendEvent::Audio(bytes),
                    ProviderOutput::PublicCaption {
                        text,
                        stable,
                        speaker,
                    } => BackendEvent::PublicCaption {
                        text,
                        stable,
                        speaker: match speaker {
                            CaptionSpeaker::User => BackendCaptionSpeaker::User,
                            CaptionSpeaker::Assistant => BackendCaptionSpeaker::Assistant,
                        },
                    },
                    ProviderOutput::SpeechStarted => BackendEvent::SpeechStarted,
                    ProviderOutput::SpeechStopped => BackendEvent::SpeechStopped,
                    ProviderOutput::ToolCall {
                        call_id,
                        name,
                        arguments,
                    } => BackendEvent::ToolCall {
                        call_id,
                        name,
                        arguments,
                    },
                    ProviderOutput::Usage(value) => BackendEvent::Usage(value),
                    ProviderOutput::GoAway => BackendEvent::GoAway,
                })
            })
            .collect()
    }

    fn pause(&mut self) -> Result<(), BackendError> {
        self.socket = None;
        Ok(())
    }

    fn resume(&mut self) -> Result<(), BackendError> {
        if self.socket.is_some() {
            return Ok(());
        }
        self.socket = Some(ProviderSocket::connect(
            self.provider,
            self.credential.clone(),
            self.system_instruction.clone(),
            self.video_enabled,
            self.native_audio,
        )?);
        Ok(())
    }

    fn stop(&mut self) -> Result<(), BackendError> {
        self.socket = None;
        Ok(())
    }
}

impl CloudLiveBackend {
    fn socket_mut(&mut self) -> Result<&mut ProviderSocket, BackendError> {
        self.socket.as_mut().ok_or(BackendError::DialogueProtocol)
    }
}

fn distinct_application_audio_unavailable() -> Result<(), BackendError> {
    Err(BackendError::ApplicationAudioScopeUnavailable)
}

fn instruction_with_carryover(base: &str, public_summary: &str) -> String {
    let summary = public_summary.trim();
    if summary.is_empty() {
        base.to_owned()
    } else {
        format!("{base}\n\nPublic continuity summary:\n{summary}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn application_audio_fails_closed_instead_of_entering_microphone_speech() {
        assert!(matches!(
            distinct_application_audio_unavailable(),
            Err(BackendError::ApplicationAudioScopeUnavailable)
        ));
        assert_eq!(
            BackendError::ApplicationAudioScopeUnavailable.public_code(),
            "APPLICATION_AUDIO_SCOPE_UNAVAILABLE"
        );
    }

    #[test]
    fn reconnect_instruction_uses_public_carryover_without_accumulating_it() {
        let instruction = instruction_with_carryover("Fairy Persona", "Current goal: test");
        assert_eq!(
            instruction,
            "Fairy Persona\n\nPublic continuity summary:\nCurrent goal: test"
        );
        assert_eq!(
            instruction_with_carryover("Fairy Persona", ""),
            "Fairy Persona"
        );
    }
}
