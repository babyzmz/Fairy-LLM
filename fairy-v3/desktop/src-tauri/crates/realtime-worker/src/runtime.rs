use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};

use zeroize::{Zeroize, Zeroizing};

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
    pub source_id: Option<u64>,
    pub microphone_enabled: bool,
    pub screen_enabled: bool,
    pub application_audio_enabled: bool,
    pub voice_output: RealtimeVoiceOutput,
}

#[derive(Clone, Debug)]
struct RuntimeIdentity {
    session_id: String,
    segment_id: String,
    context_epoch: u64,
    backend: RealtimeBackendKind,
    cloud_provider: Option<RealtimeCloudProviderKind>,
    persona_digest: String,
    activity_profile: RealtimeActivityProfile,
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
                emit_failed(&events, &identity, error.public_code());
                return;
            }
            Err(mpsc::RecvTimeoutError::Timeout) if Instant::now() < backend_deadline => {}
            Err(mpsc::RecvTimeoutError::Timeout) => {
                cancelled.store(true, Ordering::Release);
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
    let microphone = match MicrophoneCapture::start() {
        Ok(microphone) => microphone,
        Err(_) => {
            emit_failed(&events, &identity, "MICROPHONE_UNAVAILABLE");
            return;
        }
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    let mut game_audio = if application_audio_enabled {
        match source_id.and_then(|source_id| ProcessLoopbackCapture::start(source_id).ok()) {
            Some(capture) => Some(capture),
            None => {
                emit_failed(&events, &identity, "PROCESS_LOOPBACK_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    let playback = if native_audio {
        match AudioPlayback::start() {
            Ok(playback) => Some(playback),
            Err(_) => {
                emit_failed(&events, &identity, "AUDIO_OUTPUT_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    let video = if screen_enabled {
        match source_id.and_then(|id| VideoCapture::start(id, VIDEO_CAPTURE_FPS).ok()) {
            Some(video) => Some(video),
            None => {
                emit_failed(&events, &identity, "CAPTURE_SOURCE_UNAVAILABLE");
                return;
            }
        }
    } else {
        None
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
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
    let session_start = Instant::now();
    let mut frame_gate = FrameGate::new(identity.context_epoch);
    let mut last_usage_sent = Instant::now();
    let mut last_resource_sample_sent = Instant::now();
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
                    emit_failed(&events, &identity, error.public_code());
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
                    emit_failed(&events, &identity, error.public_code());
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
                if !valid
                    || active_backend
                        .rotate_context(next_context_epoch, &reason, &public_summary)
                        .is_err()
                {
                    identity.context_epoch = next_context_epoch;
                    emit_failed(&events, &identity, "CONTEXT_ROTATION_FAILED");
                    return;
                }
                while microphone.try_recv().is_some() {}
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
                current_user_utterance = false;
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
                    emit_failed(&events, &identity, error.public_code());
                    return;
                }
                standby = true;
                drain_runtime_media(
                    &microphone,
                    game_audio.as_mut(),
                    video.as_ref(),
                    playback.as_ref(),
                );
                frame_gate.reset(identity.context_epoch);
                current_user_utterance = false;
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
                if !valid_carryover
                    || active_backend.resume().is_err()
                    || active_backend
                        .rotate_context(next_context_epoch, "standby_wake", &public_summary)
                        .is_err()
                {
                    identity.context_epoch = next_context_epoch;
                    emit_failed(&events, &identity, "CONTEXT_ROTATION_FAILED");
                    return;
                }
                drain_runtime_media(
                    &microphone,
                    game_audio.as_mut(),
                    video.as_ref(),
                    playback.as_ref(),
                );
                identity.context_epoch = next_context_epoch;
                frame_gate.reset(next_context_epoch);
                current_user_utterance = false;
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
                    emit_failed(&events, &identity, error.public_code());
                    return;
                }
                drain_runtime_media(
                    &microphone,
                    game_audio.as_mut(),
                    video.as_ref(),
                    playback.as_ref(),
                );
                identity.segment_id = next_segment_id;
                identity.context_epoch = 1;
                frame_gate.reset(1);
                current_user_utterance = false;
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
                        &microphone,
                        game_audio.as_mut(),
                        video.as_ref(),
                        playback.as_ref(),
                    );
                    current_user_utterance = false;
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
            Err(mpsc::TryRecvError::Empty) => {}
        }
        if let Some(capture) = game_audio.as_mut() {
            while let Some(packet) = capture.try_recv() {
                if standby
                    || resource_policy.media_paused
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
                if let Err(error) = active_backend.push_application_audio(&bytes) {
                    bytes.zeroize();
                    emit_failed(&events, &identity, error.public_code());
                    return;
                }
                bytes.zeroize();
            }
        }
        while let Some(packet) = microphone.try_recv() {
            if standby || resource_policy.media_paused || !microphone_enabled {
                // Muted or paused: drain and discard without sending. Dropping the
                // packet zeroizes its samples.
                drop(packet);
                continue;
            }
            let mut samples = resample_pcm16(&packet.pcm16, packet.sample_rate, 16_000);
            audio_input_samples = audio_input_samples.saturating_add(samples.len() as u64);
            let mut bytes = Vec::with_capacity(samples.len() * 2);
            for sample in &samples {
                bytes.extend_from_slice(&sample.to_le_bytes());
            }
            samples.zeroize();
            if let Err(error) = active_backend.push_microphone(&bytes) {
                bytes.zeroize();
                emit_failed(&events, &identity, error.public_code());
                return;
            }
            bytes.zeroize();
        }
        let now_ms = elapsed_ms(session_start);
        if resource_policy.media_paused {
            if let Some(mut frame) = video.as_ref().and_then(VideoCapture::take_latest) {
                frame.jpeg.zeroize();
            }
        } else if !standby && video_enabled && frame_gate.should_inspect(now_ms) {
            if let Some(mut frame) = video.as_ref().and_then(VideoCapture::take_latest) {
                match frame_gate.inspect(&frame.jpeg, now_ms) {
                    Ok(FrameGateDecision::Send { .. }) => {
                        if let Err(error) = active_backend.push_video(&frame.jpeg) {
                            frame.jpeg.zeroize();
                            emit_failed(&events, &identity, error.public_code());
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
        let poll_started = Instant::now();
        let outputs = match (!standby).then(|| active_backend.poll()).transpose() {
            Ok(Some(outputs)) => outputs,
            Ok(None) => Vec::new(),
            Err(error) => {
                emit_failed(&events, &identity, error.public_code());
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
                BackendEvent::SpeechStopped => {
                    let _ = events.send(WorkerEvent::Presence {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.context_epoch,
                        state: "analyzing".to_owned(),
                        level: None,
                    });
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

fn drain_runtime_media(
    microphone: &MicrophoneCapture,
    application_audio: Option<&mut ProcessLoopbackCapture>,
    video: Option<&VideoCapture>,
    playback: Option<&AudioPlayback>,
) {
    while microphone.try_recv().is_some() {}
    if let Some(capture) = application_audio {
        while capture.try_recv().is_some() {}
    }
    if let Some(mut frame) = video.and_then(VideoCapture::take_latest) {
        frame.jpeg.zeroize();
    }
    if let Some(playback) = playback {
        playback.clear();
    }
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
            | Ok(RuntimeCommand::RotateContext { .. })
            | Ok(RuntimeCommand::Pause)
            | Ok(RuntimeCommand::Resume { .. })
            | Ok(RuntimeCommand::WakeSegment { .. }) => {}
            Err(mpsc::TryRecvError::Empty) => return false,
        }
    }
}

fn emit_usage(
    events: &mpsc::Sender<WorkerEvent>,
    identity: &RuntimeIdentity,
    audio_input_samples: u64,
    audio_output_samples: u64,
    video_frame_count: u64,
    interruption_count: u64,
    tool_call_count: u64,
) {
    let _ = events.send(WorkerEvent::Usage {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        audio_input_ms: audio_input_samples.saturating_mul(1_000) / 16_000,
        audio_output_ms: audio_output_samples.saturating_mul(1_000) / 24_000,
        video_frame_count,
        interruption_count,
        tool_call_count,
    });
}

fn emit_failed(
    events: &mpsc::Sender<WorkerEvent>,
    identity: &RuntimeIdentity,
    error_code: &'static str,
) {
    let _ = events.send(WorkerEvent::SessionState {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        status: "failed".to_owned(),
        backend: identity.backend,
        cloud_provider: identity.cloud_provider,
        error_code: Some(error_code.to_owned()),
    });
}

fn emit_cancelled(events: &mpsc::Sender<WorkerEvent>, identity: &RuntimeIdentity) {
    let _ = events.send(WorkerEvent::SessionState {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        status: "cancelled".to_owned(),
        backend: identity.backend,
        cloud_provider: identity.cloud_provider,
        error_code: None,
    });
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
