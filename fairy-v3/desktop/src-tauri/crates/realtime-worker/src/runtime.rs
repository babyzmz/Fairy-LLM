use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};

use zeroize::{Zeroize, Zeroizing};

use crate::audio_processing::{RealtimeAudioProcessor, RealtimeMicrophoneProcessor};
use crate::backend::{
    BackendCaptionSpeaker, BackendEvent, CloudBackendLaunch, CloudLiveBackend, LocalOmniBackend,
    LocalOmniLaunch, RealtimeActivityProfile, RealtimeBackend, RealtimeBackendKind,
    RealtimeCandidateDecision, RealtimeCloudProviderKind, RealtimeDialogueCandidate,
    RealtimeVoiceOutput,
};
use crate::frame_gate::{FrameGate, FrameGateDecision};
use crate::media::{
    resample_pcm16, AudioPlayback, MicrophoneCapture, ProcessLoopbackCapture, VideoCapture,
};
use crate::protocol::{
    RealtimeContextCarryover, RealtimeResourceLevel, RealtimeResourcePolicy, WorkerEvent,
};
use crate::runtime_events::{
    emit_backend_failure, emit_cancelled, emit_failed, emit_sidecar_failure, emit_usage,
};
use crate::ValidatedRealtimePersona;

// Absolute worker-side ceiling as defense in depth above the Coordinator-owned
// native Presence limit.
const SESSION_HARD_LIMIT: Duration = Duration::from_secs(250 * 60);
const VIDEO_CAPTURE_FPS: u16 = 15;

pub struct RuntimeLaunch {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub backend: RealtimeBackendKind,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub credential: Option<Zeroizing<String>>,
    pub local_omni: Option<LocalOmniLaunch>,
    pub persona: ValidatedRealtimePersona,
    pub activity_profile: RealtimeActivityProfile,
    pub desktop_host_process_id: u32,
    pub source_id: Option<u64>,
    pub microphone_enabled: bool,
    pub screen_enabled: bool,
    pub application_audio_enabled: bool,
    pub voice_output: RealtimeVoiceOutput,
}

#[derive(Clone, Debug)]
pub(super) struct RuntimeIdentity {
    pub(super) session_id: String,
    pub(super) segment_id: String,
    pub(super) context_epoch: u64,
    pub(super) backend: RealtimeBackendKind,
    pub(super) cloud_provider: Option<RealtimeCloudProviderKind>,
    pub(super) persona_digest: String,
    pub(super) activity_profile: RealtimeActivityProfile,
}

pub enum RuntimeCommand {
    Stop,
    Text {
        text: String,
    },
    ToolResult {
        call_id: String,
        public_summary: String,
    },
    AssistanceResult {
        request_id: String,
        public_summary: String,
        succeeded: bool,
    },
    SetInput {
        microphone: bool,
        video: bool,
    },
    SetProfile {
        activity_profile: RealtimeActivityProfile,
    },
    SetResourcePolicy {
        policy: RealtimeResourcePolicy,
    },
    SetMediaPrivacy {
        paused: bool,
    },
    ReplaceCaptureSource {
        next_context_epoch: u64,
        source_id: u64,
        source_sequence: u64,
        screen_enabled: bool,
        application_audio_enabled: bool,
        reason: String,
        carryover: RealtimeContextCarryover,
    },
    RetryMediaChannel {
        channel: String,
        source_sequence: u64,
    },
    RotateContext {
        next_context_epoch: u64,
        reason: String,
        carryover: RealtimeContextCarryover,
    },
    Pause,
    Resume {
        carryover: RealtimeContextCarryover,
    },
    WakeSegment {
        next_segment_id: String,
        carryover: RealtimeContextCarryover,
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
        segment_id,
        context_epoch,
        backend,
        cloud_provider,
        credential,
        local_omni,
        persona,
        activity_profile,
        desktop_host_process_id,
        source_id,
        microphone_enabled: initial_microphone_enabled,
        screen_enabled,
        application_audio_enabled,
        voice_output,
    } = launch;
    let mut identity = RuntimeIdentity {
        session_id,
        segment_id,
        context_epoch,
        backend,
        cloud_provider,
        persona_digest: persona.digest().to_owned(),
        activity_profile,
    };
    let mut candidate_emitted = false;
    if !application_audio_scope_supported(identity.backend, application_audio_enabled) {
        emit_failed(&events, &identity, "APPLICATION_AUDIO_SCOPE_UNAVAILABLE");
        return;
    }
    let native_audio = backend == RealtimeBackendKind::CloudLive
        && voice_output == RealtimeVoiceOutput::ProviderNativeVoice;
    let (backend_sender, backend_receiver) = mpsc::sync_channel(1);
    let connect_cancelled = Arc::clone(&cancelled);
    let connect_provider = identity.cloud_provider;
    let connect_backend = identity.backend;
    let connect_session_id = identity.session_id.clone();
    let connect_segment_id = identity.segment_id.clone();
    if thread::Builder::new()
        .name("fairy-realtime-connect".to_owned())
        .spawn(move || {
            let result = match connect_backend {
                RealtimeBackendKind::CloudLive => match (connect_provider, credential) {
                    (Some(provider), Some(credential)) => {
                        CloudLiveBackend::connect(CloudBackendLaunch {
                            provider,
                            credential,
                            system_instruction: persona.instruction().to_owned(),
                            video_enabled: screen_enabled,
                            native_audio,
                        })
                        .map(|backend| Box::new(backend) as Box<dyn RealtimeBackend>)
                    }
                    _ => Err(crate::backend::BackendError::LocalProtocol),
                },
                RealtimeBackendKind::LocalMiniCpmO45 => match local_omni {
                    Some(launch) => LocalOmniBackend::connect(
                        launch,
                        connect_session_id,
                        connect_segment_id,
                        context_epoch,
                        persona.instruction(),
                        activity_profile,
                        persona.digest().to_owned(),
                    )
                    .map(|backend| Box::new(backend) as Box<dyn RealtimeBackend>),
                    None => Err(crate::backend::BackendError::LocalUnavailable),
                },
            };
            if !connect_cancelled.load(Ordering::Acquire) {
                let _ = backend_sender.send(result);
            }
        })
        .is_err()
    {
        emit_failed(&events, &identity, "WORKER_INTERRUPTED");
        return;
    }
    let backend_deadline = Instant::now() + Duration::from_secs(15);
    let mut active_backend = loop {
        if startup_cancel_requested(&commands, &cancelled) {
            emit_cancelled(&events, &identity);
            return;
        }
        match backend_receiver.recv_timeout(Duration::from_millis(5)) {
            Ok(Ok(backend)) => break backend,
            Ok(Err(error)) => {
                emit_backend_failure(&events, &identity, &error, candidate_emitted);
                return;
            }
            Err(mpsc::RecvTimeoutError::Timeout) if Instant::now() < backend_deadline => {}
            Err(mpsc::RecvTimeoutError::Timeout) => {
                cancelled.store(true, Ordering::Release);
                if identity.backend == RealtimeBackendKind::LocalMiniCpmO45 {
                    emit_sidecar_failure(
                        &events,
                        &identity,
                        "LOCAL_SIDECAR_TIMEOUT",
                        candidate_emitted,
                    );
                }
                emit_failed(&events, &identity, "REALTIME_BACKEND_TIMEOUT");
                return;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                emit_failed(&events, &identity, "WORKER_INTERRUPTED");
                return;
            }
        }
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    let mut microphone = MicrophoneCapture::start().ok();
    let (mut fairy_reference, fairy_reference_error) =
        if voice_output != RealtimeVoiceOutput::TextOnly {
            match ProcessLoopbackCapture::start_process_tree(desktop_host_process_id) {
                Ok(reference) => (Some(reference), None),
                Err(_) => (None, Some("AEC_REFERENCE_UNAVAILABLE".to_owned())),
            }
        } else {
            (None, None)
        };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    let mut game_audio = if application_audio_enabled {
        source_id.and_then(|source_id| ProcessLoopbackCapture::start(source_id).ok())
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    let mut playback = if native_audio && fairy_reference.is_some() {
        AudioPlayback::start().ok()
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    let mut video = if screen_enabled {
        source_id.and_then(|id| VideoCapture::start(id, VIDEO_CAPTURE_FPS).ok())
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    if microphone.is_none() && video.is_none() {
        emit_failed(&events, &identity, "REALTIME_INPUT_UNAVAILABLE");
        return;
    }
    let _ = events.send(WorkerEvent::SessionState {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        status: "active".to_owned(),
        backend: identity.backend,
        cloud_provider: identity.cloud_provider,
        error_code: None,
    });
    emit_media_channel(
        &events,
        &identity,
        "microphone",
        1,
        if microphone.is_some() {
            "active"
        } else {
            "unavailable"
        },
        microphone.is_none().then_some("MICROPHONE_UNAVAILABLE"),
    );
    emit_media_channel(
        &events,
        &identity,
        "selected_window",
        1,
        if video.is_some() {
            "active"
        } else if screen_enabled {
            "unavailable"
        } else {
            "paused"
        },
        (screen_enabled && video.is_none()).then_some("CAPTURE_SOURCE_UNAVAILABLE"),
    );
    emit_media_channel(
        &events,
        &identity,
        "selected_application_audio",
        1,
        if game_audio.is_some() {
            "active"
        } else if application_audio_enabled {
            "unavailable"
        } else {
            "paused"
        },
        (application_audio_enabled && game_audio.is_none())
            .then_some("PROCESS_LOOPBACK_UNAVAILABLE"),
    );
    emit_media_channel(
        &events,
        &identity,
        "fairy_render_reference",
        1,
        if fairy_reference.is_some() {
            "active"
        } else if fairy_reference_error.is_some() {
            "unavailable"
        } else {
            "paused"
        },
        fairy_reference_error.as_deref(),
    );
    emit_media_channel(
        &events,
        &identity,
        "voice_output",
        1,
        if native_audio && playback.is_some() {
            "active"
        } else if native_audio {
            "unavailable"
        } else {
            "paused"
        },
        (native_audio && playback.is_none()).then_some("AUDIO_OUTPUT_UNAVAILABLE"),
    );
    let session_start = Instant::now();
    let mut frame_gate = FrameGate::new(identity.context_epoch);
    let mut audio_processor = RealtimeAudioProcessor::default();
    let mut last_usage_sent = Instant::now();
    let mut last_resource_sample_sent = Instant::now();
    let mut last_media_health_checked = Instant::now();
    // User input gates remain separate from native standby ownership.
    let mut microphone_enabled = initial_microphone_enabled;
    let mut video_enabled = screen_enabled;
    let mut standby = false;
    let mut audio_input_samples = 0_u64;
    let mut audio_output_samples = 0_u64;
    let mut video_frame_count = 0_u64;
    let mut interruption_count = 0_u64;
    let mut tool_call_count = 0_u64;
    let mut event_sequence = 0_u64;
    let mut current_user_utterance = false;
    let mut speech_activity = SpeechActivityArbiter::default();
    let mut active_source_id = source_id;
    let mut source_sequence = 1_u64;
    let mut microphone_sequence = 1_u64;
    let mut video_sequence = 1_u64;
    let mut application_audio_sequence = 1_u64;
    let mut reference_sequence = 1_u64;
    let mut media_privacy_paused = false;
    let mut application_audio_requested = application_audio_enabled;
    let mut resource_policy = RealtimeResourcePolicy::for_level(RealtimeResourceLevel::Normal);
    let mut max_inference_latency_ms = 0_u32;
    let mut frame_processing_failures = 0_u8;
    loop {
        if cancelled.load(Ordering::Acquire) {
            break;
        }
        if session_start.elapsed() >= SESSION_HARD_LIMIT {
            break;
        }
        match commands.try_recv() {
            Ok(RuntimeCommand::Stop) | Err(mpsc::TryRecvError::Disconnected) => break,
            Ok(RuntimeCommand::Text { text }) => {
                if standby {
                    continue;
                }
                frame_gate.note_user_question(elapsed_ms(session_start));
                if let Err(error) = active_backend.push_text(&text) {
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
            }
            Ok(RuntimeCommand::ToolResult {
                call_id,
                public_summary,
            }) => {
                if standby {
                    continue;
                }
                if let Err(error) = active_backend.push_assistance_result(&call_id, &public_summary)
                {
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
            }
            Ok(RuntimeCommand::AssistanceResult {
                request_id,
                public_summary,
                succeeded,
            }) => {
                if let Err(error) =
                    active_backend.push_assistance_result(&request_id, &public_summary)
                {
                    let _ = events.send(WorkerEvent::AssistanceState {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.context_epoch,
                        request_id,
                        status: "failed".to_owned(),
                        error_code: Some(error.public_code().to_owned()),
                    });
                    continue;
                }
                let _ = events.send(WorkerEvent::AssistanceState {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                    request_id,
                    status: if succeeded { "completed" } else { "failed" }.to_owned(),
                    error_code: (!succeeded).then(|| "ASSISTANCE_FAILED".to_owned()),
                });
            }
            Ok(RuntimeCommand::RotateContext {
                next_context_epoch,
                reason,
                carryover,
            }) => {
                let public_summary = carryover.public_summary();
                let valid = identity
                    .context_epoch
                    .checked_add(1)
                    .is_some_and(|next| next == next_context_epoch)
                    && valid_rotation_reason(&reason)
                    && carryover.is_valid()
                    && carryover.session_id == identity.session_id
                    && carryover.current_segment_id == identity.segment_id
                    && carryover.current_context_epoch == identity.context_epoch
                    && carryover.target_segment_id == identity.segment_id
                    && carryover.next_context_epoch == next_context_epoch
                    && carryover.persona_digest == identity.persona_digest
                    && carryover.activity_profile == identity.activity_profile;
                if !valid {
                    identity.context_epoch = next_context_epoch;
                    emit_failed(&events, &identity, "CONTEXT_ROTATION_FAILED");
                    return;
                }
                if let Err(error) =
                    active_backend.rotate_context(next_context_epoch, &reason, &public_summary)
                {
                    identity.context_epoch = next_context_epoch;
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
                if let Some(capture) = microphone.as_ref() {
                    while capture.try_recv().is_some() {}
                }
                if let Some(capture) = fairy_reference.as_mut() {
                    while capture.try_recv().is_some() {}
                }
                if let Some(capture) = game_audio.as_mut() {
                    while capture.try_recv().is_some() {}
                }
                if let Some(mut frame) = video.as_ref().and_then(VideoCapture::take_latest) {
                    frame.jpeg.zeroize();
                }
                if let Some(playback) = playback.as_ref() {
                    playback.clear();
                }
                identity.context_epoch = next_context_epoch;
                frame_gate.reset(next_context_epoch);
                audio_processor.reset();
                current_user_utterance = false;
                speech_activity.reset();
                event_sequence = 0;
                standby = false;
                let _ = events.send(WorkerEvent::ContextRotated {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                    reason,
                });
            }
            Ok(RuntimeCommand::Pause) => {
                if standby {
                    continue;
                }
                if let Err(error) = active_backend.pause() {
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
                standby = true;
                drain_runtime_media(
                    microphone.as_ref(),
                    fairy_reference.as_mut(),
                    game_audio.as_mut(),
                    video.as_ref(),
                    playback.as_ref(),
                    &mut audio_processor,
                );
                frame_gate.reset(identity.context_epoch);
                current_user_utterance = false;
                speech_activity.reset();
                let _ = events.send(WorkerEvent::SessionState {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                    status: "standby".to_owned(),
                    backend: identity.backend,
                    cloud_provider: identity.cloud_provider,
                    error_code: None,
                });
            }
            Ok(RuntimeCommand::Resume { carryover }) => {
                if !standby || identity.backend != RealtimeBackendKind::LocalMiniCpmO45 {
                    emit_failed(&events, &identity, "REALTIME_PROTOCOL_ERROR");
                    return;
                }
                let Some(next_context_epoch) = identity.context_epoch.checked_add(1) else {
                    emit_failed(&events, &identity, "CONTEXT_ROTATION_FAILED");
                    return;
                };
                let valid_carryover = carryover.is_valid()
                    && carryover.session_id == identity.session_id
                    && carryover.current_segment_id == identity.segment_id
                    && carryover.current_context_epoch == identity.context_epoch
                    && carryover.target_segment_id == identity.segment_id
                    && carryover.next_context_epoch == next_context_epoch
                    && carryover.persona_digest == identity.persona_digest
                    && carryover.activity_profile == identity.activity_profile;
                let public_summary = carryover.public_summary();
                if !valid_carryover {
                    identity.context_epoch = next_context_epoch;
                    emit_failed(&events, &identity, "CONTEXT_ROTATION_FAILED");
                    return;
                }
                if let Err(error) = active_backend.resume() {
                    identity.context_epoch = next_context_epoch;
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
                if let Err(error) = active_backend.rotate_context(
                    next_context_epoch,
                    "standby_wake",
                    &public_summary,
                ) {
                    identity.context_epoch = next_context_epoch;
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
                drain_runtime_media(
                    microphone.as_ref(),
                    fairy_reference.as_mut(),
                    game_audio.as_mut(),
                    video.as_ref(),
                    playback.as_ref(),
                    &mut audio_processor,
                );
                identity.context_epoch = next_context_epoch;
                frame_gate.reset(next_context_epoch);
                current_user_utterance = false;
                speech_activity.reset();
                event_sequence = 0;
                standby = false;
                let _ = events.send(WorkerEvent::ContextRotated {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                    reason: "standby_wake".to_owned(),
                });
            }
            Ok(RuntimeCommand::WakeSegment {
                next_segment_id,
                carryover,
            }) => {
                if !standby
                    || identity.backend != RealtimeBackendKind::CloudLive
                    || next_segment_id.trim().is_empty()
                    || next_segment_id.len() > 128
                    || !carryover.is_valid()
                    || carryover.session_id != identity.session_id
                    || carryover.current_segment_id != identity.segment_id
                    || carryover.current_context_epoch != identity.context_epoch
                    || carryover.target_segment_id != next_segment_id
                    || carryover.next_context_epoch != 1
                    || carryover.persona_digest != identity.persona_digest
                    || carryover.activity_profile != identity.activity_profile
                {
                    emit_failed(&events, &identity, "REALTIME_PROTOCOL_ERROR");
                    return;
                }
                let public_summary = carryover.public_summary();
                if let Err(error) =
                    active_backend.rotate_context(1, "standby_wake", &public_summary)
                {
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
                drain_runtime_media(
                    microphone.as_ref(),
                    fairy_reference.as_mut(),
                    game_audio.as_mut(),
                    video.as_ref(),
                    playback.as_ref(),
                    &mut audio_processor,
                );
                identity.segment_id = next_segment_id;
                identity.context_epoch = 1;
                frame_gate.reset(1);
                current_user_utterance = false;
                speech_activity.reset();
                event_sequence = 0;
                standby = false;
                let _ = events.send(WorkerEvent::SegmentWoken {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                });
            }
            Ok(RuntimeCommand::SetInput { microphone, video }) => {
                if video != video_enabled {
                    frame_gate.reset(identity.context_epoch);
                }
                microphone_enabled = microphone;
                video_enabled = video;
            }
            Ok(RuntimeCommand::SetProfile { activity_profile }) => {
                if active_backend
                    .set_activity_profile(activity_profile)
                    .is_err()
                {
                    emit_failed(&events, &identity, "REALTIME_BACKEND_PROTOCOL_FAILED");
                    return;
                }
                identity.activity_profile = activity_profile;
            }
            Ok(RuntimeCommand::SetResourcePolicy { policy }) => {
                if identity.backend != RealtimeBackendKind::LocalMiniCpmO45 || !policy.is_valid() {
                    emit_failed(&events, &identity, "REALTIME_PROTOCOL_ERROR");
                    return;
                }
                resource_policy = policy;
                let _ = frame_gate.apply_resource_policy(policy);
                if policy.media_paused {
                    drain_runtime_media(
                        microphone.as_ref(),
                        fairy_reference.as_mut(),
                        game_audio.as_mut(),
                        video.as_ref(),
                        playback.as_ref(),
                        &mut audio_processor,
                    );
                    current_user_utterance = false;
                    speech_activity.reset();
                }
                let _ = events.send(WorkerEvent::ResourcePolicyApplied {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                    policy,
                });
                if policy.level == RealtimeResourceLevel::DeviceRemoved {
                    let _ = events.send(WorkerEvent::ResourcePressure {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.context_epoch,
                        code: policy.public_code().to_owned(),
                    });
                    emit_failed(&events, &identity, policy.public_code());
                    return;
                }
            }
            Ok(RuntimeCommand::SetMediaPrivacy { paused }) => {
                if paused == media_privacy_paused {
                    continue;
                }
                media_privacy_paused = paused;
                if paused {
                    drain_runtime_media(
                        microphone.as_ref(),
                        fairy_reference.as_mut(),
                        game_audio.as_mut(),
                        video.as_ref(),
                        playback.as_ref(),
                        &mut audio_processor,
                    );
                    video = None;
                    game_audio = None;
                    video_sequence = video_sequence.saturating_add(1);
                    application_audio_sequence = application_audio_sequence.saturating_add(1);
                    emit_media_channel(
                        &events,
                        &identity,
                        "selected_window",
                        video_sequence,
                        "paused",
                        Some("SENSITIVE_WINDOW_BLOCKED"),
                    );
                    emit_media_channel(
                        &events,
                        &identity,
                        "selected_application_audio",
                        application_audio_sequence,
                        "paused",
                        Some("SENSITIVE_WINDOW_BLOCKED"),
                    );
                }
            }
            Ok(RuntimeCommand::ReplaceCaptureSource {
                next_context_epoch,
                source_id,
                source_sequence: next_source_sequence,
                screen_enabled: next_screen_enabled,
                application_audio_enabled: next_application_audio_enabled,
                reason,
                carryover,
            }) => {
                let valid = identity
                    .context_epoch
                    .checked_add(1)
                    .is_some_and(|next| next == next_context_epoch)
                    && next_source_sequence > source_sequence
                    && valid_rotation_reason(&reason)
                    && carryover.is_valid()
                    && carryover.session_id == identity.session_id
                    && carryover.current_segment_id == identity.segment_id
                    && carryover.current_context_epoch == identity.context_epoch
                    && carryover.target_segment_id == identity.segment_id
                    && carryover.next_context_epoch == next_context_epoch
                    && carryover.persona_digest == identity.persona_digest
                    && carryover.activity_profile == identity.activity_profile;
                if !valid {
                    identity.context_epoch = next_context_epoch;
                    emit_failed(&events, &identity, "REALTIME_PROTOCOL_ERROR");
                    return;
                }
                let next_video = next_screen_enabled
                    .then(|| VideoCapture::start(source_id, VIDEO_CAPTURE_FPS).ok())
                    .flatten();
                let next_application_audio = next_application_audio_enabled
                    .then(|| ProcessLoopbackCapture::start(source_id).ok())
                    .flatten();
                if let Err(error) = active_backend.rotate_context(
                    next_context_epoch,
                    &reason,
                    &carryover.public_summary(),
                ) {
                    identity.context_epoch = next_context_epoch;
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
                drain_runtime_media(
                    microphone.as_ref(),
                    fairy_reference.as_mut(),
                    game_audio.as_mut(),
                    video.as_ref(),
                    playback.as_ref(),
                    &mut audio_processor,
                );
                video = next_video;
                game_audio = next_application_audio;
                video_enabled = next_screen_enabled;
                application_audio_requested = next_application_audio_enabled;
                active_source_id = Some(source_id);
                source_sequence = next_source_sequence;
                media_privacy_paused = false;
                identity.context_epoch = next_context_epoch;
                frame_gate.reset(next_context_epoch);
                current_user_utterance = false;
                speech_activity.reset();
                event_sequence = 0;
                video_sequence = video_sequence.saturating_add(1);
                application_audio_sequence = application_audio_sequence.saturating_add(1);
                let _ = events.send(WorkerEvent::ContextRotated {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                    reason,
                });
                let capture_active = !video_enabled || video.is_some();
                let _ = events.send(WorkerEvent::CaptureSourceChanged {
                    session_id: identity.session_id.clone(),
                    segment_id: identity.segment_id.clone(),
                    context_epoch: identity.context_epoch,
                    source_id,
                    source_sequence,
                    status: if capture_active {
                        "active"
                    } else {
                        "unavailable"
                    }
                    .to_owned(),
                    error_code: (!capture_active).then(|| "CAPTURE_SOURCE_UNAVAILABLE".to_owned()),
                });
                emit_media_channel(
                    &events,
                    &identity,
                    "selected_window",
                    video_sequence,
                    if video.is_some() {
                        "active"
                    } else if video_enabled {
                        "unavailable"
                    } else {
                        "paused"
                    },
                    (video_enabled && video.is_none()).then_some("CAPTURE_SOURCE_UNAVAILABLE"),
                );
                emit_media_channel(
                    &events,
                    &identity,
                    "selected_application_audio",
                    application_audio_sequence,
                    if game_audio.is_some() {
                        "active"
                    } else if next_application_audio_enabled {
                        "unavailable"
                    } else {
                        "paused"
                    },
                    (next_application_audio_enabled && game_audio.is_none())
                        .then_some("PROCESS_LOOPBACK_UNAVAILABLE"),
                );
                if microphone.is_none() && video.is_none() {
                    emit_failed(&events, &identity, "REALTIME_INPUT_UNAVAILABLE");
                    return;
                }
            }
            Ok(RuntimeCommand::RetryMediaChannel {
                channel,
                source_sequence: requested_source_sequence,
            }) => {
                if requested_source_sequence != source_sequence {
                    continue;
                }
                match channel.as_str() {
                    "microphone" if microphone.is_none() => {
                        microphone_sequence = microphone_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "microphone",
                            microphone_sequence,
                            "recovering",
                            None,
                        );
                        microphone = MicrophoneCapture::start().ok();
                        microphone_sequence = microphone_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "microphone",
                            microphone_sequence,
                            if microphone.is_some() {
                                "active"
                            } else {
                                "unavailable"
                            },
                            microphone.is_none().then_some("MICROPHONE_UNAVAILABLE"),
                        );
                    }
                    "selected_window" if video.is_none() && !media_privacy_paused => {
                        video_sequence = video_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "selected_window",
                            video_sequence,
                            "recovering",
                            None,
                        );
                        video = active_source_id
                            .and_then(|id| VideoCapture::start(id, VIDEO_CAPTURE_FPS).ok());
                        video_sequence = video_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "selected_window",
                            video_sequence,
                            if video.is_some() {
                                "active"
                            } else {
                                "unavailable"
                            },
                            video.is_none().then_some("CAPTURE_SOURCE_UNAVAILABLE"),
                        );
                    }
                    "selected_application_audio"
                        if game_audio.is_none()
                            && application_audio_requested
                            && !media_privacy_paused =>
                    {
                        application_audio_sequence = application_audio_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "selected_application_audio",
                            application_audio_sequence,
                            "recovering",
                            None,
                        );
                        game_audio =
                            active_source_id.and_then(|id| ProcessLoopbackCapture::start(id).ok());
                        application_audio_sequence = application_audio_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "selected_application_audio",
                            application_audio_sequence,
                            if game_audio.is_some() {
                                "active"
                            } else {
                                "unavailable"
                            },
                            game_audio
                                .is_none()
                                .then_some("PROCESS_LOOPBACK_UNAVAILABLE"),
                        );
                    }
                    "fairy_render_reference"
                        if fairy_reference.is_none()
                            && voice_output != RealtimeVoiceOutput::TextOnly =>
                    {
                        reference_sequence = reference_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "fairy_render_reference",
                            reference_sequence,
                            "recovering",
                            None,
                        );
                        fairy_reference =
                            ProcessLoopbackCapture::start_process_tree(desktop_host_process_id)
                                .ok();
                        if native_audio && fairy_reference.is_some() && playback.is_none() {
                            playback = AudioPlayback::start().ok();
                        }
                        reference_sequence = reference_sequence.saturating_add(1);
                        emit_media_channel(
                            &events,
                            &identity,
                            "fairy_render_reference",
                            reference_sequence,
                            if fairy_reference.is_some() {
                                "active"
                            } else {
                                "unavailable"
                            },
                            fairy_reference
                                .is_none()
                                .then_some("AEC_REFERENCE_UNAVAILABLE"),
                        );
                    }
                    _ => {}
                }
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        if let Some(capture) = fairy_reference.as_mut() {
            while let Some(packet) = capture.try_recv() {
                if standby || resource_policy.media_paused || media_privacy_paused {
                    drop(packet);
                    continue;
                }
                let mut samples = resample_pcm16(&packet.pcm16, packet.sample_rate, 16_000);
                audio_processor.push_render_reference(&samples);
                samples.zeroize();
            }
        }
        let mut application_audio_failed = false;
        if let Some(capture) = game_audio.as_mut() {
            while let Some(packet) = capture.try_recv() {
                if standby
                    || resource_policy.media_paused
                    || media_privacy_paused
                    || (!microphone_enabled && !video_enabled)
                {
                    drop(packet);
                    continue;
                }
                let mut samples = resample_pcm16(&packet.pcm16, packet.sample_rate, 16_000);
                let mut bytes = Vec::with_capacity(samples.len() * 2);
                for sample in &samples {
                    bytes.extend_from_slice(&sample.to_le_bytes());
                }
                samples.zeroize();
                frame_gate.note_audio_activity(elapsed_ms(session_start));
                if active_backend.push_application_audio(&bytes).is_err() {
                    bytes.zeroize();
                    application_audio_failed = true;
                    break;
                }
                bytes.zeroize();
            }
        }
        if application_audio_failed {
            game_audio = None;
            application_audio_sequence = application_audio_sequence.saturating_add(1);
            emit_media_channel(
                &events,
                &identity,
                "selected_application_audio",
                application_audio_sequence,
                "unavailable",
                Some("APPLICATION_AUDIO_CHANNEL_FAILED"),
            );
        }
        if microphone
            .as_ref()
            .is_some_and(MicrophoneCapture::is_failed)
        {
            microphone = None;
            audio_processor.reset();
            speech_activity.reset();
            microphone_sequence = microphone_sequence.saturating_add(1);
            emit_media_channel(
                &events,
                &identity,
                "microphone",
                microphone_sequence,
                "unavailable",
                Some("MICROPHONE_UNAVAILABLE"),
            );
            if video.is_none() {
                emit_failed(&events, &identity, "REALTIME_INPUT_UNAVAILABLE");
                return;
            }
        }
        while let Some(packet) = microphone.as_ref().and_then(MicrophoneCapture::try_recv) {
            if standby || resource_policy.media_paused || !microphone_enabled {
                // Muted or paused: drain and discard without sending. Dropping the
                // packet zeroizes its samples.
                drop(packet);
                continue;
            }
            let mut samples = resample_pcm16(&packet.pcm16, packet.sample_rate, 16_000);
            let processed = audio_processor.process_microphone(&samples);
            samples.zeroize();
            for mut frame in processed {
                if frame.speech_started {
                    frame_gate.note_user_question(elapsed_ms(session_start));
                    if speech_activity.local_started() {
                        interruption_count = interruption_count.saturating_add(1);
                        if let Some(playback) = playback.as_ref() {
                            playback.clear();
                        }
                        let _ = events.send(WorkerEvent::BargeIn {
                            session_id: identity.session_id.clone(),
                            segment_id: identity.segment_id.clone(),
                            context_epoch: identity.context_epoch,
                        });
                        let _ = events.send(WorkerEvent::Presence {
                            session_id: identity.session_id.clone(),
                            segment_id: identity.segment_id.clone(),
                            context_epoch: identity.context_epoch,
                            state: "listening".to_owned(),
                            level: None,
                        });
                    }
                }
                if frame.speech_stopped {
                    if speech_activity.local_stopped() {
                        let _ = events.send(WorkerEvent::Presence {
                            session_id: identity.session_id.clone(),
                            segment_id: identity.segment_id.clone(),
                            context_epoch: identity.context_epoch,
                            state: "analyzing".to_owned(),
                            level: None,
                        });
                    }
                }
                audio_input_samples = audio_input_samples.saturating_add(frame.pcm16.len() as u64);
                let mut bytes = Vec::with_capacity(frame.pcm16.len() * 2);
                for sample in &frame.pcm16 {
                    bytes.extend_from_slice(&sample.to_le_bytes());
                }
                frame.pcm16.zeroize();
                if let Err(error) = active_backend.push_microphone(&bytes) {
                    bytes.zeroize();
                    emit_backend_failure(&events, &identity, &error, candidate_emitted);
                    return;
                }
                bytes.zeroize();
            }
        }
        let now_ms = elapsed_ms(session_start);
        if resource_policy.media_paused || media_privacy_paused {
            if let Some(mut frame) = video.as_ref().and_then(VideoCapture::take_latest) {
                frame.jpeg.zeroize();
            }
        } else if !standby && video_enabled && frame_gate.should_inspect(now_ms) {
            if let Some(mut frame) = video.as_ref().and_then(VideoCapture::take_latest) {
                match frame_gate.inspect(&frame.jpeg, now_ms) {
                    Ok(FrameGateDecision::Send { .. }) => {
                        if let Err(error) = active_backend.push_video(&frame.jpeg) {
                            frame.jpeg.zeroize();
                            emit_backend_failure(&events, &identity, &error, candidate_emitted);
                            return;
                        }
                        video_frame_count = video_frame_count.saturating_add(1);
                    }
                    Ok(FrameGateDecision::SuppressStatic) => {}
                    Err(_) => {
                        frame_processing_failures = frame_processing_failures.saturating_add(1);
                    }
                }
                frame.jpeg.zeroize();
            }
        }
        if last_media_health_checked.elapsed() >= Duration::from_secs(1) {
            let capture_failed = video
                .as_ref()
                .map(VideoCapture::take_health_sample)
                .is_some_and(|(_, failures)| failures >= VIDEO_CAPTURE_FPS as u8);
            if capture_failed {
                video = None;
                game_audio = None;
                video_sequence = video_sequence.saturating_add(1);
                application_audio_sequence = application_audio_sequence.saturating_add(1);
                emit_media_channel(
                    &events,
                    &identity,
                    "selected_window",
                    video_sequence,
                    "unavailable",
                    Some("CAPTURE_SOURCE_UNAVAILABLE"),
                );
                emit_media_channel(
                    &events,
                    &identity,
                    "selected_application_audio",
                    application_audio_sequence,
                    "paused",
                    Some("CAPTURE_SOURCE_UNAVAILABLE"),
                );
                if microphone.is_none() {
                    emit_failed(&events, &identity, "REALTIME_INPUT_UNAVAILABLE");
                    return;
                }
            }
            last_media_health_checked = Instant::now();
        }
        let poll_started = Instant::now();
        let outputs = match (!standby).then(|| active_backend.poll()).transpose() {
            Ok(Some(outputs)) => outputs,
            Ok(None) => Vec::new(),
            Err(error) => {
                emit_backend_failure(&events, &identity, &error, candidate_emitted);
                return;
            }
        };
        max_inference_latency_ms = max_inference_latency_ms
            .max(u32::try_from(poll_started.elapsed().as_millis()).unwrap_or(u32::MAX));
        for output in outputs {
            match output {
                BackendEvent::Ready => {}
                BackendEvent::PerceptionCandidate(candidate) => {
                    if !resource_policy.background_analysis_allowed
                        && !candidate_is_user_initiated(&candidate)
                    {
                        continue;
                    }
                    event_sequence = event_sequence.saturating_add(1);
                    candidate_emitted = true;
                    let _ = events.send(WorkerEvent::PerceptionCandidate {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.context_epoch,
                        sequence: event_sequence,
                        candidate,
                    });
                }
                BackendEvent::Audio(mut bytes) => {
                    audio_output_samples =
                        audio_output_samples.saturating_add((bytes.len() / 2) as u64);
                    // In Fairy voice mode there is no local playback (Fairy speaks
                    // from the transcript captions instead), so the provider audio
                    // is discarded without the cost of decoding it.
                    if let Some(playback) = playback.as_ref() {
                        let mut samples = bytes
                            .chunks_exact(2)
                            .map(|pair| i16::from_le_bytes([pair[0], pair[1]]))
                            .collect::<Vec<_>>();
                        playback.enqueue_pcm16(&samples, 24_000);
                        samples.zeroize();
                    }
                    bytes.zeroize();
                    let _ = events.send(WorkerEvent::Presence {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.context_epoch,
                        state: "speaking".to_owned(),
                        level: None,
                    });
                }
                BackendEvent::PublicCaption {
                    text,
                    stable,
                    speaker,
                } => {
                    event_sequence = event_sequence.saturating_add(1);
                    match speaker {
                        BackendCaptionSpeaker::User => {
                            if stable && !text.trim().is_empty() {
                                current_user_utterance = true;
                            }
                            let _ = events.send(WorkerEvent::PublicCaption {
                                session_id: identity.session_id.clone(),
                                segment_id: identity.segment_id.clone(),
                                context_epoch: identity.context_epoch,
                                sequence: event_sequence,
                                text,
                                stable,
                                speaker: "user".to_owned(),
                            });
                        }
                        BackendCaptionSpeaker::Assistant => {
                            let candidate = match assistant_caption_candidate(
                                &identity,
                                text,
                                stable,
                                current_user_utterance,
                            ) {
                                Ok(candidate) => candidate,
                                Err(error) => {
                                    emit_failed(&events, &identity, error.public_code());
                                    return;
                                }
                            };
                            if !resource_policy.background_analysis_allowed
                                && !candidate_is_user_initiated(&candidate)
                            {
                                continue;
                            }
                            candidate_emitted = true;
                            let _ = events.send(WorkerEvent::PerceptionCandidate {
                                session_id: identity.session_id.clone(),
                                segment_id: identity.segment_id.clone(),
                                context_epoch: identity.context_epoch,
                                sequence: event_sequence,
                                candidate,
                            });
                            if stable {
                                current_user_utterance = false;
                            }
                        }
                    }
                }
                BackendEvent::SpeechStarted => {
                    frame_gate.note_user_question(elapsed_ms(session_start));
                    if speech_activity.backend_started() {
                        interruption_count = interruption_count.saturating_add(1);
                        if let Some(playback) = playback.as_ref() {
                            playback.clear();
                        }
                        let _ = events.send(WorkerEvent::BargeIn {
                            session_id: identity.session_id.clone(),
                            segment_id: identity.segment_id.clone(),
                            context_epoch: identity.context_epoch,
                        });
                        let _ = events.send(WorkerEvent::Presence {
                            session_id: identity.session_id.clone(),
                            segment_id: identity.segment_id.clone(),
                            context_epoch: identity.context_epoch,
                            state: "listening".to_owned(),
                            level: None,
                        });
                    }
                }
                BackendEvent::SpeechStopped => {
                    if speech_activity.backend_stopped() {
                        let _ = events.send(WorkerEvent::Presence {
                            session_id: identity.session_id.clone(),
                            segment_id: identity.segment_id.clone(),
                            context_epoch: identity.context_epoch,
                            state: "analyzing".to_owned(),
                            level: None,
                        });
                    }
                }
                BackendEvent::ToolCall {
                    call_id,
                    name,
                    arguments: _,
                } => {
                    tool_call_count = tool_call_count.saturating_add(1);
                    let _ = events.send(WorkerEvent::ToolRequest {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.context_epoch,
                        call_id,
                        tool_name: name,
                        public_intent: "Fairy wants to use a companion tool".to_owned(),
                    });
                }
                BackendEvent::Usage(_) => {}
                BackendEvent::GoAway => {
                    emit_failed(&events, &identity, "REALTIME_PROVIDER_GOING_AWAY");
                    return;
                }
            }
        }
        if identity.backend == RealtimeBackendKind::LocalMiniCpmO45
            && last_resource_sample_sent.elapsed() >= Duration::from_secs(1)
        {
            let (capture_frame_backlog, capture_failures) = video
                .as_ref()
                .map(VideoCapture::take_health_sample)
                .unwrap_or((0, 0));
            let _ = events.send(WorkerEvent::ResourceSample {
                session_id: identity.session_id.clone(),
                segment_id: identity.segment_id.clone(),
                context_epoch: identity.context_epoch,
                allocation_failure_count: frame_processing_failures
                    .saturating_add(capture_failures),
                inference_latency_ms: max_inference_latency_ms,
                capture_frame_backlog,
                renderer_healthy: true,
                target_changed: false,
            });
            frame_processing_failures = 0;
            max_inference_latency_ms = 0;
            last_resource_sample_sent = Instant::now();
        }
        if last_usage_sent.elapsed() >= Duration::from_secs(5) {
            emit_usage(
                &events,
                &identity,
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
    let _ = active_backend.stop();
    emit_usage(
        &events,
        &identity,
        audio_input_samples,
        audio_output_samples,
        video_frame_count,
        interruption_count,
        tool_call_count,
    );
    let _ = events.send(WorkerEvent::SessionState {
        session_id: identity.session_id,
        segment_id: identity.segment_id,
        context_epoch: identity.context_epoch,
        status: "completed".to_owned(),
        backend: identity.backend,
        cloud_provider: identity.cloud_provider,
        error_code: None,
    });
}

fn elapsed_ms(started_at: Instant) -> u64 {
    started_at.elapsed().as_millis().min(u128::from(u64::MAX)) as u64
}

#[derive(Default)]
struct SpeechActivityArbiter {
    local_active: bool,
    backend_active: bool,
    barge_in_announced: bool,
}

impl SpeechActivityArbiter {
    fn local_started(&mut self) -> bool {
        self.local_active = true;
        self.announce_once()
    }

    fn local_stopped(&mut self) -> bool {
        self.local_active = false;
        self.finish_if_inactive()
    }

    fn backend_started(&mut self) -> bool {
        self.backend_active = true;
        self.announce_once()
    }

    fn backend_stopped(&mut self) -> bool {
        self.backend_active = false;
        self.finish_if_inactive()
    }

    fn reset(&mut self) {
        *self = Self::default();
    }

    fn announce_once(&mut self) -> bool {
        if self.barge_in_announced {
            return false;
        }
        self.barge_in_announced = true;
        true
    }

    fn finish_if_inactive(&mut self) -> bool {
        if self.local_active || self.backend_active {
            return false;
        }
        let had_utterance = self.barge_in_announced;
        self.barge_in_announced = false;
        had_utterance
    }
}

fn drain_runtime_media(
    microphone: Option<&MicrophoneCapture>,
    fairy_reference: Option<&mut ProcessLoopbackCapture>,
    application_audio: Option<&mut ProcessLoopbackCapture>,
    video: Option<&VideoCapture>,
    playback: Option<&AudioPlayback>,
    audio_processor: &mut RealtimeAudioProcessor,
) {
    if let Some(capture) = microphone {
        while capture.try_recv().is_some() {}
    }
    if let Some(capture) = fairy_reference {
        while capture.try_recv().is_some() {}
    }
    if let Some(capture) = application_audio {
        while capture.try_recv().is_some() {}
    }
    if let Some(mut frame) = video.and_then(VideoCapture::take_latest) {
        frame.jpeg.zeroize();
    }
    if let Some(playback) = playback {
        playback.clear();
    }
    audio_processor.reset();
}

fn emit_media_channel(
    events: &mpsc::Sender<WorkerEvent>,
    identity: &RuntimeIdentity,
    channel: &str,
    sequence: u64,
    status: &str,
    error_code: Option<&str>,
) {
    let _ = events.send(WorkerEvent::MediaChannelState {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        channel: channel.to_owned(),
        sequence,
        status: status.to_owned(),
        error_code: error_code.map(str::to_owned),
    });
}

fn application_audio_scope_supported(
    backend: RealtimeBackendKind,
    application_audio_enabled: bool,
) -> bool {
    !application_audio_enabled || backend == RealtimeBackendKind::LocalMiniCpmO45
}

fn valid_rotation_reason(reason: &str) -> bool {
    matches!(
        reason,
        "privacy_resume"
            | "profile_changed"
            | "window_changed"
            | "standby_wake"
            | "context_budget"
            | "task_changed"
            | "manual"
    )
}

fn candidate_is_user_initiated(candidate: &RealtimeDialogueCandidate) -> bool {
    candidate.response_to_user
        || candidate
            .grounding
            .iter()
            .any(|grounding| grounding == "current_user_utterance")
}

fn assistant_caption_candidate(
    identity: &RuntimeIdentity,
    text: String,
    stable: bool,
    response_to_user: bool,
) -> Result<RealtimeDialogueCandidate, crate::backend::BackendError> {
    let candidate = RealtimeDialogueCandidate {
        decision: RealtimeCandidateDecision::Speak,
        activity: identity.activity_profile,
        confidence: 1.0,
        intent: if response_to_user {
            "answer".to_owned()
        } else {
            "comment".to_owned()
        },
        grounding: if response_to_user {
            vec!["current_user_utterance".to_owned()]
        } else {
            Vec::new()
        },
        text,
        urgency: 0.0,
        needs_online_assistance: false,
        response_to_user,
        stable,
        persona_digest: identity.persona_digest.clone(),
    };
    candidate
        .is_valid()
        .then_some(candidate)
        .ok_or(crate::backend::BackendError::DialogueProtocol)
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
            Ok(RuntimeCommand::Text { .. })
            | Ok(RuntimeCommand::ToolResult { .. })
            | Ok(RuntimeCommand::AssistanceResult { .. })
            | Ok(RuntimeCommand::SetInput { .. })
            | Ok(RuntimeCommand::SetProfile { .. })
            | Ok(RuntimeCommand::SetResourcePolicy { .. })
            | Ok(RuntimeCommand::SetMediaPrivacy { .. })
            | Ok(RuntimeCommand::ReplaceCaptureSource { .. })
            | Ok(RuntimeCommand::RetryMediaChannel { .. })
            | Ok(RuntimeCommand::RotateContext { .. })
            | Ok(RuntimeCommand::Pause)
            | Ok(RuntimeCommand::Resume { .. })
            | Ok(RuntimeCommand::WakeSegment { .. }) => {}
            Err(mpsc::TryRecvError::Empty) => return false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cloud_application_audio_never_enters_the_microphone_transport() {
        assert!(application_audio_scope_supported(
            RealtimeBackendKind::CloudLive,
            false,
        ));
        assert!(!application_audio_scope_supported(
            RealtimeBackendKind::CloudLive,
            true,
        ));
        assert!(application_audio_scope_supported(
            RealtimeBackendKind::LocalMiniCpmO45,
            true,
        ));
    }

    #[test]
    fn session_hard_limit_backstops_above_the_max_renderer_setting() {
        // The Presence maximum is 240 minutes; the worker ceiling sits above it
        // so the renderer normally stops first and this only catches a runaway.
        assert!(SESSION_HARD_LIMIT > Duration::from_secs(240 * 60));
    }

    #[test]
    fn capture_rate_stays_inside_the_frozen_latest_frame_range() {
        assert!((10..=15).contains(&VIDEO_CAPTURE_FPS));
    }

    #[test]
    fn local_backend_failure_emits_safe_sidecar_signal_before_terminal_state() {
        let (events, received) = mpsc::channel();
        let identity = RuntimeIdentity {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            backend: RealtimeBackendKind::LocalMiniCpmO45,
            cloud_provider: None,
            persona_digest: "a".repeat(64),
            activity_profile: RealtimeActivityProfile::Focus,
        };
        emit_backend_failure(
            &events,
            &identity,
            &crate::backend::BackendError::LocalProtocol,
            true,
        );
        assert_eq!(
            received.recv().expect("sidecar failure"),
            WorkerEvent::LocalSidecarFailure {
                session_id: "session-1".to_owned(),
                segment_id: "segment-1".to_owned(),
                context_epoch: 1,
                error_code: "LOCAL_SIDECAR_PROTOCOL_DISCONNECTED".to_owned(),
                candidate_emitted: true,
            }
        );
        assert!(matches!(
            received.recv().expect("terminal state"),
            WorkerEvent::SessionState { status, .. } if status == "failed"
        ));
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

    #[test]
    fn local_and_provider_vad_share_one_barge_in_lifecycle() {
        let mut activity = SpeechActivityArbiter::default();
        assert!(activity.local_started());
        assert!(!activity.backend_started());
        assert!(!activity.local_stopped());
        assert!(activity.backend_stopped());
        assert!(activity.backend_started());
        activity.reset();
        assert!(activity.local_started());
    }

    #[test]
    fn cloud_assistant_caption_becomes_a_persona_bound_candidate() {
        let identity = RuntimeIdentity {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            backend: RealtimeBackendKind::CloudLive,
            cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
            persona_digest: "a".repeat(64),
            activity_profile: RealtimeActivityProfile::Focus,
        };
        let candidate = assistant_caption_candidate(&identity, "Answer.".to_owned(), true, true)
            .expect("candidate");
        assert!(candidate.is_valid());
        assert!(candidate.response_to_user);
        assert_eq!(candidate.grounding, vec!["current_user_utterance"]);
        assert_eq!(candidate.intent, "answer");
        assert_eq!(candidate.activity, RealtimeActivityProfile::Focus);
        assert_eq!(candidate.persona_digest, "a".repeat(64));
    }

    #[test]
    fn unsolicited_cloud_caption_does_not_invent_grounding() {
        let identity = RuntimeIdentity {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            backend: RealtimeBackendKind::CloudLive,
            cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
            persona_digest: "a".repeat(64),
            activity_profile: RealtimeActivityProfile::Auto,
        };
        let candidate = assistant_caption_candidate(&identity, "Maybe.".to_owned(), false, false)
            .expect("candidate");
        assert!(candidate.is_valid());
        assert!(!candidate.response_to_user);
        assert!(candidate.grounding.is_empty());
    }
}
