use std::collections::VecDeque;

use zeroize::{Zeroize, Zeroizing};

use super::{
    BackendCaptionSpeaker, BackendError, BackendEvent, RealtimeBackend, RealtimeCloudProviderKind,
};
use crate::media::mix_pcm16_queue;
use crate::provider::{CaptionSpeaker, ProviderOutput};
use crate::transport::ProviderSocket;

const APPLICATION_AUDIO_SAMPLE_LIMIT: usize = 32_000;
const APPLICATION_AUDIO_GAIN: f32 = 0.35;

fn clear_audio_queue(queue: &mut VecDeque<i16>) {
    for sample in queue.iter_mut() {
        *sample = 0;
    }
    queue.clear();
}

pub struct CloudBackendLaunch {
    pub provider: RealtimeCloudProviderKind,
    pub credential: Zeroizing<String>,
    pub system_instruction: String,
    pub video_enabled: bool,
    pub native_audio: bool,
}

pub struct CloudLiveBackend {
    socket: ProviderSocket,
    application_audio: VecDeque<i16>,
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
            application_audio: VecDeque::with_capacity(APPLICATION_AUDIO_SAMPLE_LIMIT),
        })
    }

    fn clear_application_audio(&mut self) {
        clear_audio_queue(&mut self.application_audio);
    }
}

impl RealtimeBackend for CloudLiveBackend {
    fn push_microphone(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError> {
        let mut samples = pcm16_le
            .chunks_exact(2)
            .map(|pair| i16::from_le_bytes([pair[0], pair[1]]))
            .collect::<Vec<_>>();
        mix_pcm16_queue(
            &mut samples,
            &mut self.application_audio,
            APPLICATION_AUDIO_GAIN,
        );
        let mut bytes = Vec::with_capacity(samples.len() * 2);
        for sample in &samples {
            bytes.extend_from_slice(&sample.to_le_bytes());
        }
        samples.zeroize();
        let result = self.socket.send_audio(&bytes).map_err(BackendError::from);
        bytes.zeroize();
        result
    }

    fn push_application_audio(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError> {
        self.application_audio.extend(
            pcm16_le
                .chunks_exact(2)
                .map(|pair| i16::from_le_bytes([pair[0], pair[1]])),
        );
        while self.application_audio.len() > APPLICATION_AUDIO_SAMPLE_LIMIT {
            self.application_audio.pop_front();
        }
        Ok(())
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
        self.clear_application_audio();
        Ok(())
    }

    fn stop(&mut self) -> Result<(), BackendError> {
        self.clear_application_audio();
        Ok(())
    }
}

impl Drop for CloudLiveBackend {
    fn drop(&mut self) {
        self.clear_application_audio();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn application_audio_queue_is_bounded_and_zeroized_on_pause() {
        let mut queue = VecDeque::from(vec![1_i16; APPLICATION_AUDIO_SAMPLE_LIMIT + 1]);
        while queue.len() > APPLICATION_AUDIO_SAMPLE_LIMIT {
            queue.pop_front();
        }
        assert_eq!(queue.len(), APPLICATION_AUDIO_SAMPLE_LIMIT);
        clear_audio_queue(&mut queue);
        assert!(queue.is_empty());
    }
}
