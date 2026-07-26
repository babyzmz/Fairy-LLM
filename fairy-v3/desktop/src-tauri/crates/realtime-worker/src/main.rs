use std::io::{stdin, stdout};

use std::thread;

use fairy_realtime_worker::{
    read_frame, validate_start, write_frame, HostCommand, RealtimeRuntime, RuntimeCommand,
    RuntimeLaunch, WorkerEvent,
};

fn main() {
    let mut input = stdin().lock();
    let mut output = stdout().lock();
    if write_frame(
        &mut output,
        &WorkerEvent::Ready {
            protocol: "fairy-realtime-worker-v1",
        },
    )
    .is_err()
    {
        return;
    }
    drop(output);
    let mut runtime: Option<RealtimeRuntime> = None;
    let mut active_session: Option<String> = None;
    let mut event_writer: Option<thread::JoinHandle<()>> = None;
    loop {
        let command = match read_frame::<HostCommand>(&mut input) {
            Ok(Some(command)) => command,
            Ok(None) | Err(_) => return,
        };
        match command {
            HostCommand::Start {
                session_id,
                provider,
                voice_mode,
                source_id,
                screen_enabled,
                game_audio_enabled,
                credential,
            } if runtime.is_none() => {
                if validate_start(
                    &session_id,
                    &voice_mode,
                    source_id,
                    screen_enabled,
                    game_audio_enabled,
                    credential.expose(),
                )
                .is_err()
                {
                    let _ = write_frame(
                        &mut stdout().lock(),
                        &WorkerEvent::SessionState {
                            session_id,
                            status: "failed",
                            provider: Some(provider),
                            error_code: Some("REALTIME_INVALID_START"),
                        },
                    );
                    continue;
                }
                let mut started = RealtimeRuntime::spawn(RuntimeLaunch {
                    session_id: session_id.clone(),
                    provider,
                    credential: credential.into_zeroizing(),
                    system_instruction: companion_instruction(),
                    source_id,
                    screen_enabled,
                    game_audio_enabled,
                    voice_mode,
                });
                let Some(events) = started.take_events() else {
                    return;
                };
                event_writer = Some(thread::spawn(move || {
                    for event in events {
                        if write_frame(&mut stdout().lock(), &event).is_err() {
                            return;
                        }
                    }
                }));
                active_session = Some(session_id);
                runtime = Some(started);
            }
            HostCommand::Stop { session_id }
                if active_session.as_deref() == Some(session_id.as_str()) =>
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
            } if active_session.as_deref() == Some(session_id.as_str()) => {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::ToolResult {
                        call_id,
                        public_summary,
                    });
                }
            }
            HostCommand::SetInput {
                session_id,
                microphone,
                video,
            } if active_session.as_deref() == Some(session_id.as_str()) => {
                if let Some(active) = runtime.as_ref() {
                    active.command(RuntimeCommand::SetInput { microphone, video });
                }
            }
            HostCommand::Ping => {
                if write_frame(&mut stdout().lock(), &WorkerEvent::Pong).is_err() {
                    return;
                }
            }
            HostCommand::UpdateUsage { .. } => {}
            _ => {
                let event = WorkerEvent::SessionState {
                    session_id: active_session.clone().unwrap_or_default(),
                    status: "failed",
                    provider: None,
                    error_code: Some("REALTIME_PROTOCOL_ERROR"),
                };
                if write_frame(&mut stdout().lock(), &event).is_err() {
                    return;
                }
            }
        }
    }
}

fn companion_instruction() -> String {
    "You are Fairy, a concise realtime game companion. React to the player's speech and the selected game window without pretending to control the game. Do not reveal hidden reasoning. Ask before any action outside observation. Keep spoken turns brief and avoid interrupting urgent gameplay audio."
        .to_owned()
}
