use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
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

// Absolute worker-side ceiling as defense in depth: the renderer enforces the
// user's configurable maximum, and this backstop stops a runaway session if the
// renderer timer ever fails to fire. It sits above the maximum renderer setting.
const SESSION_HARD_LIMIT: Duration = Duration::from_secs(130 * 60);

fn frame_hash(bytes: &[u8]) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    bytes.hash(&mut hasher);
    hasher.finish()
}

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
    SetInput {
        microphone: bool,
        video: bool,
    },
}

pub struct RealtimeRuntime {
    commands: mpsc::Sender<RuntimeCommand>,
    events: Option<mpsc::Receiver<WorkerEvent>>,
    worker: Option<thread::JoinHandle<()>>,
    cancelled: Arc<AtomicBool>,
}

impl RealtimeRuntime {
    pub fn spawn(launch: RuntimeLaunch) -> Self {
        let (command_sender, command_receiver) = mpsc::channel();
        let (event_sender, event_receiver) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let worker_cancelled = Arc::clone(&cancelled);
        let worker = thread::Builder::new()
            .name("fairy-realtime-session".to_owned())
            .spawn(move || run_session(launch, command_receiver, event_sender, worker_cancelled))
            .ok();
        Self {
            commands: command_sender,
            events: Some(event_receiver),
            worker,
            cancelled,
        }
    }

    pub fn command(&self, command: RuntimeCommand) -> bool {
        if matches!(command, RuntimeCommand::Stop) {
            self.cancelled.store(true, Ordering::Release);
        }
        self.commands.send(command).is_ok()
    }

    pub fn take_events(&mut self) -> Option<mpsc::Receiver<WorkerEvent>> {
        self.events.take()
    }
}

impl Drop for RealtimeRuntime {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
        let _ = self.commands.send(RuntimeCommand::Stop);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn run_session(
    launch: RuntimeLaunch,
    commands: mpsc::Receiver<RuntimeCommand>,
    events: mpsc::Sender<WorkerEvent>,
    cancelled: Arc<AtomicBool>,
) {
    let RuntimeLaunch {
        session_id,
        provider: provider_kind,
        credential,
        system_instruction,
        source_id,
        screen_enabled,
        game_audio_enabled,
        voice_mode,
    } = launch;
    let native_audio = match voice_mode.as_str() {
        "native" => true,
        "fairy" => false,
        _ => {
            emit_failed(&events, &session_id, "REALTIME_VOICE_MODE_INVALID");
            return;
        }
    };
    let (provider_sender, provider_receiver) = mpsc::sync_channel(1);
    let connect_cancelled = Arc::clone(&cancelled);
    let connect_provider = provider_kind.clone();
    if thread::Builder::new()
        .name("fairy-realtime-connect".to_owned())
        .spawn(move || {
            let result = ProviderSocket::connect(
                connect_provider,
                credential,
                system_instruction,
                screen_enabled,
                native_audio,
            );
            if !connect_cancelled.load(Ordering::Acquire) {
                let _ = provider_sender.send(result);
            }
        })
        .is_err()
    {
        emit_failed(&events, &session_id, "WORKER_INTERRUPTED");
        return;
    }
    let provider_deadline = Instant::now() + Duration::from_secs(15);
    let mut provider = loop {
        if startup_cancel_requested(&commands, &cancelled) {
            emit_cancelled(&events, &session_id);
            return;
        }
        match provider_receiver.recv_timeout(Duration::from_millis(5)) {
            Ok(Ok(provider)) => break provider,
            Ok(Err(error)) => {
                emit_failed(&events, &session_id, error.public_code());
                return;
            }
            Err(mpsc::RecvTimeoutError::Timeout) if Instant::now() < provider_deadline => {}
            Err(mpsc::RecvTimeoutError::Timeout) => {
                cancelled.store(true, Ordering::Release);
                emit_failed(&events, &session_id, "REALTIME_PROVIDER_TIMEOUT");
                return;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                emit_failed(&events, &session_id, "WORKER_INTERRUPTED");
                return;
            }
        }
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &session_id);
        return;
    }
    let microphone = match MicrophoneCapture::start() {
        Ok(microphone) => microphone,
        Err(_) => {
            emit_failed(&events, &session_id, "MICROPHONE_UNAVAILABLE");
            return;
        }
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &session_id);
        return;
    }
    let mut game_audio = if game_audio_enabled {
        match source_id.and_then(|source_id| ProcessLoopbackCapture::start(source_id).ok()) {
            Some(capture) => Some(capture),
            None => {
                emit_failed(&events, &session_id, "PROCESS_LOOPBACK_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &session_id);
        return;
    }
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
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &session_id);
        return;
    }
    let video = if screen_enabled {
        match source_id.and_then(|id| VideoCapture::start(id, 30).ok()) {
            Some(video) => Some(video),
            None => {
                emit_failed(&events, &session_id, "CAPTURE_SOURCE_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &session_id);
        return;
    }
    let _ = events.send(WorkerEvent::SessionState {
        session_id: session_id.clone(),
        status: "active",
        provider: Some(provider_kind),
        error_code: None,
    });
    let session_start = Instant::now();
    let mut last_video_sent = Instant::now() - Duration::from_secs(1);
    let mut last_usage_sent = Instant::now();
    let mut last_frame_hash: Option<u64> = None;
    // Input gating for the mute (microphone) and pause (microphone + screen)
    // controls. Both default on; muted/paused input is not sent to the provider.
    let mut microphone_enabled = true;
    let mut video_enabled = true;
    let mut game_audio_queue = EphemeralAudioQueue::with_capacity(32_000);
    let mut audio_input_samples = 0_u64;
    let mut audio_output_samples = 0_u64;
    let mut video_frame_count = 0_u64;
    let mut interruption_count = 0_u64;
    let mut tool_call_count = 0_u64;
    loop {
        if cancelled.load(Ordering::Acquire) {
            break;
        }
        if session_start.elapsed() >= SESSION_HARD_LIMIT {
            break;
        }
        match commands.try_recv() {
            Ok(RuntimeCommand::Stop) | Err(mpsc::TryRecvError::Disconnected) => break,
            Ok(RuntimeCommand::ToolResult {
                call_id,
                public_summary,
            }) => {
                if let Err(error) = provider.send_tool_result(&call_id, &public_summary) {
                    emit_failed(&events, &session_id, error.public_code());
                    return;
                }
            }
            Ok(RuntimeCommand::SetInput { microphone, video }) => {
                microphone_enabled = microphone;
                video_enabled = video;
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
            if !microphone_enabled {
                // Muted or paused: drain and discard without sending. Dropping the
                // packet zeroizes its samples.
                drop(packet);
                continue;
            }
            let mut samples = resample_pcm16(&packet.pcm16, packet.sample_rate, 16_000);
            game_audio_queue.mix_into(&mut samples, 0.35);
            audio_input_samples = audio_input_samples.saturating_add(samples.len() as u64);
            let mut bytes = Vec::with_capacity(samples.len() * 2);
            for sample in &samples {
                bytes.extend_from_slice(&sample.to_le_bytes());
            }
            samples.zeroize();
            if let Err(error) = provider.send_audio(&bytes) {
                bytes.zeroize();
                emit_failed(&events, &session_id, error.public_code());
                return;
            }
            bytes.zeroize();
        }
        if video_enabled && last_video_sent.elapsed() >= Duration::from_secs(1) {
            if let Some(mut frame) = video.as_ref().and_then(VideoCapture::take_latest) {
                let hash = frame_hash(&frame.jpeg);
                if last_frame_hash == Some(hash) {
                    // Identical to the last sent frame (a static screen); skip the
                    // send to avoid re-billing image tokens for an unchanged view.
                    frame.jpeg.zeroize();
                    last_video_sent = Instant::now();
                } else {
                    if let Err(error) = provider.send_video(&frame.jpeg) {
                        frame.jpeg.zeroize();
                        emit_failed(&events, &session_id, error.public_code());
                        return;
                    }
                    frame.jpeg.zeroize();
                    last_frame_hash = Some(hash);
                    video_frame_count = video_frame_count.saturating_add(1);
                    last_video_sent = Instant::now();
                }
            }
        }
        let outputs = match provider.receive() {
            Ok(outputs) => outputs,
            Err(error) => {
                emit_failed(&events, &session_id, error.public_code());
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
                    let _ = events.send(WorkerEvent::BargeIn {
                        session_id: session_id.clone(),
                    });
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

fn startup_cancel_requested(
    commands: &mpsc::Receiver<RuntimeCommand>,
    cancelled: &AtomicBool,
) -> bool {
    if cancelled.load(Ordering::Acquire) {
        return true;
    }
    loop {
        match commands.try_recv() {
            Ok(RuntimeCommand::Stop) | Err(mpsc::TryRecvError::Disconnected) => {
                cancelled.store(true, Ordering::Release);
                return true;
            }
            Ok(RuntimeCommand::ToolResult { .. }) | Ok(RuntimeCommand::SetInput { .. }) => {}
            Err(mpsc::TryRecvError::Empty) => return false,
        }
    }
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

fn emit_cancelled(events: &mpsc::Sender<WorkerEvent>, session_id: &str) {
    let _ = events.send(WorkerEvent::SessionState {
        session_id: session_id.to_owned(),
        status: "cancelled",
        provider: None,
        error_code: None,
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_frames_hash_equal_and_changed_frames_differ() {
        assert_eq!(frame_hash(&[1, 2, 3, 4]), frame_hash(&[1, 2, 3, 4]));
        assert_ne!(frame_hash(&[1, 2, 3, 4]), frame_hash(&[1, 2, 3, 5]));
    }

    #[test]
    fn session_hard_limit_backstops_above_the_max_renderer_setting() {
        // The renderer maximum is 120 minutes; the worker ceiling sits above it
        // so the renderer normally stops first and this only catches a runaway.
        assert!(SESSION_HARD_LIMIT > Duration::from_secs(120 * 60));
    }

    #[test]
    fn startup_stop_is_observed_within_the_barge_in_budget() {
        let (sender, receiver) = mpsc::channel();
        let cancelled = AtomicBool::new(false);
        sender.send(RuntimeCommand::Stop).expect("stop command");
        let started = Instant::now();

        assert!(startup_cancel_requested(&receiver, &cancelled));
        assert!(started.elapsed() < Duration::from_millis(100));
        assert!(cancelled.load(Ordering::Acquire));
    }
}
