use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};

use zeroize::{Zeroize, Zeroizing};

use crate::audio_processing::{RealtimeAudioProcessor, RealtimeMicrophoneProcessor};
use crate::backend::{
    CloudBackendLaunch, CloudLiveBackend, LocalOmniBackend, LocalOmniLaunch,
    RealtimeActivityProfile, RealtimeBackend, RealtimeBackendKind, RealtimeCandidateDecision,
    RealtimeCloudProviderKind, RealtimeDialogueCandidate, RealtimeVoiceOutput,
};
use crate::media::{AudioPlayback, MicrophoneCapture, ProcessLoopbackCapture, VideoCapture};
use crate::protocol::{RealtimeContextCarryover, RealtimeResourcePolicy, WorkerEvent};
use crate::runtime_events::{
    emit_backend_failure, emit_cancelled, emit_failed, emit_sidecar_failure,
};
use crate::runtime_session::run_session;
use crate::ValidatedRealtimePersona;

// Absolute worker-side ceiling as defense in depth above the Coordinator-owned
// native Presence limit.
pub(super) const SESSION_HARD_LIMIT: Duration = Duration::from_secs(250 * 60);
pub(super) const VIDEO_CAPTURE_FPS: u16 = 15;

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
    pub segment_woken_on_start: bool,
    pub initial_context_summary: Option<String>,
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
    UnloadLocalBackend,
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

    pub fn unload_local_backend(&mut self) -> bool {
        if self
            .commands
            .send(RuntimeCommand::UnloadLocalBackend)
            .is_err()
        {
            return false;
        }
        if let Some(worker) = self.worker.take() {
            worker.join().is_ok()
        } else {
            false
        }
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

#[allow(clippy::too_many_arguments)]
pub(super) fn connect_realtime_backend(
    identity: &RuntimeIdentity,
    credential: Option<Zeroizing<String>>,
    local_omni: Option<LocalOmniLaunch>,
    persona: ValidatedRealtimePersona,
    screen_enabled: bool,
    native_audio: bool,
    initial_context_summary: Option<String>,
    commands: &mpsc::Receiver<RuntimeCommand>,
    events: &mpsc::Sender<WorkerEvent>,
    cancelled: &Arc<AtomicBool>,
) -> Option<Box<dyn RealtimeBackend>> {
    let (backend_sender, backend_receiver) = mpsc::sync_channel(1);
    let connect_cancelled = Arc::clone(cancelled);
    let connect_provider = identity.cloud_provider;
    let connect_backend = identity.backend;
    let connect_session_id = identity.session_id.clone();
    let connect_segment_id = identity.segment_id.clone();
    let context_epoch = identity.context_epoch;
    let activity_profile = identity.activity_profile;
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
                    Some(launch) => {
                        let instruction = instruction_with_carryover(
                            persona.instruction(),
                            initial_context_summary.as_deref(),
                        );
                        LocalOmniBackend::connect(
                            launch,
                            connect_session_id,
                            connect_segment_id,
                            context_epoch,
                            &instruction,
                            activity_profile,
                            persona.digest().to_owned(),
                        )
                        .map(|backend| Box::new(backend) as Box<dyn RealtimeBackend>)
                    }
                    None => Err(crate::backend::BackendError::LocalUnavailable),
                },
            };
            if !connect_cancelled.load(Ordering::Acquire) {
                let _ = backend_sender.send(result);
            }
        })
        .is_err()
    {
        emit_failed(events, identity, "WORKER_INTERRUPTED");
        return None;
    }
    let backend_deadline = Instant::now() + Duration::from_secs(15);
    loop {
        if startup_cancel_requested(commands, cancelled) {
            emit_cancelled(events, identity);
            return None;
        }
        match backend_receiver.recv_timeout(Duration::from_millis(5)) {
            Ok(Ok(backend)) => return Some(backend),
            Ok(Err(error)) => {
                emit_backend_failure(events, identity, &error, false);
                return None;
            }
            Err(mpsc::RecvTimeoutError::Timeout) if Instant::now() < backend_deadline => {}
            Err(mpsc::RecvTimeoutError::Timeout) => {
                cancelled.store(true, Ordering::Release);
                if identity.backend == RealtimeBackendKind::LocalMiniCpmO45 {
                    emit_sidecar_failure(events, identity, "LOCAL_SIDECAR_TIMEOUT", false);
                }
                emit_failed(events, identity, "REALTIME_BACKEND_TIMEOUT");
                return None;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                emit_failed(events, identity, "WORKER_INTERRUPTED");
                return None;
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn emit_initial_media_state(
    events: &mpsc::Sender<WorkerEvent>,
    identity: &RuntimeIdentity,
    microphone_available: bool,
    screen_enabled: bool,
    video_available: bool,
    application_audio_enabled: bool,
    application_audio_available: bool,
    reference_available: bool,
    reference_error: Option<&str>,
    native_audio: bool,
    playback_available: bool,
) {
    emit_media_channel(
        events,
        identity,
        "microphone",
        1,
        if microphone_available {
            "active"
        } else {
            "unavailable"
        },
        (!microphone_available).then_some("MICROPHONE_UNAVAILABLE"),
    );
    emit_media_channel(
        events,
        identity,
        "selected_window",
        1,
        if video_available {
            "active"
        } else if screen_enabled {
            "unavailable"
        } else {
            "paused"
        },
        (screen_enabled && !video_available).then_some("CAPTURE_SOURCE_UNAVAILABLE"),
    );
    emit_media_channel(
        events,
        identity,
        "selected_application_audio",
        1,
        if application_audio_available {
            "active"
        } else if application_audio_enabled {
            "unavailable"
        } else {
            "paused"
        },
        (application_audio_enabled && !application_audio_available)
            .then_some("PROCESS_LOOPBACK_UNAVAILABLE"),
    );
    emit_media_channel(
        events,
        identity,
        "fairy_render_reference",
        1,
        if reference_available {
            "active"
        } else if reference_error.is_some() {
            "unavailable"
        } else {
            "paused"
        },
        reference_error,
    );
    emit_media_channel(
        events,
        identity,
        "voice_output",
        1,
        if native_audio && playback_available {
            "active"
        } else if native_audio {
            "unavailable"
        } else {
            "paused"
        },
        (native_audio && !playback_available).then_some("AUDIO_OUTPUT_UNAVAILABLE"),
    );
}

pub(super) fn elapsed_ms(started_at: Instant) -> u64 {
    started_at.elapsed().as_millis().min(u128::from(u64::MAX)) as u64
}

pub(super) fn instruction_with_carryover(base: &str, public_summary: Option<&str>) -> String {
    let summary = public_summary.unwrap_or_default().trim();
    if summary.is_empty() {
        return base.to_owned();
    }
    format!("{base}\n\nBounded prior-session context:\n{summary}")
}

#[derive(Default)]
pub(super) struct SpeechActivityArbiter {
    local_active: bool,
    backend_active: bool,
    barge_in_announced: bool,
}

impl SpeechActivityArbiter {
    pub(super) fn local_started(&mut self) -> bool {
        self.local_active = true;
        self.announce_once()
    }

    pub(super) fn local_stopped(&mut self) -> bool {
        self.local_active = false;
        self.finish_if_inactive()
    }

    pub(super) fn backend_started(&mut self) -> bool {
        self.backend_active = true;
        self.announce_once()
    }

    pub(super) fn backend_stopped(&mut self) -> bool {
        self.backend_active = false;
        self.finish_if_inactive()
    }

    pub(super) fn reset(&mut self) {
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

pub(super) fn drain_runtime_media(
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

pub(super) fn emit_media_channel(
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

pub(super) fn application_audio_scope_supported(
    backend: RealtimeBackendKind,
    application_audio_enabled: bool,
) -> bool {
    !application_audio_enabled || backend == RealtimeBackendKind::LocalMiniCpmO45
}

pub(super) fn valid_rotation_reason(reason: &str) -> bool {
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

pub(super) fn candidate_is_user_initiated(candidate: &RealtimeDialogueCandidate) -> bool {
    candidate.response_to_user
        || candidate
            .grounding
            .iter()
            .any(|grounding| grounding == "current_user_utterance")
}

pub(super) fn assistant_caption_candidate(
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

pub(super) fn startup_cancel_requested(
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
            | Ok(RuntimeCommand::UnloadLocalBackend)
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
