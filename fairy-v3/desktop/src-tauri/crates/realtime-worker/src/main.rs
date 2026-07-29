use std::io::{stdin, stdout};
use std::thread;

use fairy_realtime_worker::{
    read_frame, validate_backend_start, validate_persona_snapshot, write_frame,
    BackendStartRequest, HostCommand, RealtimeBackendKind, RealtimeRuntime, RuntimeCommand,
    RuntimeLaunch, WorkerEvent,
};

const WORKER_PROTOCOL: &str = "fairy-realtime-worker-v2";

#[derive(Clone)]
struct ActiveIdentity {
    session_id: String,
    segment_id: String,
    context_epoch: u64,
    backend: RealtimeBackendKind,
}

fn main() {
    let mut input = stdin().lock();
    let mut output = stdout().lock();
    if write_frame(
        &mut output,
        &WorkerEvent::Ready {
            protocol: WORKER_PROTOCOL.to_owned(),
        },
    )
    .is_err()
    {
        return;
    }
    drop(output);
    let mut runtime: Option<RealtimeRuntime> = None;
    let mut active_identity: Option<ActiveIdentity> = None;
    let mut event_writer: Option<thread::JoinHandle<()>> = None;
    loop {
        let command = match read_frame::<HostCommand>(&mut input) {
            Ok(Some(command)) => command,
            Ok(None) | Err(_) => return,
        };
        match command {
            HostCommand::Start {
                session_id,
                segment_id,
                context_epoch,
                backend,
                cloud_provider,
                cloud_credential,
                local_omni,
                persona_snapshot,
                locale,
                activity_profile,
                interaction_intensity,
                voice_output,
                desktop_host_process_id,
                source_id,
                microphone_enabled,
                screen_enabled,
                application_audio_enabled,
                online_assistance_enabled,
            } if runtime.is_none() => {
                let persona = validate_persona_snapshot(
                    persona_snapshot.expose(),
                    &locale,
                    activity_profile,
                    interaction_intensity,
                );
                let validation = validate_backend_start(&BackendStartRequest {
                    session_id: session_id.clone(),
                    segment_id: segment_id.clone(),
                    context_epoch,
                    backend,
                    cloud_provider,
                    cloud_credential_present: cloud_credential
                        .as_ref()
                        .is_some_and(|credential| !credential.expose().trim().is_empty()),
                    persona_snapshot_present: persona.is_ok(),
                    activity_profile,
                    interaction_intensity,
                    voice_output,
                    desktop_host_process_id,
                    source_id,
                    microphone_enabled,
                    screen_enabled,
                    application_audio_enabled,
                    online_assistance_enabled,
                });
                if validation.is_err() || persona.is_err() {
                    emit_start_failure(
                        &session_id,
                        &segment_id,
                        context_epoch,
                        backend,
                        cloud_provider,
                        "REALTIME_INVALID_START",
                    );
                    continue;
                }
                let backend_launch_valid = match backend {
                    RealtimeBackendKind::CloudLive => {
                        cloud_provider.is_some()
                            && cloud_credential.is_some()
                            && local_omni.is_none()
                    }
                    RealtimeBackendKind::LocalMiniCpmO45 => {
                        cloud_provider.is_none()
                            && cloud_credential.is_none()
                            && local_omni.is_some()
                    }
                };
                if !backend_launch_valid {
                    emit_start_failure(
                        &session_id,
                        &segment_id,
                        context_epoch,
                        backend,
                        cloud_provider,
                        "REALTIME_INVALID_START",
                    );
                    continue;
                }
                let Some((started, writer)) = spawn_runtime(RuntimeLaunch {
                    session_id: session_id.clone(),
                    segment_id: segment_id.clone(),
                    context_epoch,
                    backend,
                    cloud_provider,
                    credential: cloud_credential.map(|value| value.into_zeroizing()),
                    local_omni: local_omni.map(|launch| *launch),
                    persona: persona.expect("validated Persona"),
                    activity_profile,
                    desktop_host_process_id,
                    source_id,
                    microphone_enabled,
                    screen_enabled,
                    application_audio_enabled,
                    voice_output,
                }) else {
                    return;
                };
                event_writer = Some(writer);
                active_identity = Some(ActiveIdentity {
                    session_id,
                    segment_id,
                    context_epoch,
                    backend,
                });
                runtime = Some(started);
            }
            HostCommand::RecoverLocal {
                session_id,
                current_segment_id,
                current_context_epoch,
                next_segment_id,
                local_omni,
                persona_snapshot,
                locale,
                activity_profile,
                interaction_intensity,
                voice_output,
                desktop_host_process_id,
                source_id,
                microphone_enabled,
                screen_enabled,
                application_audio_enabled,
                online_assistance_enabled,
            } if active_identity.as_ref().is_some_and(|identity| {
                identity.session_id == session_id
                    && identity.segment_id == current_segment_id
                    && identity.context_epoch == current_context_epoch
                    && identity.backend == RealtimeBackendKind::LocalMiniCpmO45
                    && !next_segment_id.trim().is_empty()
                    && next_segment_id != current_segment_id
                    && next_segment_id.len() <= 128
            }) =>
            {
                let persona = validate_persona_snapshot(
                    persona_snapshot.expose(),
                    &locale,
                    activity_profile,
                    interaction_intensity,
                );
                let validation = validate_backend_start(&BackendStartRequest {
                    session_id: session_id.clone(),
                    segment_id: next_segment_id.clone(),
                    context_epoch: 1,
                    backend: RealtimeBackendKind::LocalMiniCpmO45,
                    cloud_provider: None,
                    cloud_credential_present: false,
                    persona_snapshot_present: persona.is_ok(),
                    activity_profile,
                    interaction_intensity,
                    voice_output,
                    desktop_host_process_id,
                    source_id,
                    microphone_enabled,
                    screen_enabled,
                    application_audio_enabled,
                    online_assistance_enabled,
                });
                if validation.is_err() || persona.is_err() {
                    emit_recovery_failure(
                        &session_id,
                        &next_segment_id,
                        "LOCAL_SIDECAR_RECOVERY_FAILED",
                    );
                    continue;
                }
                if let Some(active) = runtime.take() {
                    drop(active);
                }
                if let Some(writer) = event_writer.take() {
                    let _ = writer.join();
                }
                let Some((started, writer)) = spawn_runtime(RuntimeLaunch {
                    session_id: session_id.clone(),
                    segment_id: next_segment_id.clone(),
                    context_epoch: 1,
                    backend: RealtimeBackendKind::LocalMiniCpmO45,
                    cloud_provider: None,
                    credential: None,
                    local_omni: Some(*local_omni),
                    persona: persona.expect("validated recovery Persona"),
                    activity_profile,
                    desktop_host_process_id,
                    source_id,
                    microphone_enabled,
                    screen_enabled,
                    application_audio_enabled,
                    voice_output,
                }) else {
                    emit_recovery_failure(
                        &session_id,
                        &next_segment_id,
                        "LOCAL_SIDECAR_RECOVERY_FAILED",
                    );
                    continue;
                };
                event_writer = Some(writer);
                active_identity = Some(ActiveIdentity {
                    session_id,
                    segment_id: next_segment_id,
                    context_epoch: 1,
                    backend: RealtimeBackendKind::LocalMiniCpmO45,
                });
                runtime = Some(started);
            }
            HostCommand::Stop { session_id }
                if active_identity
                    .as_ref()
                    .is_some_and(|identity| identity.session_id == session_id) =>
            {
                if let Some(active) = runtime.take() {
                    active.command(RuntimeCommand::Stop);
                }
                if let Some(writer) = event_writer.take() {
                    let _ = writer.join();
                }
                return;
            }
            HostCommand::ToolResult {
                session_id,
                call_id,
                public_summary,
                succeeded: _,
            } if active_identity
                .as_ref()
                .is_some_and(|identity| identity.session_id == session_id) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::ToolResult {
                        call_id,
                        public_summary,
                    });
                }
            }
            HostCommand::AssistanceResult {
                session_id,
                request_id,
                public_summary,
                succeeded,
            } if active_identity
                .as_ref()
                .is_some_and(|identity| identity.session_id == session_id) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::AssistanceResult {
                        request_id,
                        public_summary,
                        succeeded,
                    });
                }
            }
            HostCommand::SetInput {
                session_id,
                microphone,
                video,
            } if active_identity
                .as_ref()
                .is_some_and(|identity| identity.session_id == session_id) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::SetInput { microphone, video });
                }
            }
            HostCommand::SetProfile {
                session_id,
                activity_profile,
                interaction_intensity: _,
            } if active_identity
                .as_ref()
                .is_some_and(|identity| identity.session_id == session_id) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::SetProfile { activity_profile });
                }
            }
            HostCommand::SetResourcePolicy {
                session_id,
                segment_id,
                context_epoch,
                policy,
            } if active_identity.as_ref().is_some_and(|identity| {
                identity.session_id == session_id
                    && identity.segment_id == segment_id
                    && identity.context_epoch == context_epoch
                    && identity.backend == RealtimeBackendKind::LocalMiniCpmO45
                    && policy.is_valid()
            }) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::SetResourcePolicy { policy });
                }
            }
            HostCommand::Pause { session_id }
                if active_identity
                    .as_ref()
                    .is_some_and(|identity| identity.session_id == session_id) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::Pause);
                }
            }
            HostCommand::Resume {
                session_id,
                carryover,
            } if active_identity.as_ref().is_some_and(|identity| {
                identity.session_id == session_id
                    && identity.backend == RealtimeBackendKind::LocalMiniCpmO45
                    && identity.context_epoch < u64::MAX
                    && carryover.is_valid()
                    && carryover.session_id == identity.session_id
                    && carryover.current_segment_id == identity.segment_id
                    && carryover.current_context_epoch == identity.context_epoch
                    && carryover.target_segment_id == identity.segment_id
                    && carryover.next_context_epoch == identity.context_epoch + 1
            }) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::Resume { carryover });
                    if let Some(identity) = active_identity.as_mut() {
                        identity.context_epoch += 1;
                    }
                }
            }
            HostCommand::WakeSegment {
                session_id,
                current_segment_id,
                current_context_epoch,
                next_segment_id,
                carryover,
            } if active_identity.as_ref().is_some_and(|identity| {
                identity.session_id == session_id
                    && identity.backend == RealtimeBackendKind::CloudLive
                    && identity.segment_id == current_segment_id
                    && identity.context_epoch == current_context_epoch
                    && !next_segment_id.trim().is_empty()
                    && next_segment_id != current_segment_id
                    && carryover.is_valid()
                    && carryover.session_id == identity.session_id
                    && carryover.current_segment_id == identity.segment_id
                    && carryover.current_context_epoch == identity.context_epoch
                    && carryover.target_segment_id == next_segment_id
                    && carryover.next_context_epoch == 1
            }) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::WakeSegment {
                        next_segment_id: next_segment_id.clone(),
                        carryover,
                    });
                    if let Some(identity) = active_identity.as_mut() {
                        identity.segment_id = next_segment_id;
                        identity.context_epoch = 1;
                    }
                }
            }
            HostCommand::RotateContext {
                session_id,
                segment_id,
                current_context_epoch,
                next_context_epoch,
                reason,
                carryover,
            } if active_identity.as_ref().is_some_and(|identity| {
                identity.session_id == session_id
                    && identity.segment_id == segment_id
                    && identity.context_epoch == current_context_epoch
                    && current_context_epoch
                        .checked_add(1)
                        .is_some_and(|next| next == next_context_epoch)
                    && carryover.is_valid()
                    && carryover.session_id == identity.session_id
                    && carryover.current_segment_id == identity.segment_id
                    && carryover.current_context_epoch == identity.context_epoch
                    && carryover.target_segment_id == identity.segment_id
                    && carryover.next_context_epoch == next_context_epoch
            }) =>
            {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::RotateContext {
                        next_context_epoch,
                        reason,
                        carryover,
                    });
                    if let Some(identity) = active_identity.as_mut() {
                        identity.context_epoch = next_context_epoch;
                    }
                }
            }
            HostCommand::Ping => {
                if write_frame(&mut stdout().lock(), &WorkerEvent::Pong).is_err() {
                    return;
                }
            }
            HostCommand::UpdateUsage { .. } => {}
            _ => {
                let identity = active_identity.clone().unwrap_or(ActiveIdentity {
                    session_id: String::new(),
                    segment_id: String::new(),
                    context_epoch: 0,
                    backend: RealtimeBackendKind::CloudLive,
                });
                emit_start_failure(
                    &identity.session_id,
                    &identity.segment_id,
                    identity.context_epoch,
                    identity.backend,
                    None,
                    "REALTIME_PROTOCOL_ERROR",
                );
            }
        }
    }
}

fn spawn_runtime(launch: RuntimeLaunch) -> Option<(RealtimeRuntime, thread::JoinHandle<()>)> {
    let mut runtime = RealtimeRuntime::spawn(launch);
    let events = runtime.take_events()?;
    let writer = thread::Builder::new()
        .name("fairy-realtime-events".to_owned())
        .spawn(move || {
            for event in events {
                if write_frame(&mut stdout().lock(), &event).is_err() {
                    return;
                }
            }
        })
        .ok()?;
    Some((runtime, writer))
}

fn emit_recovery_failure(session_id: &str, segment_id: &str, error_code: &'static str) {
    let _ = write_frame(
        &mut stdout().lock(),
        &WorkerEvent::LocalSidecarFailure {
            session_id: session_id.to_owned(),
            segment_id: segment_id.to_owned(),
            context_epoch: 1,
            error_code: error_code.to_owned(),
            candidate_emitted: false,
        },
    );
    emit_start_failure(
        session_id,
        segment_id,
        1,
        RealtimeBackendKind::LocalMiniCpmO45,
        None,
        "LOCAL_BACKEND_NOT_READY",
    );
}

fn emit_start_failure(
    session_id: &str,
    segment_id: &str,
    context_epoch: u64,
    backend: RealtimeBackendKind,
    cloud_provider: Option<fairy_realtime_worker::RealtimeCloudProviderKind>,
    error_code: &'static str,
) {
    let _ = write_frame(
        &mut stdout().lock(),
        &WorkerEvent::SessionState {
            session_id: session_id.to_owned(),
            segment_id: segment_id.to_owned(),
            context_epoch,
            status: "failed".to_owned(),
            backend,
            cloud_provider,
            error_code: Some(error_code.to_owned()),
        },
    );
}
