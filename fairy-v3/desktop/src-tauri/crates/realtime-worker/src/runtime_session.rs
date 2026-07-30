use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

use zeroize::Zeroize;

use crate::audio_processing::{RealtimeAudioProcessor, RealtimeMicrophoneProcessor};
use crate::backend::{
    BackendCaptionSpeaker, BackendEvent, RealtimeBackendKind, RealtimeVoiceOutput,
};
use crate::frame_gate::{FrameGate, FrameGateDecision};
use crate::media::{
    resample_pcm16, AudioPlayback, MicrophoneCapture, ProcessLoopbackCapture, VideoCapture,
};
use crate::protocol::{
    RealtimeResourceLevel, RealtimeResourcePolicy, RealtimeStartupStage, WorkerEvent,
};
use crate::runtime::*;
use crate::runtime_events::{emit_backend_failure, emit_cancelled, emit_failed, emit_usage};
pub(super) fn run_session(
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
        segment_woken_on_start,
        initial_context_summary,
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
    emit_startup_stage(
        &events,
        &identity,
        RealtimeStartupStage::StartingBackendRuntime,
    );
    let Some(mut active_backend) = connect_realtime_backend(
        &identity,
        credential,
        local_omni,
        persona,
        screen_enabled,
        native_audio,
        initial_context_summary,
        &commands,
        &events,
        &cancelled,
    ) else {
        return;
    };
    if startup_cancel_requested(&commands, &cancelled) {
        emit_cancelled(&events, &identity);
        return;
    }
    emit_startup_stage(
        &events,
        &identity,
        RealtimeStartupStage::AcquiringMicrophone,
    );
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
    if screen_enabled {
        emit_startup_stage(
            &events,
            &identity,
            RealtimeStartupStage::AcquiringObservedWindow,
        );
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
    if application_audio_enabled {
        emit_startup_stage(
            &events,
            &identity,
            RealtimeStartupStage::AcquiringApplicationAudio,
        );
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
    if microphone.is_none() && video.is_none() {
        emit_failed(&events, &identity, "REALTIME_INPUT_UNAVAILABLE");
        return;
    }
    emit_startup_stage(&events, &identity, RealtimeStartupStage::Active);
    let _ = events.send(WorkerEvent::SessionState {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        status: "active".to_owned(),
        backend: identity.backend,
        cloud_provider: identity.cloud_provider,
        error_code: None,
    });
    if segment_woken_on_start {
        let _ = events.send(WorkerEvent::SegmentWoken {
            session_id: identity.session_id.clone(),
            segment_id: identity.segment_id.clone(),
            context_epoch: identity.context_epoch,
        });
    }
    emit_initial_media_state(
        &events,
        &identity,
        microphone.is_some(),
        screen_enabled,
        video.is_some(),
        application_audio_enabled,
        game_audio.is_some(),
        fairy_reference.is_some(),
        fairy_reference_error.as_deref(),
        native_audio,
        playback.is_some(),
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
    let mut local_backend_unloaded = false;
    loop {
        if cancelled.load(Ordering::Acquire) {
            break;
        }
        if session_start.elapsed() >= SESSION_HARD_LIMIT {
            break;
        }
        match commands.try_recv() {
            Ok(RuntimeCommand::Stop) | Err(mpsc::TryRecvError::Disconnected) => break,
            Ok(RuntimeCommand::UnloadLocalBackend) => {
                if identity.backend != RealtimeBackendKind::LocalMiniCpmO45 || !standby {
                    emit_failed(&events, &identity, "REALTIME_PROTOCOL_ERROR");
                    return;
                }
                local_backend_unloaded = true;
                break;
            }
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
                if frame.speech_stopped && speech_activity.local_stopped() {
                    let _ = events.send(WorkerEvent::Presence {
                        session_id: identity.session_id.clone(),
                        segment_id: identity.segment_id.clone(),
                        context_epoch: identity.context_epoch,
                        state: "analyzing".to_owned(),
                        level: None,
                    });
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
    if local_backend_unloaded {
        let _ = events.send(WorkerEvent::LocalBackendUnloaded {
            session_id: identity.session_id,
            segment_id: identity.segment_id,
            context_epoch: identity.context_epoch,
        });
    } else {
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
}

fn emit_startup_stage(
    events: &mpsc::Sender<WorkerEvent>,
    identity: &RuntimeIdentity,
    stage: RealtimeStartupStage,
) {
    let _ = events.send(WorkerEvent::StartupStage {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        stage,
    });
}
