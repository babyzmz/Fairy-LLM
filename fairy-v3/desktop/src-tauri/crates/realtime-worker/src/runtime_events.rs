use std::sync::mpsc;

use crate::backend::{BackendError, RealtimeBackendKind};
use crate::protocol::WorkerEvent;
use crate::runtime::RuntimeIdentity;

pub(super) fn emit_usage(
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

pub(super) fn emit_failed(
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

pub(super) fn emit_backend_failure(
    events: &mpsc::Sender<WorkerEvent>,
    identity: &RuntimeIdentity,
    error: &BackendError,
    candidate_emitted: bool,
) {
    if identity.backend == RealtimeBackendKind::LocalMiniCpmO45 {
        let sidecar_code = match error {
            BackendError::LocalUnavailable => Some("LOCAL_SIDECAR_START_FAILED"),
            BackendError::LocalProtocol => Some("LOCAL_SIDECAR_PROTOCOL_DISCONNECTED"),
            BackendError::LocalTimeout => Some("LOCAL_SIDECAR_TIMEOUT"),
            BackendError::LocalIo(_) => Some("LOCAL_SIDECAR_PROCESS_EXIT"),
            BackendError::Cloud(_)
            | BackendError::DialogueProtocol
            | BackendError::ApplicationAudioScopeUnavailable => None,
        };
        if let Some(code) = sidecar_code {
            emit_sidecar_failure(events, identity, code, candidate_emitted);
        }
    }
    emit_failed(events, identity, error.public_code());
}

pub(super) fn emit_sidecar_failure(
    events: &mpsc::Sender<WorkerEvent>,
    identity: &RuntimeIdentity,
    error_code: &'static str,
    candidate_emitted: bool,
) {
    let _ = events.send(WorkerEvent::LocalSidecarFailure {
        session_id: identity.session_id.clone(),
        segment_id: identity.segment_id.clone(),
        context_epoch: identity.context_epoch,
        error_code: error_code.to_owned(),
        candidate_emitted,
    });
}

pub(super) fn emit_cancelled(events: &mpsc::Sender<WorkerEvent>, identity: &RuntimeIdentity) {
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
