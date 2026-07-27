use fairy_realtime_worker::{
    RealtimeActivityProfile, RealtimeBackendKind, RealtimeInteractionIntensity,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;

const DEFAULT_PRESENCE_MAX_MINUTES: u16 = 240;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RealtimeCoordinatorStart {
    pub session_id: String,
    pub segment_id: String,
    pub persona_digest: String,
    pub backend: RealtimeBackendKind,
    pub activity_profile: RealtimeActivityProfile,
    pub interaction_intensity: RealtimeInteractionIntensity,
    pub presence_max_minutes: u16,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ContextEpochIdentity {
    pub session_id: String,
    pub segment_id: String,
    pub epoch: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextRotationReason {
    PrivacyResume,
    ProfileChanged,
    WindowChanged,
    Manual,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum RealtimeCoordinatorEvent {
    RotateContext {
        reason: ContextRotationReason,
    },
    UserApprovedBackendChange {
        segment_id: String,
        backend: RealtimeBackendKind,
        persona_digest: String,
    },
    SetProfile {
        profile: RealtimeActivityProfile,
    },
    PausePrivacy,
    ResumePrivacy,
    ExtendPresence {
        additional_minutes: u16,
    },
    End,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum RealtimeCoordinatorError {
    #[error("the realtime coordinator start request is invalid")]
    InvalidStart,
    #[error("the requested realtime transition is invalid")]
    InvalidTransition,
    #[error("the backend segment does not preserve the active persona snapshot")]
    PersonaMismatch,
    #[error("the context epoch cannot be incremented")]
    EpochOverflow,
    #[error("the presence duration cannot be extended")]
    DurationOverflow,
    #[error("the realtime presence session has ended")]
    Terminal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RealtimeCoordinatorStatus {
    Active,
    PrivacyPaused,
    Ended,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RealtimeCoordinatorState {
    identity: ContextEpochIdentity,
    persona_digest: String,
    backend: RealtimeBackendKind,
    activity_profile: RealtimeActivityProfile,
    interaction_intensity: RealtimeInteractionIntensity,
    presence_max_minutes: u16,
    status: RealtimeCoordinatorStatus,
    media_generation_enabled: bool,
}

impl RealtimeCoordinatorState {
    pub fn start(request: RealtimeCoordinatorStart) -> Result<Self, RealtimeCoordinatorError> {
        if !valid_identifier(&request.session_id)
            || !valid_identifier(&request.segment_id)
            || !valid_digest(&request.persona_digest)
        {
            return Err(RealtimeCoordinatorError::InvalidStart);
        }

        Ok(Self {
            identity: ContextEpochIdentity {
                session_id: request.session_id,
                segment_id: request.segment_id,
                epoch: 1,
            },
            persona_digest: request.persona_digest,
            backend: request.backend,
            activity_profile: request.activity_profile,
            interaction_intensity: request.interaction_intensity,
            presence_max_minutes: if request.presence_max_minutes == 0 {
                DEFAULT_PRESENCE_MAX_MINUTES
            } else {
                request.presence_max_minutes
            },
            status: RealtimeCoordinatorStatus::Active,
            media_generation_enabled: true,
        })
    }

    pub fn apply(
        &mut self,
        event: RealtimeCoordinatorEvent,
    ) -> Result<(), RealtimeCoordinatorError> {
        if self.status == RealtimeCoordinatorStatus::Ended {
            return Err(RealtimeCoordinatorError::Terminal);
        }

        match event {
            RealtimeCoordinatorEvent::RotateContext { reason: _ } => {
                self.require_active()?;
                self.rotate_context()
            }
            RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id,
                backend,
                persona_digest,
            } => {
                self.require_active()?;
                if persona_digest != self.persona_digest {
                    return Err(RealtimeCoordinatorError::PersonaMismatch);
                }
                if !valid_identifier(&segment_id) || segment_id == self.identity.segment_id {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                self.identity.segment_id = segment_id;
                self.identity.epoch = 1;
                self.backend = backend;
                Ok(())
            }
            RealtimeCoordinatorEvent::SetProfile { profile } => {
                self.require_active()?;
                if profile == self.activity_profile {
                    return Ok(());
                }
                self.activity_profile = profile;
                self.rotate_context()
            }
            RealtimeCoordinatorEvent::PausePrivacy => {
                self.require_active()?;
                self.status = RealtimeCoordinatorStatus::PrivacyPaused;
                self.media_generation_enabled = false;
                Ok(())
            }
            RealtimeCoordinatorEvent::ResumePrivacy => {
                if self.status != RealtimeCoordinatorStatus::PrivacyPaused {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                self.rotate_context()?;
                self.status = RealtimeCoordinatorStatus::Active;
                self.media_generation_enabled = true;
                Ok(())
            }
            RealtimeCoordinatorEvent::ExtendPresence { additional_minutes } => {
                if additional_minutes == 0 {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                self.presence_max_minutes = self
                    .presence_max_minutes
                    .checked_add(additional_minutes)
                    .ok_or(RealtimeCoordinatorError::DurationOverflow)?;
                Ok(())
            }
            RealtimeCoordinatorEvent::End => {
                self.status = RealtimeCoordinatorStatus::Ended;
                self.media_generation_enabled = false;
                Ok(())
            }
        }
    }

    pub fn active_identity(&self) -> &ContextEpochIdentity {
        &self.identity
    }

    pub fn backend(&self) -> RealtimeBackendKind {
        self.backend
    }

    pub fn media_generation_enabled(&self) -> bool {
        self.media_generation_enabled
    }

    pub fn presence_max_minutes(&self) -> u16 {
        self.presence_max_minutes
    }

    pub fn accepts_result(&self, session_id: &str, segment_id: &str, epoch: u64) -> bool {
        self.status == RealtimeCoordinatorStatus::Active
            && self.identity.session_id == session_id
            && self.identity.segment_id == segment_id
            && self.identity.epoch == epoch
    }

    fn require_active(&self) -> Result<(), RealtimeCoordinatorError> {
        if self.status == RealtimeCoordinatorStatus::Active {
            Ok(())
        } else {
            Err(RealtimeCoordinatorError::InvalidTransition)
        }
    }

    fn rotate_context(&mut self) -> Result<(), RealtimeCoordinatorError> {
        self.identity.epoch = self
            .identity
            .epoch
            .checked_add(1)
            .ok_or(RealtimeCoordinatorError::EpochOverflow)?;
        Ok(())
    }
}

fn valid_identifier(value: &str) -> bool {
    !value.trim().is_empty()
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn start_request() -> RealtimeCoordinatorStart {
        RealtimeCoordinatorStart {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            persona_digest: "a".repeat(64),
            backend: RealtimeBackendKind::CloudLive,
            activity_profile: RealtimeActivityProfile::Auto,
            interaction_intensity: RealtimeInteractionIntensity::Standard,
            presence_max_minutes: 240,
        }
    }

    #[test]
    fn rotating_context_rejects_results_from_the_previous_epoch() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        let first = state.active_identity().clone();
        state
            .apply(RealtimeCoordinatorEvent::RotateContext {
                reason: ContextRotationReason::PrivacyResume,
            })
            .expect("rotate");

        assert!(!state.accepts_result(&first.session_id, &first.segment_id, first.epoch));
        let active = state.active_identity();
        assert!(state.accepts_result(&active.session_id, &active.segment_id, active.epoch));
    }

    #[test]
    fn approved_backend_change_creates_a_new_segment() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        state
            .apply(RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id: "segment-2".to_owned(),
                backend: RealtimeBackendKind::LocalMiniCpmO45,
                persona_digest: "a".repeat(64),
            })
            .expect("backend change");

        assert_eq!(state.active_identity().segment_id, "segment-2".to_owned());
        assert_eq!(state.active_identity().epoch, 1);
        assert_eq!(state.backend(), RealtimeBackendKind::LocalMiniCpmO45);
    }

    #[test]
    fn backend_change_rejects_persona_drift_and_reused_segment_identity() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        assert_eq!(
            state.apply(RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id: "segment-2".to_owned(),
                backend: RealtimeBackendKind::LocalMiniCpmO45,
                persona_digest: "b".repeat(64),
            }),
            Err(RealtimeCoordinatorError::PersonaMismatch)
        );
        assert_eq!(
            state.apply(RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id: "segment-1".to_owned(),
                backend: RealtimeBackendKind::LocalMiniCpmO45,
                persona_digest: "a".repeat(64),
            }),
            Err(RealtimeCoordinatorError::InvalidTransition)
        );
    }

    #[test]
    fn profile_change_and_privacy_resume_rotate_context() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        state
            .apply(RealtimeCoordinatorEvent::SetProfile {
                profile: RealtimeActivityProfile::Game,
            })
            .expect("profile");
        assert_eq!(state.active_identity().epoch, 2);

        state
            .apply(RealtimeCoordinatorEvent::PausePrivacy)
            .expect("pause");
        assert!(!state.media_generation_enabled());
        state
            .apply(RealtimeCoordinatorEvent::ResumePrivacy)
            .expect("resume");
        assert!(state.media_generation_enabled());
        assert_eq!(state.active_identity().epoch, 3);
    }

    #[test]
    fn terminal_state_rejects_all_results_and_transitions() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        state
            .apply(RealtimeCoordinatorEvent::End)
            .expect("end session");
        let active = state.active_identity();
        assert!(!state.accepts_result(&active.session_id, &active.segment_id, active.epoch));
        assert_eq!(
            state.apply(RealtimeCoordinatorEvent::ResumePrivacy),
            Err(RealtimeCoordinatorError::Terminal)
        );
    }

    #[test]
    fn presence_duration_defaults_to_four_hours_and_extends_explicitly() {
        let mut request = start_request();
        request.presence_max_minutes = 0;
        let mut state = RealtimeCoordinatorState::start(request).expect("start");
        assert_eq!(state.presence_max_minutes(), 240);
        state
            .apply(RealtimeCoordinatorEvent::ExtendPresence {
                additional_minutes: 60,
            })
            .expect("extend");
        assert_eq!(state.presence_max_minutes(), 300);
    }
}
