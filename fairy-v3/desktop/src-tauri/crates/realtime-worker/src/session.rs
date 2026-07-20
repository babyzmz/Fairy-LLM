use std::collections::VecDeque;

use thiserror::Error;
use zeroize::{Zeroize, Zeroizing};

use crate::protocol::{HostCommand, ProviderKind, WorkerEvent};

const MAX_PUBLIC_CAPTION_CHARS: usize = 2_000;
const MAX_PENDING_TOOL_RESULTS: usize = 32;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionSnapshot {
    pub session_id: String,
    pub provider: ProviderKind,
    pub status: &'static str,
    pub screen_enabled: bool,
    pub game_audio_enabled: bool,
    pub source_id: Option<u64>,
    pub audio_input_ms: u64,
    pub audio_output_ms: u64,
    pub video_frame_count: u64,
    pub interruption_count: u64,
}

#[derive(Default)]
pub struct LatestFrame {
    sequence: u64,
    jpeg: Option<Zeroizing<Vec<u8>>>,
}

impl LatestFrame {
    pub fn replace(&mut self, sequence: u64, jpeg: Vec<u8>) {
        if sequence <= self.sequence {
            return;
        }
        if let Some(mut previous) = self.jpeg.take() {
            previous.zeroize();
        }
        self.sequence = sequence;
        self.jpeg = Some(Zeroizing::new(jpeg));
    }

    pub fn take(&mut self) -> Option<(u64, Zeroizing<Vec<u8>>)> {
        self.jpeg.take().map(|jpeg| (self.sequence, jpeg))
    }

    pub fn clear(&mut self) {
        if let Some(mut jpeg) = self.jpeg.take() {
            jpeg.zeroize();
        }
        self.sequence = 0;
    }
}

struct ActiveSession {
    snapshot: SessionSnapshot,
    credential: Zeroizing<String>,
    pending_tool_results: VecDeque<Zeroizing<String>>,
    latest_frame: LatestFrame,
}

#[derive(Default)]
pub struct RealtimeWorker {
    active: Option<ActiveSession>,
}

#[derive(Debug, Error)]
pub enum SessionError {
    #[error("a realtime session is already active")]
    Busy,
    #[error("the realtime session does not match the active session")]
    ScopeMismatch,
    #[error("the realtime session request is invalid")]
    Invalid,
}

impl RealtimeWorker {
    pub fn handle(&mut self, command: HostCommand) -> Result<Vec<WorkerEvent>, SessionError> {
        match command {
            HostCommand::Start {
                session_id,
                provider,
                voice_mode,
                source_id,
                screen_enabled,
                game_audio_enabled,
                credential,
            } => self.start(
                session_id,
                provider,
                voice_mode,
                source_id,
                screen_enabled,
                game_audio_enabled,
                credential,
            ),
            HostCommand::Stop { session_id } => self.stop(&session_id),
            HostCommand::UpdateUsage {
                session_id,
                audio_input_ms,
                audio_output_ms,
                video_frame_count,
                interruption_count,
            } => {
                let session = self.require_active(&session_id)?;
                if audio_input_ms < session.snapshot.audio_input_ms
                    || audio_output_ms < session.snapshot.audio_output_ms
                    || video_frame_count < session.snapshot.video_frame_count
                {
                    return Err(SessionError::Invalid);
                }
                session.snapshot.audio_input_ms = audio_input_ms;
                session.snapshot.audio_output_ms = audio_output_ms;
                session.snapshot.video_frame_count = video_frame_count;
                session.snapshot.interruption_count = interruption_count;
                Ok(Vec::new())
            }
            HostCommand::ToolResult {
                session_id,
                call_id,
                public_summary,
                succeeded: _,
            } => {
                let session = self.require_active(&session_id)?;
                if call_id.is_empty()
                    || public_summary.is_empty()
                    || public_summary.chars().count() > MAX_PUBLIC_CAPTION_CHARS
                {
                    return Err(SessionError::Invalid);
                }
                if session.pending_tool_results.len() == MAX_PENDING_TOOL_RESULTS {
                    session.pending_tool_results.pop_front();
                }
                session
                    .pending_tool_results
                    .push_back(Zeroizing::new(public_summary));
                Ok(Vec::new())
            }
            HostCommand::Ping => Ok(vec![WorkerEvent::Pong]),
        }
    }

    pub fn snapshot(&self) -> Option<SessionSnapshot> {
        self.active.as_ref().map(|session| session.snapshot.clone())
    }

    pub fn latest_frame(&mut self) -> Result<&mut LatestFrame, SessionError> {
        self.active
            .as_mut()
            .map(|session| &mut session.latest_frame)
            .ok_or(SessionError::ScopeMismatch)
    }

    fn start(
        &mut self,
        session_id: String,
        provider: ProviderKind,
        voice_mode: String,
        source_id: Option<u64>,
        screen_enabled: bool,
        game_audio_enabled: bool,
        credential: String,
    ) -> Result<Vec<WorkerEvent>, SessionError> {
        if self.active.is_some()
            || session_id.is_empty()
            || credential.trim().is_empty()
            || !matches!(voice_mode.as_str(), "native" | "fairy")
            || (screen_enabled && source_id.is_none())
            || (!screen_enabled && source_id.is_some())
            || (game_audio_enabled && !screen_enabled)
        {
            return Err(if self.active.is_some() {
                SessionError::Busy
            } else {
                SessionError::Invalid
            });
        }
        let snapshot = SessionSnapshot {
            session_id: session_id.clone(),
            provider: provider.clone(),
            status: "active",
            screen_enabled,
            game_audio_enabled,
            source_id,
            audio_input_ms: 0,
            audio_output_ms: 0,
            video_frame_count: 0,
            interruption_count: 0,
        };
        self.active = Some(ActiveSession {
            snapshot,
            credential: Zeroizing::new(credential),
            pending_tool_results: VecDeque::new(),
            latest_frame: LatestFrame::default(),
        });
        Ok(vec![WorkerEvent::SessionState {
            session_id,
            status: "active",
            provider: Some(provider),
            error_code: None,
        }])
    }

    fn stop(&mut self, session_id: &str) -> Result<Vec<WorkerEvent>, SessionError> {
        if self
            .active
            .as_ref()
            .is_none_or(|session| session.snapshot.session_id != session_id)
        {
            return Err(SessionError::ScopeMismatch);
        }
        let mut session = self.active.take().expect("active session checked");
        session.credential.zeroize();
        for result in &mut session.pending_tool_results {
            result.zeroize();
        }
        session.latest_frame.clear();
        Ok(vec![WorkerEvent::SessionState {
            session_id: session_id.to_owned(),
            status: "completed",
            provider: Some(session.snapshot.provider),
            error_code: None,
        }])
    }

    fn require_active(&mut self, session_id: &str) -> Result<&mut ActiveSession, SessionError> {
        self.active
            .as_mut()
            .filter(|session| session.snapshot.session_id == session_id)
            .ok_or(SessionError::ScopeMismatch)
    }
}

impl Drop for RealtimeWorker {
    fn drop(&mut self) {
        if let Some(session) = self.active.as_mut() {
            session.credential.zeroize();
            for result in &mut session.pending_tool_results {
                result.zeroize();
            }
            session.latest_frame.clear();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn start_command() -> HostCommand {
        HostCommand::Start {
            session_id: "session-1".to_owned(),
            provider: ProviderKind::GlmRealtimeFlash,
            voice_mode: "native".to_owned(),
            source_id: Some(42),
            screen_enabled: true,
            game_audio_enabled: false,
            credential: "private-key".to_owned(),
        }
    }

    #[test]
    fn one_session_is_scoped_and_provider_is_frozen() {
        let mut worker = RealtimeWorker::default();
        worker.handle(start_command()).expect("start");
        assert!(matches!(
            worker.handle(start_command()),
            Err(SessionError::Busy)
        ));
        assert_eq!(
            worker.snapshot().expect("snapshot").provider,
            ProviderKind::GlmRealtimeFlash
        );
        assert!(matches!(
            worker.handle(HostCommand::Stop {
                session_id: "other".to_owned()
            }),
            Err(SessionError::ScopeMismatch)
        ));
        worker
            .handle(HostCommand::Stop {
                session_id: "session-1".to_owned(),
            })
            .expect("stop");
        assert!(worker.snapshot().is_none());
    }

    #[test]
    fn latest_frame_wins_and_old_frames_are_discarded() {
        let mut worker = RealtimeWorker::default();
        worker.handle(start_command()).expect("start");
        worker
            .latest_frame()
            .expect("frame buffer")
            .replace(1, vec![1; 8]);
        worker
            .latest_frame()
            .expect("frame buffer")
            .replace(3, vec![3; 8]);
        worker
            .latest_frame()
            .expect("frame buffer")
            .replace(2, vec![2; 8]);
        let (sequence, frame) = worker
            .latest_frame()
            .expect("frame buffer")
            .take()
            .expect("latest frame");
        assert_eq!(sequence, 3);
        assert_eq!(&*frame, &[3; 8]);
    }

    #[test]
    fn game_audio_cannot_escape_the_selected_window_scope() {
        let mut worker = RealtimeWorker::default();
        let HostCommand::Start {
            session_id,
            provider,
            voice_mode,
            credential,
            ..
        } = start_command()
        else {
            unreachable!()
        };
        assert!(matches!(
            worker.handle(HostCommand::Start {
                session_id,
                provider,
                voice_mode,
                source_id: None,
                screen_enabled: false,
                game_audio_enabled: true,
                credential,
            }),
            Err(SessionError::Invalid)
        ));
    }
}
