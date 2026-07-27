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
    socket: ProviderSocket,
}

impl CloudLiveBackend {
    pub fn connect(launch: CloudBackendLaunch) -> Result<Self, BackendError> {
        Ok(Self {
            socket: ProviderSocket::connect(
                launch.provider,
                launch.credential,
                launch.system_instruction,
                launch.video_enabled,
                launch.native_audio,
            )?,
        })
    }
}

impl RealtimeBackend for CloudLiveBackend {
    fn push_microphone(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError> {
        self.socket.send_audio(pcm16_le).map_err(BackendError::from)
    }

    fn push_application_audio(&mut self, _pcm16_le: &[u8]) -> Result<(), BackendError> {
        distinct_application_audio_unavailable()
    }

    fn push_video(&mut self, jpeg: &[u8]) -> Result<(), BackendError> {
        self.socket.send_video(jpeg).map_err(BackendError::from)
    }

    fn push_text(&mut self, text: &str) -> Result<(), BackendError> {
        self.socket.send_text(text).map_err(BackendError::from)
    }

    fn push_assistance_result(
        &mut self,
        call_id: &str,
        public_summary: &str,
    ) -> Result<(), BackendError> {
        self.socket
            .send_tool_result(call_id, public_summary)
            .map_err(BackendError::from)
    }

    fn poll(&mut self) -> Result<Vec<BackendEvent>, BackendError> {
        self.socket
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
        Ok(())
    }

    fn stop(&mut self) -> Result<(), BackendError> {
        Ok(())
    }
}

fn distinct_application_audio_unavailable() -> Result<(), BackendError> {
    Err(BackendError::ApplicationAudioScopeUnavailable)
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
}
