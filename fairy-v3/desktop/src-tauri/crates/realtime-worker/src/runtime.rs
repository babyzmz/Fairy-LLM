use std::collections::VecDeque;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use zeroize::{Zeroize, Zeroizing};

use crate::media::{
    mix_pcm16_queue, resample_pcm16, AudioPlayback, MicrophoneCapture, ProcessLoopbackCapture,
    VideoCapture,
};
use crate::protocol::{ProviderKind, WorkerEvent};
use crate::provider::{CaptionSpeaker, ProviderOutput};
use crate::transport::ProviderSocket;

struct EphemeralAudioQueue(VecDeque<i16>);

impl EphemeralAudioQueue {
    fn with_capacity(capacity: usize) -> Self {
        Self(VecDeque::with_capacity(capacity))
    }

    fn extend_bounded(&mut self, samples: impl IntoIterator<Item = i16>, maximum: usize) {
        self.0.extend(samples);
        while self.0.len() > maximum {
            self.0.pop_front();
        }
    }

    fn mix_into(&mut self, target: &mut [i16], gain: f32) {
        mix_pcm16_queue(target, &mut self.0, gain);
    }
}

impl Drop for EphemeralAudioQueue {
    fn drop(&mut self) {
        for sample in &mut self.0 {
            *sample = 0;
        }
        self.0.clear();
    }
}

pub struct RuntimeLaunch {
    pub session_id: String,
    pub provider: ProviderKind,
    pub credential: Zeroizing<String>,
    pub system_instruction: String,
    pub source_id: Option<u64>,
    pub screen_enabled: bool,
    pub game_audio_enabled: bool,
    pub voice_mode: String,
}

pub enum RuntimeCommand {
    Stop,
    ToolResult {
        call_id: String,
        public_summary: String,
    },
}

pub struct RealtimeRuntime {
    commands: mpsc::Sender<RuntimeCommand>,
    events: Option<mpsc::Receiver<WorkerEvent>>,
    worker: Option<thread::JoinHandle<()>>,
}

impl RealtimeRuntime {
    pub fn spawn(launch: RuntimeLaunch) -> Self {
        let (command_sender, command_receiver) = mpsc::channel();
        let (event_sender, event_receiver) = mpsc::channel();
        let worker = thread::Builder::new()
            .name("fairy-realtime-session".to_owned())
            .spawn(move || run_session(launch, command_receiver, event_sender))
            .ok();
        Self {
            commands: command_sender,
            events: Some(event_receiver),
            worker,
        }
    }

    pub fn command(&self, command: RuntimeCommand) -> bool {
        self.commands.send(command).is_ok()
    }

    pub fn take_events(&mut self) -> Option<mpsc::Receiver<WorkerEvent>> {
        self.events.take()
    }
}

impl Drop for RealtimeRuntime {
    fn drop(&mut self) {
        let _ = self.commands.send(RuntimeCommand::Stop);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn run_session(
    mut launch: RuntimeLaunch,
    commands: mpsc::Receiver<RuntimeCommand>,
    events: mpsc::Sender<WorkerEvent>,
) {
    let session_id = launch.session_id.clone();
    let native_audio = match launch.voice_mode.as_str() {
        "native" => true,
        "fairy" => false,
        _ => {
            emit_failed(&events, &session_id, "REALTIME_VOICE_MODE_INVALID");
            launch.credential.zeroize();
            return;
        }
    };
    let mut provider = match ProviderSocket::connect(
        launch.provider.clone(),
        launch.credential,
        launch.system_instruction,
        launch.screen_enabled,
        native_audio,
    ) {
        Ok(provider) => provider,
        Err(_) => {
            emit_failed(&events, &session_id, "REALTIME_PROVIDER_UNAVAILABLE");
            return;
        }
    };
    let microphone = match MicrophoneCapture::start() {
        Ok(microphone) => microphone,
        Err(_) => {
            emit_failed(&events, &session_id, "MICROPHONE_UNAVAILABLE");
            return;
        }
    };
    let mut game_audio = if launch.game_audio_enabled {
        match launch
            .source_id
            .and_then(|source_id| ProcessLoopbackCapture::start(source_id).ok())
        {
            Some(capture) => Some(capture),
            None => {
                emit_failed(&events, &session_id, "PROCESS_LOOPBACK_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    let playback = if native_audio {
        match AudioPlayback::start() {
            Ok(playback) => Some(playback),
            Err(_) => {
                emit_failed(&events, &session_id, "AUDIO_OUTPUT_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    let video = if launch.screen_enabled {
        match launch
            .source_id
            .and_then(|id| VideoCapture::start(id, 30).ok())
        {
            Some(video) => Some(video),
            None => {
                emit_failed(&events, &session_id, "CAPTURE_SOURCE_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    let _ = events.send(WorkerEvent::SessionState {
        session_id: session_id.clone(),
        status: "active",
        provider: Some(launch.provider),
        error_code: None,
    });
    let mut last_video_sent = Instant::now() - Duration::from_secs(1);
    let mut last_usage_sent = Instant::now();
    let mut game_audio_queue = EphemeralAudioQueue::with_capacity(32_000);
    let mut audio_input_samples = 0_u64;
    let mut audio_output_samples = 0_u64;
    let mut video_frame_count = 0_u64;
    let mut interruption_count = 0_u64;
    let mut tool_call_count = 0_u64;
    loop {
        match commands.try_recv() {
            Ok(RuntimeCommand::Stop) | Err(mpsc::TryRecvError::Disconnected) => break,
            Ok(RuntimeCommand::ToolResult {
                call_id,
                public_summary,
            }) => {
                if provider
                    .send_tool_result(&call_id, &public_summary)
                    .is_err()
                {
                    emit_failed(&events, &session_id, "REALTIME_PROVIDER_INTERRUPTED");
                    return;
                }
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        if let Some(capture) = game_audio.as_mut() {
            while let Some(packet) = capture.try_recv() {
                game_audio_queue.extend_bounded(
                    resample_pcm16(&packet.pcm16, packet.sample_rate, 16_000),
                    32_000,
                );
            }
        }
        while let Some(packet) = microphone.try_recv() {
            let mut samples = resample_pcm16(&packet.pcm16, packet.sample_rate, 16_000);
            game_audio_queue.mix_into(&mut samples, 0.35);
            audio_input_samples = audio_input_samples.saturating_add(samples.len() as u64);
            let mut bytes = Vec::with_capacity(samples.len() * 2);
            for sample in &samples {
                bytes.extend_from_slice(&sample.to_le_bytes());
            }
            samples.zeroize();
            if provider.send_audio(&bytes).is_err() {
                bytes.zeroize();
                emit_failed(&events, &session_id, "REALTIME_PROVIDER_INTERRUPTED");
                return;
            }
            bytes.zeroize();
        }
        if last_video_sent.elapsed() >= Duration::from_secs(1) {
            if let Some(mut frame) = video.as_ref().and_then(VideoCapture::take_latest) {
                if provider.send_video(&frame.jpeg).is_err() {
                    frame.jpeg.zeroize();
                    emit_failed(&events, &session_id, "REALTIME_PROVIDER_INTERRUPTED");
                    return;
                }
                frame.jpeg.zeroize();
                video_frame_count = video_frame_count.saturating_add(1);
                last_video_sent = Instant::now();
            }
        }
        let outputs = match provider.receive() {
            Ok(outputs) => outputs,
            Err(_) => {
                emit_failed(&events, &session_id, "REALTIME_PROVIDER_INTERRUPTED");
                return;
            }
        };
        for output in outputs {
            match output {
                ProviderOutput::Ready => {}
                ProviderOutput::Audio(mut bytes) => {
                    audio_output_samples =
                        audio_output_samples.saturating_add((bytes.len() / 2) as u64);
                    let mut samples = bytes
                        .chunks_exact(2)
                        .map(|pair| i16::from_le_bytes([pair[0], pair[1]]))
                        .collect::<Vec<_>>();
                    if let Some(playback) = playback.as_ref() {
                        playback.enqueue_pcm16(&samples, 24_000);
                    }
                    samples.zeroize();
                    bytes.zeroize();
                    let _ = events.send(WorkerEvent::Presence {
                        session_id: session_id.clone(),
                        state: "speaking",
                        level: None,
                    });
                }
                ProviderOutput::PublicCaption {
                    text,
                    stable,
                    speaker,
                } => {
                    let _ = events.send(WorkerEvent::PublicCaption {
                        session_id: session_id.clone(),
                        text,
                        stable,
                        speaker: match speaker {
                            CaptionSpeaker::User => "user",
                            CaptionSpeaker::Assistant => "assistant",
                        },
                    });
                }
                ProviderOutput::SpeechStarted => {
                    interruption_count = interruption_count.saturating_add(1);
                    if let Some(playback) = playback.as_ref() {
                        playback.clear();
                    }
                    let _ = events.send(WorkerEvent::Presence {
                        session_id: session_id.clone(),
                        state: "listening",
                        level: None,
                    });
                }
                ProviderOutput::SpeechStopped => {
                    let _ = events.send(WorkerEvent::Presence {
                        session_id: session_id.clone(),
                        state: "analyzing",
                        level: None,
                    });
                }
                ProviderOutput::ToolCall {
                    call_id,
                    name,
                    arguments: _,
                } => {
                    tool_call_count = tool_call_count.saturating_add(1);
                    let _ = events.send(WorkerEvent::ToolRequest {
                        session_id: session_id.clone(),
                        call_id,
                        tool_name: name,
                        public_intent: "Fairy wants to use a companion tool".to_owned(),
                    });
                }
                ProviderOutput::Usage(_) => {}
                ProviderOutput::GoAway => {
                    emit_failed(&events, &session_id, "REALTIME_PROVIDER_GOING_AWAY");
                    return;
                }
            }
        }
        if last_usage_sent.elapsed() >= Duration::from_secs(5) {
            emit_usage(
                &events,
                &session_id,
                audio_input_samples,
                audio_output_samples,
                video_frame_count,
                interruption_count,
                tool_call_count,
            );
            last_usage_sent = Instant::now();
        }
    }
    if let Some(playback) = playback.as_ref() {
        playback.clear();
    }
    emit_usage(
        &events,
        &session_id,
        audio_input_samples,
        audio_output_samples,
        video_frame_count,
        interruption_count,
        tool_call_count,
    );
    let _ = events.send(WorkerEvent::SessionState {
        session_id,
        status: "completed",
        provider: None,
        error_code: None,
    });
}

fn emit_usage(
    events: &mpsc::Sender<WorkerEvent>,
    session_id: &str,
    audio_input_samples: u64,
    audio_output_samples: u64,
    video_frame_count: u64,
    interruption_count: u64,
    tool_call_count: u64,
) {
    let _ = events.send(WorkerEvent::Usage {
        session_id: session_id.to_owned(),
        audio_input_ms: audio_input_samples.saturating_mul(1_000) / 16_000,
        audio_output_ms: audio_output_samples.saturating_mul(1_000) / 24_000,
        video_frame_count,
        interruption_count,
        tool_call_count,
    });
}

fn emit_failed(events: &mpsc::Sender<WorkerEvent>, session_id: &str, error_code: &'static str) {
    let _ = events.send(WorkerEvent::SessionState {
        session_id: session_id.to_owned(),
        status: "failed",
        provider: None,
        error_code: Some(error_code),
    });
}
