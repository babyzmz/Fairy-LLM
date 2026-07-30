use fairy_realtime_worker::{
    RealtimeActivityProfile, RealtimeBackendKind, RealtimeCloudProviderKind,
    RealtimeDialogueCandidate, RealtimeInteractionIntensity, RealtimeResourceLevel, WorkerEvent,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::realtime_activity::{ActivityObservation, RealtimeActivityClassifier};

const DEFAULT_PRESENCE_MAX_MINUTES: u16 = 240;
const MINUTE_MS: u64 = 60_000;
const STANDBY_AFTER_MS: u64 = 3 * MINUTE_MS;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RealtimeCoordinatorStart {
    pub session_id: String,
    pub segment_id: String,
    pub persona_digest: String,
    pub backend: RealtimeBackendKind,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub activity_profile: RealtimeActivityProfile,
    pub interaction_intensity: RealtimeInteractionIntensity,
    pub presence_max_minutes: u16,
    pub local_keep_warm_minutes: u8,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ContextEpochIdentity {
    pub session_id: String,
    pub segment_id: String,
    pub epoch: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimePresenceState {
    Idle,
    Preparing,
    LoadingModel,
    Connecting,
    Listening,
    Observing,
    Thinking,
    Searching,
    Speaking,
    Standby,
    PrivacyPaused,
    ResourceLimited,
    Error,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RealtimePresenceProjection {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub sequence: u64,
    pub state: RealtimePresenceState,
    pub level: Option<u8>,
    pub persona_digest: String,
    pub requested_activity_profile: RealtimeActivityProfile,
    pub effective_activity: RealtimeActivityProfile,
    pub interaction_intensity: RealtimeInteractionIntensity,
    pub backend: RealtimeBackendKind,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub standby_reason: Option<RealtimeStandbyReason>,
    pub wake_available: bool,
    pub duration_extension_required: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeStandbyReason {
    Inactivity,
    DurationLimit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BackendSegmentCreationReason {
    InitialResolution,
    UserApprovedContinuation,
    AutomaticRecovery,
    StandbyWake,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BackendSegmentState {
    pub segment_id: String,
    pub ordinal: u32,
    pub backend: RealtimeBackendKind,
    pub cloud_provider: Option<RealtimeCloudProviderKind>,
    pub persona_digest: String,
    pub creation_reason: BackendSegmentCreationReason,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextRotationReason {
    PrivacyResume,
    ProfileChanged,
    WindowChanged,
    StandbyWake,
    Manual,
}

impl ContextRotationReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PrivacyResume => "privacy_resume",
            Self::ProfileChanged => "profile_changed",
            Self::WindowChanged => "window_changed",
            Self::StandbyWake => "standby_wake",
            Self::Manual => "manual",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PendingContextRotation {
    pub current: ContextEpochIdentity,
    pub next_epoch: u64,
    pub reason: ContextRotationReason,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PendingCloudWake {
    pub current: ContextEpochIdentity,
    pub next_segment_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PendingLocalWake {
    pub current: ContextEpochIdentity,
    pub next_segment_id: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RealtimeCoordinatorAction {
    EnterLocalStandby,
    EnterCloudStandby,
    RequestLocalUnload,
    RequireDurationExtension,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RealtimeWakeTransition {
    Local(PendingContextRotation),
    LocalSegment(PendingLocalWake),
    Cloud(PendingCloudWake),
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
        cloud_provider: Option<RealtimeCloudProviderKind>,
        persona_digest: String,
    },
    RecoverLocalSegment {
        segment_id: String,
        persona_digest: String,
    },
    LocalBackendUnloaded,
    SetPolicy {
        profile: RealtimeActivityProfile,
        interaction_intensity: RealtimeInteractionIntensity,
    },
    PausePrivacy,
    ResumePrivacy,
    ExtendPresence {
        additional_minutes: u16,
    },
    End,
    BackendFailed,
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
    Standby,
    PrivacyPaused,
    Ended,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RealtimeCoordinatorState {
    identity: ContextEpochIdentity,
    persona_digest: String,
    backend: RealtimeBackendKind,
    segment: BackendSegmentState,
    activity_profile: RealtimeActivityProfile,
    activity_classifier: RealtimeActivityClassifier,
    interaction_intensity: RealtimeInteractionIntensity,
    presence_max_minutes: u16,
    local_keep_warm_minutes: u8,
    status: RealtimeCoordinatorStatus,
    media_generation_enabled: bool,
    action_required: bool,
    presence_sequence: u64,
    presence_state: RealtimePresenceState,
    presence_level: Option<u8>,
    background_presence_state: RealtimePresenceState,
    background_presence_level: Option<u8>,
    fairy_speech_active: bool,
    pending_context_rotation: Option<PendingContextRotation>,
    pending_local_wake: Option<PendingLocalWake>,
    pending_cloud_wake: Option<PendingCloudWake>,
    started_at_ms: u64,
    last_activity_at_ms: u64,
    local_unload_requested: bool,
    local_backend_unloaded: bool,
    privacy_resume_to_standby: bool,
    duration_extension_required: bool,
    standby_reason: Option<RealtimeStandbyReason>,
}

impl RealtimeCoordinatorState {
    pub fn start(request: RealtimeCoordinatorStart) -> Result<Self, RealtimeCoordinatorError> {
        if !valid_identifier(&request.session_id)
            || !valid_identifier(&request.segment_id)
            || !valid_digest(&request.persona_digest)
            || !backend_provider_valid(request.backend, request.cloud_provider)
            || request.local_keep_warm_minutes > 30
        {
            return Err(RealtimeCoordinatorError::InvalidStart);
        }

        let segment_id = request.segment_id;
        let persona_digest = request.persona_digest;
        Ok(Self {
            identity: ContextEpochIdentity {
                session_id: request.session_id,
                segment_id: segment_id.clone(),
                epoch: 1,
            },
            persona_digest: persona_digest.clone(),
            backend: request.backend,
            segment: BackendSegmentState {
                segment_id,
                ordinal: 1,
                backend: request.backend,
                cloud_provider: request.cloud_provider,
                persona_digest,
                creation_reason: BackendSegmentCreationReason::InitialResolution,
            },
            activity_profile: request.activity_profile,
            activity_classifier: RealtimeActivityClassifier::new(request.activity_profile),
            interaction_intensity: request.interaction_intensity,
            presence_max_minutes: if request.presence_max_minutes == 0 {
                DEFAULT_PRESENCE_MAX_MINUTES
            } else {
                request.presence_max_minutes
            },
            local_keep_warm_minutes: request.local_keep_warm_minutes,
            status: RealtimeCoordinatorStatus::Active,
            media_generation_enabled: true,
            action_required: false,
            presence_sequence: 1,
            presence_state: RealtimePresenceState::Preparing,
            presence_level: None,
            background_presence_state: RealtimePresenceState::Preparing,
            background_presence_level: None,
            fairy_speech_active: false,
            pending_context_rotation: None,
            pending_local_wake: None,
            pending_cloud_wake: None,
            started_at_ms: 0,
            last_activity_at_ms: 0,
            local_unload_requested: false,
            local_backend_unloaded: false,
            privacy_resume_to_standby: false,
            duration_extension_required: false,
            standby_reason: None,
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
            RealtimeCoordinatorEvent::RotateContext { reason } => {
                self.require_active()?;
                self.prepare_context_rotation(reason).map(|_| ())
            }
            RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id,
                backend,
                cloud_provider,
                persona_digest,
            } => {
                self.require_active()?;
                if persona_digest != self.persona_digest {
                    return Err(RealtimeCoordinatorError::PersonaMismatch);
                }
                if !valid_identifier(&segment_id) || segment_id == self.identity.segment_id {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                if !backend_provider_valid(backend, cloud_provider) {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                self.identity.segment_id = segment_id;
                self.identity.epoch = 1;
                self.pending_context_rotation = None;
                self.pending_local_wake = None;
                self.pending_cloud_wake = None;
                self.backend = backend;
                self.segment = BackendSegmentState {
                    segment_id: self.identity.segment_id.clone(),
                    ordinal: self
                        .segment
                        .ordinal
                        .checked_add(1)
                        .ok_or(RealtimeCoordinatorError::InvalidTransition)?,
                    backend,
                    cloud_provider,
                    persona_digest,
                    creation_reason: BackendSegmentCreationReason::UserApprovedContinuation,
                };
                self.action_required = false;
                self.media_generation_enabled = true;
                self.status = RealtimeCoordinatorStatus::Active;
                self.local_unload_requested = false;
                self.local_backend_unloaded = false;
                self.privacy_resume_to_standby = false;
                self.standby_reason = None;
                self.set_authoritative_presence(RealtimePresenceState::Preparing, None)?;
                Ok(())
            }
            RealtimeCoordinatorEvent::RecoverLocalSegment {
                segment_id,
                persona_digest,
            } => {
                self.require_active()?;
                if self.backend != RealtimeBackendKind::LocalMiniCpmO45
                    || !self.action_required
                    || persona_digest != self.persona_digest
                    || !valid_identifier(&segment_id)
                    || segment_id == self.identity.segment_id
                {
                    return if persona_digest != self.persona_digest {
                        Err(RealtimeCoordinatorError::PersonaMismatch)
                    } else {
                        Err(RealtimeCoordinatorError::InvalidTransition)
                    };
                }
                self.identity.segment_id = segment_id;
                self.identity.epoch = 1;
                self.pending_context_rotation = None;
                self.pending_local_wake = None;
                self.pending_cloud_wake = None;
                self.segment = BackendSegmentState {
                    segment_id: self.identity.segment_id.clone(),
                    ordinal: self
                        .segment
                        .ordinal
                        .checked_add(1)
                        .ok_or(RealtimeCoordinatorError::InvalidTransition)?,
                    backend: RealtimeBackendKind::LocalMiniCpmO45,
                    cloud_provider: None,
                    persona_digest,
                    creation_reason: BackendSegmentCreationReason::AutomaticRecovery,
                };
                self.action_required = false;
                self.media_generation_enabled = true;
                self.local_unload_requested = false;
                self.local_backend_unloaded = false;
                self.privacy_resume_to_standby = false;
                self.standby_reason = None;
                self.set_authoritative_presence(RealtimePresenceState::Preparing, None)?;
                Ok(())
            }
            RealtimeCoordinatorEvent::LocalBackendUnloaded => {
                if self.backend != RealtimeBackendKind::LocalMiniCpmO45
                    || self.status != RealtimeCoordinatorStatus::Standby
                    || !self.local_unload_requested
                    || self.local_backend_unloaded
                {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                self.local_backend_unloaded = true;
                self.media_generation_enabled = false;
                Ok(())
            }
            RealtimeCoordinatorEvent::SetPolicy {
                profile,
                interaction_intensity,
            } => {
                self.require_active()?;
                if profile == self.activity_profile
                    && interaction_intensity == self.interaction_intensity
                {
                    return Ok(());
                }
                self.activity_profile = profile;
                self.activity_classifier = RealtimeActivityClassifier::new(profile);
                self.interaction_intensity = interaction_intensity;
                self.prepare_context_rotation(ContextRotationReason::ProfileChanged)?;
                self.set_authoritative_presence(RealtimePresenceState::Preparing, None)?;
                Ok(())
            }
            RealtimeCoordinatorEvent::PausePrivacy => {
                if !matches!(
                    self.status,
                    RealtimeCoordinatorStatus::Active | RealtimeCoordinatorStatus::Standby
                ) {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                self.privacy_resume_to_standby = self.status == RealtimeCoordinatorStatus::Standby;
                self.status = RealtimeCoordinatorStatus::PrivacyPaused;
                self.media_generation_enabled = false;
                self.pending_context_rotation = None;
                self.pending_local_wake = None;
                self.pending_cloud_wake = None;
                self.standby_reason = None;
                self.set_authoritative_presence(RealtimePresenceState::PrivacyPaused, None)?;
                Ok(())
            }
            RealtimeCoordinatorEvent::ResumePrivacy => {
                if self.status != RealtimeCoordinatorStatus::PrivacyPaused {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                if self.privacy_resume_to_standby {
                    self.privacy_resume_to_standby = false;
                    self.status = RealtimeCoordinatorStatus::Standby;
                    self.media_generation_enabled = false;
                    self.set_authoritative_presence(RealtimePresenceState::Standby, None)?;
                    return Ok(());
                }
                self.status = RealtimeCoordinatorStatus::Active;
                self.prepare_context_rotation(ContextRotationReason::PrivacyResume)
                    .map(|_| ())
            }
            RealtimeCoordinatorEvent::ExtendPresence { additional_minutes } => {
                if additional_minutes == 0 {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                self.presence_max_minutes = self
                    .presence_max_minutes
                    .checked_add(additional_minutes)
                    .ok_or(RealtimeCoordinatorError::DurationOverflow)?;
                if self.duration_extension_required {
                    self.duration_extension_required = false;
                    self.action_required = false;
                }
                Ok(())
            }
            RealtimeCoordinatorEvent::End => {
                self.status = RealtimeCoordinatorStatus::Ended;
                self.media_generation_enabled = false;
                self.pending_context_rotation = None;
                self.pending_local_wake = None;
                self.pending_cloud_wake = None;
                self.standby_reason = None;
                self.privacy_resume_to_standby = false;
                self.set_authoritative_presence(RealtimePresenceState::Idle, None)?;
                Ok(())
            }
            RealtimeCoordinatorEvent::BackendFailed => {
                self.require_active()?;
                self.action_required = true;
                self.media_generation_enabled = false;
                self.set_authoritative_presence(RealtimePresenceState::Error, None)?;
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

    pub fn active_segment(&self) -> &BackendSegmentState {
        &self.segment
    }

    pub fn action_required(&self) -> bool {
        self.action_required
    }

    pub fn persona_digest(&self) -> &str {
        &self.persona_digest
    }

    pub fn requested_activity_profile(&self) -> RealtimeActivityProfile {
        self.activity_profile
    }

    pub fn effective_activity(&self) -> RealtimeActivityProfile {
        self.activity_classifier.effective_activity()
    }

    pub fn interaction_intensity(&self) -> RealtimeInteractionIntensity {
        self.interaction_intensity
    }

    pub fn observe_activity_candidate(
        &mut self,
        sequence: u64,
        candidate: &RealtimeDialogueCandidate,
    ) -> Option<ActivityObservation> {
        (self.status == RealtimeCoordinatorStatus::Active && !self.action_required)
            .then(|| self.activity_classifier.observe(sequence, candidate))
    }

    pub fn media_generation_enabled(&self) -> bool {
        self.media_generation_enabled
    }

    pub fn record_meaningful_activity(&mut self, now_ms: u64) -> bool {
        if self.status != RealtimeCoordinatorStatus::Active
            || self.action_required
            || self.pending_context_rotation.is_some()
            || self.pending_local_wake.is_some()
            || self.pending_cloud_wake.is_some()
            || now_ms < self.last_activity_at_ms
        {
            return false;
        }
        self.last_activity_at_ms = now_ms;
        true
    }

    pub fn tick(&mut self, now_ms: u64) -> Vec<RealtimeCoordinatorAction> {
        if self.status == RealtimeCoordinatorStatus::Ended
            || self.status == RealtimeCoordinatorStatus::PrivacyPaused
            || self.pending_context_rotation.is_some()
            || self.pending_local_wake.is_some()
            || self.pending_cloud_wake.is_some()
        {
            return Vec::new();
        }
        if !self.duration_extension_required
            && now_ms.saturating_sub(self.started_at_ms)
                >= u64::from(self.presence_max_minutes) * MINUTE_MS
        {
            self.duration_extension_required = true;
            self.action_required = true;
            self.status = RealtimeCoordinatorStatus::Standby;
            self.media_generation_enabled = false;
            self.standby_reason = Some(RealtimeStandbyReason::DurationLimit);
            let _ = self.set_authoritative_presence(RealtimePresenceState::Standby, None);
            return vec![RealtimeCoordinatorAction::RequireDurationExtension];
        }
        if self.action_required {
            return Vec::new();
        }
        let idle_ms = now_ms.saturating_sub(self.last_activity_at_ms);
        if self.status == RealtimeCoordinatorStatus::Active && idle_ms >= STANDBY_AFTER_MS {
            self.status = RealtimeCoordinatorStatus::Standby;
            self.media_generation_enabled = false;
            self.standby_reason = Some(RealtimeStandbyReason::Inactivity);
            self.local_unload_requested = false;
            self.local_backend_unloaded = false;
            self.privacy_resume_to_standby = false;
            let _ = self.set_authoritative_presence(RealtimePresenceState::Standby, None);
            return match self.backend {
                RealtimeBackendKind::LocalMiniCpmO45 if self.local_keep_warm_minutes == 0 => {
                    self.local_unload_requested = true;
                    vec![
                        RealtimeCoordinatorAction::EnterLocalStandby,
                        RealtimeCoordinatorAction::RequestLocalUnload,
                    ]
                }
                RealtimeBackendKind::LocalMiniCpmO45 => {
                    vec![RealtimeCoordinatorAction::EnterLocalStandby]
                }
                RealtimeBackendKind::CloudLive => {
                    vec![RealtimeCoordinatorAction::EnterCloudStandby]
                }
            };
        }
        if self.status == RealtimeCoordinatorStatus::Standby
            && self.backend == RealtimeBackendKind::LocalMiniCpmO45
            && !self.local_unload_requested
            && idle_ms
                >= STANDBY_AFTER_MS
                    .saturating_add(u64::from(self.local_keep_warm_minutes) * MINUTE_MS)
        {
            self.local_unload_requested = true;
            return vec![RealtimeCoordinatorAction::RequestLocalUnload];
        }
        Vec::new()
    }

    pub fn prepare_wake(
        &mut self,
        next_segment_id: Option<String>,
    ) -> Result<RealtimeWakeTransition, RealtimeCoordinatorError> {
        if self.status != RealtimeCoordinatorStatus::Standby
            || self.action_required
            || self.pending_context_rotation.is_some()
            || self.pending_local_wake.is_some()
            || self.pending_cloud_wake.is_some()
        {
            return Err(RealtimeCoordinatorError::InvalidTransition);
        }
        match self.backend {
            RealtimeBackendKind::LocalMiniCpmO45 => {
                if self.local_backend_unloaded {
                    let next_segment_id =
                        next_segment_id.ok_or(RealtimeCoordinatorError::InvalidTransition)?;
                    if !valid_identifier(&next_segment_id)
                        || next_segment_id == self.identity.segment_id
                    {
                        return Err(RealtimeCoordinatorError::InvalidTransition);
                    }
                    let pending = PendingLocalWake {
                        current: self.identity.clone(),
                        next_segment_id,
                    };
                    self.pending_local_wake = Some(pending.clone());
                    return Ok(RealtimeWakeTransition::LocalSegment(pending));
                }
                if next_segment_id.is_some() {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                let next_epoch = self
                    .identity
                    .epoch
                    .checked_add(1)
                    .ok_or(RealtimeCoordinatorError::EpochOverflow)?;
                let pending = PendingContextRotation {
                    current: self.identity.clone(),
                    next_epoch,
                    reason: ContextRotationReason::StandbyWake,
                };
                self.pending_context_rotation = Some(pending.clone());
                Ok(RealtimeWakeTransition::Local(pending))
            }
            RealtimeBackendKind::CloudLive => {
                let next_segment_id =
                    next_segment_id.ok_or(RealtimeCoordinatorError::InvalidTransition)?;
                if !valid_identifier(&next_segment_id)
                    || next_segment_id == self.identity.segment_id
                {
                    return Err(RealtimeCoordinatorError::InvalidTransition);
                }
                let pending = PendingCloudWake {
                    current: self.identity.clone(),
                    next_segment_id,
                };
                self.pending_cloud_wake = Some(pending.clone());
                Ok(RealtimeWakeTransition::Cloud(pending))
            }
        }
    }

    pub fn prepare_context_rotation(
        &mut self,
        reason: ContextRotationReason,
    ) -> Result<PendingContextRotation, RealtimeCoordinatorError> {
        self.require_active()?;
        if self.pending_context_rotation.is_some() {
            return Err(RealtimeCoordinatorError::InvalidTransition);
        }
        let next_epoch = self
            .identity
            .epoch
            .checked_add(1)
            .ok_or(RealtimeCoordinatorError::EpochOverflow)?;
        let pending = PendingContextRotation {
            current: self.identity.clone(),
            next_epoch,
            reason,
        };
        self.pending_context_rotation = Some(pending.clone());
        self.media_generation_enabled = false;
        Ok(pending)
    }

    pub fn prepare_standby_context_rotation(
        &mut self,
        reason: ContextRotationReason,
    ) -> Result<PendingContextRotation, RealtimeCoordinatorError> {
        if self.status != RealtimeCoordinatorStatus::Standby
            || self.local_backend_unloaded
            || self.pending_context_rotation.is_some()
            || self.pending_local_wake.is_some()
            || self.pending_cloud_wake.is_some()
        {
            return Err(RealtimeCoordinatorError::InvalidTransition);
        }
        let next_epoch = self
            .identity
            .epoch
            .checked_add(1)
            .ok_or(RealtimeCoordinatorError::EpochOverflow)?;
        let pending = PendingContextRotation {
            current: self.identity.clone(),
            next_epoch,
            reason,
        };
        self.pending_context_rotation = Some(pending.clone());
        self.media_generation_enabled = false;
        Ok(pending)
    }

    pub fn commit_context_rotation(
        &mut self,
        next_epoch: u64,
    ) -> Result<(), RealtimeCoordinatorError> {
        self.commit_context_rotation_at(next_epoch, self.last_activity_at_ms)
    }

    pub fn commit_context_rotation_at(
        &mut self,
        next_epoch: u64,
        now_ms: u64,
    ) -> Result<(), RealtimeCoordinatorError> {
        let pending = self
            .pending_context_rotation
            .take()
            .ok_or(RealtimeCoordinatorError::InvalidTransition)?;
        if pending.next_epoch != next_epoch
            || pending.current.session_id != self.identity.session_id
            || pending.current.segment_id != self.identity.segment_id
            || pending.current.epoch != self.identity.epoch
        {
            self.pending_context_rotation = Some(pending);
            return Err(RealtimeCoordinatorError::InvalidTransition);
        }
        self.identity.epoch = next_epoch;
        self.activity_classifier.reset_epoch();
        if matches!(
            pending.reason,
            ContextRotationReason::StandbyWake | ContextRotationReason::PrivacyResume
        ) {
            if pending.reason == ContextRotationReason::StandbyWake {
                self.status = RealtimeCoordinatorStatus::Active;
            }
            self.last_activity_at_ms = now_ms.max(self.last_activity_at_ms);
            self.local_unload_requested = false;
            self.local_backend_unloaded = false;
            self.privacy_resume_to_standby = false;
            self.standby_reason = None;
        }
        self.media_generation_enabled = self.status == RealtimeCoordinatorStatus::Active;
        self.set_authoritative_presence(RealtimePresenceState::Standby, None)?;
        Ok(())
    }

    pub fn commit_cloud_wake(
        &mut self,
        next_segment_id: &str,
        now_ms: u64,
    ) -> Result<(), RealtimeCoordinatorError> {
        let pending = self
            .pending_cloud_wake
            .take()
            .ok_or(RealtimeCoordinatorError::InvalidTransition)?;
        if pending.current != self.identity || pending.next_segment_id != next_segment_id {
            self.pending_cloud_wake = Some(pending);
            return Err(RealtimeCoordinatorError::InvalidTransition);
        }
        self.identity.segment_id = next_segment_id.to_owned();
        self.identity.epoch = 1;
        self.segment = BackendSegmentState {
            segment_id: next_segment_id.to_owned(),
            ordinal: self
                .segment
                .ordinal
                .checked_add(1)
                .ok_or(RealtimeCoordinatorError::InvalidTransition)?,
            backend: self.backend,
            cloud_provider: self.segment.cloud_provider,
            persona_digest: self.persona_digest.clone(),
            creation_reason: BackendSegmentCreationReason::StandbyWake,
        };
        self.activity_classifier.reset_epoch();
        self.status = RealtimeCoordinatorStatus::Active;
        self.last_activity_at_ms = now_ms.max(self.last_activity_at_ms);
        self.media_generation_enabled = true;
        self.local_unload_requested = false;
        self.local_backend_unloaded = false;
        self.privacy_resume_to_standby = false;
        self.standby_reason = None;
        self.set_authoritative_presence(RealtimePresenceState::Standby, None)?;
        Ok(())
    }

    pub fn commit_local_wake(
        &mut self,
        next_segment_id: &str,
        now_ms: u64,
    ) -> Result<(), RealtimeCoordinatorError> {
        let pending = self
            .pending_local_wake
            .take()
            .ok_or(RealtimeCoordinatorError::InvalidTransition)?;
        if pending.current != self.identity || pending.next_segment_id != next_segment_id {
            self.pending_local_wake = Some(pending);
            return Err(RealtimeCoordinatorError::InvalidTransition);
        }
        self.identity.segment_id = next_segment_id.to_owned();
        self.identity.epoch = 1;
        self.segment = BackendSegmentState {
            segment_id: next_segment_id.to_owned(),
            ordinal: self
                .segment
                .ordinal
                .checked_add(1)
                .ok_or(RealtimeCoordinatorError::InvalidTransition)?,
            backend: RealtimeBackendKind::LocalMiniCpmO45,
            cloud_provider: None,
            persona_digest: self.persona_digest.clone(),
            creation_reason: BackendSegmentCreationReason::StandbyWake,
        };
        self.activity_classifier.reset_epoch();
        self.status = RealtimeCoordinatorStatus::Active;
        self.last_activity_at_ms = now_ms.max(self.last_activity_at_ms);
        self.media_generation_enabled = true;
        self.local_unload_requested = false;
        self.local_backend_unloaded = false;
        self.privacy_resume_to_standby = false;
        self.standby_reason = None;
        self.set_authoritative_presence(RealtimePresenceState::Standby, None)?;
        Ok(())
    }

    pub fn fail_context_rotation(&mut self) -> Result<(), RealtimeCoordinatorError> {
        self.pending_context_rotation = None;
        self.pending_local_wake = None;
        self.pending_cloud_wake = None;
        self.action_required = true;
        self.media_generation_enabled = false;
        self.standby_reason = None;
        self.set_authoritative_presence(RealtimePresenceState::Error, None)?;
        Ok(())
    }

    pub fn pending_context_rotation(&self) -> Option<&PendingContextRotation> {
        self.pending_context_rotation.as_ref()
    }

    pub fn pending_cloud_wake(&self) -> Option<&PendingCloudWake> {
        self.pending_cloud_wake.as_ref()
    }

    pub fn pending_local_wake(&self) -> Option<&PendingLocalWake> {
        self.pending_local_wake.as_ref()
    }

    pub fn is_standby(&self) -> bool {
        self.status == RealtimeCoordinatorStatus::Standby
    }

    pub fn duration_extension_required(&self) -> bool {
        self.duration_extension_required
    }

    pub fn local_unload_requested(&self) -> bool {
        self.local_unload_requested
    }

    pub fn local_backend_unloaded(&self) -> bool {
        self.local_backend_unloaded
    }

    pub fn presence_max_minutes(&self) -> u16 {
        self.presence_max_minutes
    }

    pub fn presence_projection(&self) -> RealtimePresenceProjection {
        RealtimePresenceProjection {
            session_id: self.identity.session_id.clone(),
            segment_id: self.identity.segment_id.clone(),
            context_epoch: self.identity.epoch,
            sequence: self.presence_sequence,
            state: self.presence_state,
            level: self.presence_level,
            persona_digest: self.persona_digest.clone(),
            requested_activity_profile: self.activity_profile,
            effective_activity: self.activity_classifier.effective_activity(),
            interaction_intensity: self.interaction_intensity,
            backend: self.backend,
            cloud_provider: self.segment.cloud_provider,
            standby_reason: self.standby_reason,
            wake_available: matches!(
                self.status,
                RealtimeCoordinatorStatus::Standby | RealtimeCoordinatorStatus::PrivacyPaused
            ) && !self.action_required,
            duration_extension_required: self.duration_extension_required,
        }
    }

    pub fn project_worker_event(
        &mut self,
        event: &WorkerEvent,
    ) -> Option<RealtimePresenceProjection> {
        let (session_id, segment_id, context_epoch, state, level) = worker_presence_input(event)?;
        if self.status == RealtimeCoordinatorStatus::Ended
            || self.pending_context_rotation.is_some()
            || self.pending_local_wake.is_some()
            || self.pending_cloud_wake.is_some()
            || session_id != self.identity.session_id
            || segment_id != self.identity.segment_id
            || context_epoch != self.identity.epoch
        {
            return None;
        }
        self.background_presence_state = state;
        self.background_presence_level = level;
        if matches!(
            state,
            RealtimePresenceState::Idle
                | RealtimePresenceState::Listening
                | RealtimePresenceState::PrivacyPaused
                | RealtimePresenceState::ResourceLimited
                | RealtimePresenceState::Error
        ) {
            self.fairy_speech_active = false;
        }
        if self.fairy_speech_active || !self.set_presence(state, level).ok()? {
            return None;
        }
        Some(self.presence_projection())
    }

    pub fn project_host_state(
        &mut self,
        state: RealtimePresenceState,
    ) -> Option<RealtimePresenceProjection> {
        if self.status == RealtimeCoordinatorStatus::Ended {
            return None;
        }
        if !self.set_authoritative_presence(state, None).ok()? {
            return None;
        }
        Some(self.presence_projection())
    }

    pub fn project_fairy_speech(&mut self, speaking: bool) -> Option<RealtimePresenceProjection> {
        if self.status == RealtimeCoordinatorStatus::Ended {
            return None;
        }
        self.fairy_speech_active = speaking;
        let (state, level) = if speaking {
            (RealtimePresenceState::Speaking, None)
        } else {
            (
                self.background_presence_state,
                self.background_presence_level,
            )
        };
        if !self.set_presence(state, level).ok()? {
            return None;
        }
        Some(self.presence_projection())
    }

    pub fn accepts_result(&self, session_id: &str, segment_id: &str, epoch: u64) -> bool {
        self.status == RealtimeCoordinatorStatus::Active
            && !self.action_required
            && self.pending_context_rotation.is_none()
            && self.pending_local_wake.is_none()
            && self.pending_cloud_wake.is_none()
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

    fn set_presence(
        &mut self,
        state: RealtimePresenceState,
        level: Option<u8>,
    ) -> Result<bool, RealtimeCoordinatorError> {
        if self.presence_state == state && self.presence_level == level {
            return Ok(false);
        }
        self.presence_sequence = self
            .presence_sequence
            .checked_add(1)
            .ok_or(RealtimeCoordinatorError::InvalidTransition)?;
        self.presence_state = state;
        self.presence_level = level;
        Ok(true)
    }

    fn set_authoritative_presence(
        &mut self,
        state: RealtimePresenceState,
        level: Option<u8>,
    ) -> Result<bool, RealtimeCoordinatorError> {
        self.background_presence_state = state;
        self.background_presence_level = level;
        self.fairy_speech_active = false;
        self.set_presence(state, level)
    }
}

fn worker_presence_input(
    event: &WorkerEvent,
) -> Option<(&str, &str, u64, RealtimePresenceState, Option<u8>)> {
    match event {
        WorkerEvent::BackendState {
            session_id,
            segment_id,
            context_epoch,
            status,
            ..
        } => map_backend_state(status).map(|state| {
            (
                session_id.as_str(),
                segment_id.as_str(),
                *context_epoch,
                state,
                None,
            )
        }),
        WorkerEvent::ModelLoadProgress {
            session_id,
            segment_id,
            context_epoch,
            ..
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            RealtimePresenceState::LoadingModel,
            None,
        )),
        WorkerEvent::SessionState {
            session_id,
            segment_id,
            context_epoch,
            status,
            ..
        } => map_session_state(status).map(|state| {
            (
                session_id.as_str(),
                segment_id.as_str(),
                *context_epoch,
                state,
                None,
            )
        }),
        WorkerEvent::Presence {
            session_id,
            segment_id,
            context_epoch,
            state,
            level,
        } => map_worker_presence(state).map(|mapped| {
            (
                session_id.as_str(),
                segment_id.as_str(),
                *context_epoch,
                mapped,
                *level,
            )
        }),
        WorkerEvent::BargeIn {
            session_id,
            segment_id,
            context_epoch,
        }
        | WorkerEvent::PublicCaption {
            session_id,
            segment_id,
            context_epoch,
            speaker: _,
            ..
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            RealtimePresenceState::Listening,
            None,
        )),
        WorkerEvent::PerceptionCandidate {
            session_id,
            segment_id,
            context_epoch,
            ..
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            RealtimePresenceState::Thinking,
            None,
        )),
        WorkerEvent::AssistanceRequest {
            session_id,
            segment_id,
            context_epoch,
            ..
        }
        | WorkerEvent::ToolRequest {
            session_id,
            segment_id,
            context_epoch,
            ..
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            RealtimePresenceState::Searching,
            None,
        )),
        WorkerEvent::AssistanceState {
            session_id,
            segment_id,
            context_epoch,
            status,
            ..
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            if status == "pending" || status == "running" {
                RealtimePresenceState::Searching
            } else {
                RealtimePresenceState::Thinking
            },
            None,
        )),
        WorkerEvent::ResourcePressure {
            session_id,
            segment_id,
            context_epoch,
            ..
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            RealtimePresenceState::ResourceLimited,
            None,
        )),
        WorkerEvent::ResourcePolicyApplied {
            session_id,
            segment_id,
            context_epoch,
            policy,
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            if policy.level == RealtimeResourceLevel::Normal {
                RealtimePresenceState::Observing
            } else {
                RealtimePresenceState::ResourceLimited
            },
            match policy.level {
                RealtimeResourceLevel::Normal => None,
                RealtimeResourceLevel::Pressure => Some(1),
                RealtimeResourceLevel::High => Some(2),
                RealtimeResourceLevel::Critical => Some(3),
                RealtimeResourceLevel::DeviceRemoved => Some(4),
            },
        )),
        WorkerEvent::ContextRotated {
            session_id,
            segment_id,
            context_epoch,
            ..
        }
        | WorkerEvent::SegmentWoken {
            session_id,
            segment_id,
            context_epoch,
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            RealtimePresenceState::Standby,
            None,
        )),
        WorkerEvent::PrivacyPaused {
            session_id,
            segment_id,
            context_epoch,
        } => Some((
            session_id,
            segment_id,
            *context_epoch,
            RealtimePresenceState::PrivacyPaused,
            None,
        )),
        WorkerEvent::Ready { .. }
        | WorkerEvent::StartupStage { .. }
        | WorkerEvent::MediaChannelState { .. }
        | WorkerEvent::CaptureSourceChanged { .. }
        | WorkerEvent::LocalSidecarFailure { .. }
        | WorkerEvent::LocalBackendUnloaded { .. }
        | WorkerEvent::ResourceSample { .. }
        | WorkerEvent::Usage { .. }
        | WorkerEvent::Diagnostic { .. }
        | WorkerEvent::Pong => None,
    }
}

fn map_backend_state(status: &str) -> Option<RealtimePresenceState> {
    match status {
        "preparing" | "starting" => Some(RealtimePresenceState::Preparing),
        "loading" | "loading_model" => Some(RealtimePresenceState::LoadingModel),
        "connecting" | "ready" => Some(RealtimePresenceState::Connecting),
        "failed" | "interrupted" => Some(RealtimePresenceState::Error),
        _ => None,
    }
}

fn map_session_state(status: &str) -> Option<RealtimePresenceState> {
    match status {
        "starting" => Some(RealtimePresenceState::Preparing),
        "active" => Some(RealtimePresenceState::Standby),
        "stopping" => Some(RealtimePresenceState::Standby),
        "completed" | "cancelled" => Some(RealtimePresenceState::Idle),
        "failed" | "interrupted" => Some(RealtimePresenceState::Error),
        _ => None,
    }
}

fn map_worker_presence(state: &str) -> Option<RealtimePresenceState> {
    match state {
        "active" | "idle" | "standby" => Some(RealtimePresenceState::Standby),
        "listening" => Some(RealtimePresenceState::Listening),
        "observing" => Some(RealtimePresenceState::Observing),
        "analyzing" | "thinking" => Some(RealtimePresenceState::Thinking),
        "searching" => Some(RealtimePresenceState::Searching),
        "speaking" => Some(RealtimePresenceState::Speaking),
        "privacy_paused" => Some(RealtimePresenceState::PrivacyPaused),
        "resource_limited" => Some(RealtimePresenceState::ResourceLimited),
        "error" => Some(RealtimePresenceState::Error),
        _ => None,
    }
}

fn valid_identifier(value: &str) -> bool {
    !value.trim().is_empty()
}

fn backend_provider_valid(
    backend: RealtimeBackendKind,
    provider: Option<RealtimeCloudProviderKind>,
) -> bool {
    match backend {
        RealtimeBackendKind::LocalMiniCpmO45 => provider.is_none(),
        RealtimeBackendKind::CloudLive => provider.is_some(),
    }
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
    use fairy_realtime_worker::RealtimeCandidateDecision;

    fn start_request() -> RealtimeCoordinatorStart {
        RealtimeCoordinatorStart {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            persona_digest: "a".repeat(64),
            backend: RealtimeBackendKind::CloudLive,
            cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
            activity_profile: RealtimeActivityProfile::Auto,
            interaction_intensity: RealtimeInteractionIntensity::Standard,
            presence_max_minutes: 240,
            local_keep_warm_minutes: 10,
        }
    }

    fn activity_candidate(activity: RealtimeActivityProfile) -> RealtimeDialogueCandidate {
        RealtimeDialogueCandidate {
            decision: RealtimeCandidateDecision::Speak,
            activity,
            confidence: 0.9,
            intent: "comment".to_owned(),
            grounding: vec!["current_window: stable activity".to_owned()],
            text: "Observed event.".to_owned(),
            urgency: 0.2,
            needs_online_assistance: false,
            response_to_user: false,
            stable: true,
            persona_digest: "a".repeat(64),
        }
    }

    fn local_start_request() -> RealtimeCoordinatorStart {
        RealtimeCoordinatorStart {
            backend: RealtimeBackendKind::LocalMiniCpmO45,
            cloud_provider: None,
            ..start_request()
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
        let pending = state
            .pending_context_rotation()
            .expect("pending rotation")
            .clone();
        state
            .commit_context_rotation(pending.next_epoch)
            .expect("acknowledge rotation");
        let active = state.active_identity();
        assert!(state.accepts_result(&active.session_id, &active.segment_id, active.epoch));
    }

    #[test]
    fn prepared_context_rotation_stays_inactive_until_exact_acknowledgement() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        let pending = state
            .prepare_context_rotation(ContextRotationReason::WindowChanged)
            .expect("prepare");
        assert_eq!(pending.current.epoch, 1);
        assert_eq!(pending.next_epoch, 2);
        assert_eq!(state.active_identity().epoch, 1);
        assert!(!state.media_generation_enabled());
        assert!(!state.accepts_result("session-1", "segment-1", 1));
        assert_eq!(
            state.commit_context_rotation(3),
            Err(RealtimeCoordinatorError::InvalidTransition)
        );
        assert_eq!(state.active_identity().epoch, 1);
        state.commit_context_rotation(2).expect("commit");
        assert_eq!(state.active_identity().epoch, 2);
        assert!(state.media_generation_enabled());
        assert!(state.pending_context_rotation().is_none());
    }

    #[test]
    fn coordinator_owns_requested_and_effective_auto_activity() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        assert_eq!(
            state.requested_activity_profile(),
            RealtimeActivityProfile::Auto
        );
        assert_eq!(state.effective_activity(), RealtimeActivityProfile::Focus);
        for sequence in 1..=2 {
            let observation = state
                .observe_activity_candidate(
                    sequence,
                    &activity_candidate(RealtimeActivityProfile::Game),
                )
                .expect("observation");
            assert!(!observation.switched);
        }
        let observation = state
            .observe_activity_candidate(3, &activity_candidate(RealtimeActivityProfile::Game))
            .expect("observation");
        assert!(observation.switched);
        assert_eq!(state.effective_activity(), RealtimeActivityProfile::Game);
        assert_eq!(
            state.interaction_intensity(),
            RealtimeInteractionIntensity::Standard
        );
        let projection = state.presence_projection();
        assert_eq!(
            projection.requested_activity_profile,
            RealtimeActivityProfile::Auto
        );
        assert_eq!(projection.effective_activity, RealtimeActivityProfile::Game);
        assert_eq!(
            projection.interaction_intensity,
            RealtimeInteractionIntensity::Standard
        );
        assert_eq!(projection.backend, RealtimeBackendKind::CloudLive);
    }

    #[test]
    fn approved_backend_change_creates_a_new_segment() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        state
            .apply(RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id: "segment-2".to_owned(),
                backend: RealtimeBackendKind::LocalMiniCpmO45,
                cloud_provider: None,
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
                cloud_provider: None,
                persona_digest: "b".repeat(64),
            }),
            Err(RealtimeCoordinatorError::PersonaMismatch)
        );
        assert_eq!(
            state.apply(RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id: "segment-1".to_owned(),
                backend: RealtimeBackendKind::LocalMiniCpmO45,
                cloud_provider: None,
                persona_digest: "a".repeat(64),
            }),
            Err(RealtimeCoordinatorError::InvalidTransition)
        );
    }

    #[test]
    fn profile_change_and_privacy_resume_rotate_context() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        state
            .apply(RealtimeCoordinatorEvent::SetPolicy {
                profile: RealtimeActivityProfile::Game,
                interaction_intensity: RealtimeInteractionIntensity::Active,
            })
            .expect("profile");
        assert_eq!(state.active_identity().epoch, 1);
        assert!(!state.media_generation_enabled());
        let profile_epoch = state
            .pending_context_rotation()
            .expect("profile rotation")
            .next_epoch;
        state
            .commit_context_rotation(profile_epoch)
            .expect("profile rotation acknowledgement");
        assert_eq!(state.active_identity().epoch, 2);
        assert_eq!(
            state.interaction_intensity(),
            RealtimeInteractionIntensity::Active
        );

        state
            .apply(RealtimeCoordinatorEvent::PausePrivacy)
            .expect("pause");
        assert!(!state.media_generation_enabled());
        state
            .apply(RealtimeCoordinatorEvent::ResumePrivacy)
            .expect("resume");
        assert!(!state.media_generation_enabled());
        let privacy_epoch = state
            .pending_context_rotation()
            .expect("privacy rotation")
            .next_epoch;
        state
            .commit_context_rotation(privacy_epoch)
            .expect("privacy rotation acknowledgement");
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

    #[test]
    fn native_ticks_enter_backend_specific_standby_only_at_the_threshold() {
        let mut local =
            RealtimeCoordinatorState::start(local_start_request()).expect("local start");
        let mut cloud = RealtimeCoordinatorState::start(start_request()).expect("cloud start");
        assert!(local.tick(STANDBY_AFTER_MS - 1).is_empty());
        assert!(cloud.tick(STANDBY_AFTER_MS - 1).is_empty());
        assert_eq!(
            local.tick(STANDBY_AFTER_MS),
            vec![RealtimeCoordinatorAction::EnterLocalStandby]
        );
        assert_eq!(
            cloud.tick(STANDBY_AFTER_MS),
            vec![RealtimeCoordinatorAction::EnterCloudStandby]
        );
        assert!(local.is_standby());
        assert!(cloud.is_standby());
        assert!(!local.media_generation_enabled());
        assert!(!cloud.media_generation_enabled());
        assert_eq!(
            local.presence_projection().standby_reason,
            Some(RealtimeStandbyReason::Inactivity)
        );
        assert!(local.presence_projection().wake_available);
    }

    #[test]
    fn meaningful_activity_resets_standby_and_local_unload_deadlines() {
        let mut state =
            RealtimeCoordinatorState::start(local_start_request()).expect("local start");
        assert!(state.record_meaningful_activity(120_000));
        assert!(state.tick(299_999).is_empty());
        assert_eq!(
            state.tick(300_000),
            vec![RealtimeCoordinatorAction::EnterLocalStandby]
        );
        assert!(state.tick(899_999).is_empty());
        assert_eq!(
            state.tick(900_000),
            vec![RealtimeCoordinatorAction::RequestLocalUnload]
        );
        assert!(state.local_unload_requested());
        assert!(state.tick(1_200_000).is_empty());
    }

    #[test]
    fn zero_keep_warm_requests_one_immediate_unload_and_wakes_as_a_new_segment() {
        let mut request = local_start_request();
        request.local_keep_warm_minutes = 0;
        let mut state = RealtimeCoordinatorState::start(request).expect("local start");
        assert_eq!(
            state.tick(STANDBY_AFTER_MS),
            vec![
                RealtimeCoordinatorAction::EnterLocalStandby,
                RealtimeCoordinatorAction::RequestLocalUnload,
            ]
        );
        assert!(state.tick(STANDBY_AFTER_MS + MINUTE_MS).is_empty());
        state
            .apply(RealtimeCoordinatorEvent::LocalBackendUnloaded)
            .expect("unload acknowledgement");
        assert!(state.local_backend_unloaded());

        let RealtimeWakeTransition::LocalSegment(wake) = state
            .prepare_wake(Some("segment-2".to_owned()))
            .expect("local segment wake")
        else {
            panic!("local segment wake")
        };
        assert_eq!(wake.current.segment_id, "segment-1");
        state
            .commit_local_wake("segment-2", STANDBY_AFTER_MS + MINUTE_MS)
            .expect("local wake acknowledgement");
        assert_eq!(state.active_identity().segment_id, "segment-2");
        assert_eq!(state.active_identity().epoch, 1);
        assert_eq!(
            state.active_segment().creation_reason,
            BackendSegmentCreationReason::StandbyWake
        );
        assert!(!state.local_backend_unloaded());
        assert!(!state.is_standby());
    }

    #[test]
    fn late_tick_honors_additional_keep_warm_before_requesting_unload() {
        let mut request = local_start_request();
        request.local_keep_warm_minutes = 5;
        let mut state = RealtimeCoordinatorState::start(request).expect("local start");
        assert_eq!(
            state.tick(STANDBY_AFTER_MS + 5 * MINUTE_MS + 1),
            vec![RealtimeCoordinatorAction::EnterLocalStandby]
        );
        assert_eq!(
            state.tick(STANDBY_AFTER_MS + 5 * MINUTE_MS + 2),
            vec![RealtimeCoordinatorAction::RequestLocalUnload]
        );
        assert!(state.tick(STANDBY_AFTER_MS + 20 * MINUTE_MS).is_empty());
    }

    #[test]
    fn local_wake_requires_a_new_epoch_and_cloud_wake_requires_a_new_segment() {
        let mut local =
            RealtimeCoordinatorState::start(local_start_request()).expect("local start");
        assert_eq!(
            local.tick(STANDBY_AFTER_MS),
            vec![RealtimeCoordinatorAction::EnterLocalStandby]
        );
        let RealtimeWakeTransition::Local(local_wake) =
            local.prepare_wake(None).expect("local wake")
        else {
            panic!("local epoch wake")
        };
        assert_eq!(local_wake.next_epoch, 2);
        assert!(!local.accepts_result("session-1", "segment-1", 1));
        local
            .commit_context_rotation_at(local_wake.next_epoch, STANDBY_AFTER_MS + 1)
            .expect("local wake acknowledgement");
        assert_eq!(local.active_identity().epoch, 2);
        assert!(!local.is_standby());

        let mut cloud = RealtimeCoordinatorState::start(start_request()).expect("cloud start");
        assert_eq!(
            cloud.tick(STANDBY_AFTER_MS),
            vec![RealtimeCoordinatorAction::EnterCloudStandby]
        );
        let RealtimeWakeTransition::Cloud(cloud_wake) = cloud
            .prepare_wake(Some("segment-2".to_owned()))
            .expect("cloud wake")
        else {
            panic!("cloud segment wake")
        };
        assert_eq!(cloud_wake.current.segment_id, "segment-1");
        cloud
            .commit_cloud_wake("segment-2", STANDBY_AFTER_MS + 1)
            .expect("cloud wake acknowledgement");
        assert_eq!(cloud.active_identity().segment_id, "segment-2");
        assert_eq!(cloud.active_identity().epoch, 1);
        assert_eq!(
            cloud.active_segment().creation_reason,
            BackendSegmentCreationReason::StandbyWake
        );
        assert!(!cloud.is_standby());
    }

    #[test]
    fn privacy_pause_blocks_standby_wake_and_duration_expiry_requires_extension() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        state
            .apply(RealtimeCoordinatorEvent::PausePrivacy)
            .expect("privacy pause");
        assert!(state.tick(STANDBY_AFTER_MS * 2).is_empty());
        assert_eq!(
            state.prepare_wake(Some("segment-2".to_owned())),
            Err(RealtimeCoordinatorError::InvalidTransition)
        );

        let mut expiring = RealtimeCoordinatorState::start(RealtimeCoordinatorStart {
            presence_max_minutes: 1,
            ..start_request()
        })
        .expect("expiring");
        assert!(expiring.tick(MINUTE_MS - 1).is_empty());
        assert_eq!(
            expiring.tick(MINUTE_MS),
            vec![RealtimeCoordinatorAction::RequireDurationExtension]
        );
        assert!(expiring.duration_extension_required());
        assert!(expiring.action_required());
        assert_eq!(
            expiring.presence_projection().standby_reason,
            Some(RealtimeStandbyReason::DurationLimit)
        );
        assert!(!expiring.presence_projection().wake_available);
        expiring
            .apply(RealtimeCoordinatorEvent::ExtendPresence {
                additional_minutes: 1,
            })
            .expect("extend");
        assert!(!expiring.duration_extension_required());
        assert!(!expiring.action_required());
        assert!(expiring.tick(MINUTE_MS + 1).is_empty());
    }

    #[test]
    fn backend_failure_requires_an_explicit_new_segment() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        let failed = state.active_identity().clone();
        state
            .apply(RealtimeCoordinatorEvent::BackendFailed)
            .expect("failure");
        assert!(state.action_required());
        assert!(!state.media_generation_enabled());
        assert!(!state.accepts_result(&failed.session_id, &failed.segment_id, failed.epoch));

        state
            .apply(RealtimeCoordinatorEvent::UserApprovedBackendChange {
                segment_id: "segment-2".to_owned(),
                backend: RealtimeBackendKind::LocalMiniCpmO45,
                cloud_provider: None,
                persona_digest: "a".repeat(64),
            })
            .expect("explicit continuation");
        assert!(!state.action_required());
        assert_eq!(state.active_segment().ordinal, 2);
        assert_eq!(
            state.active_segment().creation_reason,
            BackendSegmentCreationReason::UserApprovedContinuation
        );
    }

    #[test]
    fn local_backend_failure_allows_one_governed_recovery_segment() {
        let mut request = start_request();
        request.backend = RealtimeBackendKind::LocalMiniCpmO45;
        request.cloud_provider = None;
        let mut state = RealtimeCoordinatorState::start(request).expect("start");
        state
            .apply(RealtimeCoordinatorEvent::BackendFailed)
            .expect("failure");
        state
            .apply(RealtimeCoordinatorEvent::RecoverLocalSegment {
                segment_id: "segment-recovery".to_owned(),
                persona_digest: "a".repeat(64),
            })
            .expect("automatic recovery");
        assert!(!state.action_required());
        assert_eq!(state.active_identity().epoch, 1);
        assert_eq!(state.active_segment().ordinal, 2);
        assert_eq!(
            state.active_segment().creation_reason,
            BackendSegmentCreationReason::AutomaticRecovery
        );
        assert_eq!(
            state.apply(RealtimeCoordinatorEvent::RecoverLocalSegment {
                segment_id: "segment-recovery-2".to_owned(),
                persona_digest: "a".repeat(64),
            }),
            Err(RealtimeCoordinatorError::InvalidTransition)
        );
    }

    #[test]
    fn presence_projection_is_identity_and_persona_fenced() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        let initial = state.presence_projection();
        assert_eq!(initial.sequence, 1);
        assert_eq!(initial.state, RealtimePresenceState::Preparing);
        assert_eq!(initial.persona_digest, "a".repeat(64));

        let active = WorkerEvent::SessionState {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            status: "active".to_owned(),
            backend: RealtimeBackendKind::CloudLive,
            cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
            error_code: None,
        };
        let projected = state
            .project_worker_event(&active)
            .expect("active projection");
        assert_eq!(projected.sequence, 2);
        assert_eq!(projected.state, RealtimePresenceState::Standby);
        assert!(state.project_worker_event(&active).is_none());

        let stale = WorkerEvent::Presence {
            session_id: "session-1".to_owned(),
            segment_id: "stale-segment".to_owned(),
            context_epoch: 1,
            state: "speaking".to_owned(),
            level: Some(4),
        };
        assert!(state.project_worker_event(&stale).is_none());
        assert_eq!(state.presence_projection(), projected);
    }

    #[test]
    fn worker_presence_maps_to_the_bounded_coordinator_vocabulary() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        let listening = WorkerEvent::Presence {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            state: "listening".to_owned(),
            level: Some(7),
        };
        let projected = state
            .project_worker_event(&listening)
            .expect("listening projection");
        assert_eq!(projected.state, RealtimePresenceState::Listening);
        assert_eq!(projected.level, Some(7));

        let unknown = WorkerEvent::Presence {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            state: "provider_secret_state".to_owned(),
            level: Some(7),
        };
        assert!(state.project_worker_event(&unknown).is_none());
        assert_eq!(state.presence_projection(), projected);

        let pressure = WorkerEvent::ResourcePressure {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            code: "GPU_BUDGET_LOW".to_owned(),
        };
        assert_eq!(
            state
                .project_worker_event(&pressure)
                .expect("pressure projection")
                .state,
            RealtimePresenceState::ResourceLimited
        );
    }

    #[test]
    fn resource_policy_ack_projects_bounded_pressure_and_recovery_levels() {
        let mut state = RealtimeCoordinatorState::start(local_start_request()).expect("start");
        let identity = state.active_identity().clone();
        let high = WorkerEvent::ResourcePolicyApplied {
            session_id: identity.session_id.clone(),
            segment_id: identity.segment_id.clone(),
            context_epoch: identity.epoch,
            policy: fairy_realtime_worker::RealtimeResourcePolicy::for_level(
                RealtimeResourceLevel::High,
            ),
        };
        let projection = state.project_worker_event(&high).expect("high projection");
        assert_eq!(projection.state, RealtimePresenceState::ResourceLimited);
        assert_eq!(projection.level, Some(2));
        assert!(state.media_generation_enabled());

        let recovered = WorkerEvent::ResourcePolicyApplied {
            session_id: identity.session_id,
            segment_id: identity.segment_id,
            context_epoch: identity.epoch,
            policy: fairy_realtime_worker::RealtimeResourcePolicy::for_level(
                RealtimeResourceLevel::Normal,
            ),
        };
        let projection = state
            .project_worker_event(&recovered)
            .expect("normal projection");
        assert_eq!(projection.state, RealtimePresenceState::Observing);
        assert_eq!(projection.level, None);
    }

    #[test]
    fn fairy_speech_overlays_and_restores_the_latest_worker_presence() {
        let mut state = RealtimeCoordinatorState::start(start_request()).expect("start");
        let thinking = WorkerEvent::Presence {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            state: "thinking".to_owned(),
            level: None,
        };
        state
            .project_worker_event(&thinking)
            .expect("thinking projection");
        assert_eq!(
            state
                .project_fairy_speech(true)
                .expect("speaking projection")
                .state,
            RealtimePresenceState::Speaking
        );

        let searching = WorkerEvent::AssistanceRequest {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
            request_id: "request-1".to_owned(),
            public_intent: "Look up a public fact".to_owned(),
        };
        assert!(state.project_worker_event(&searching).is_none());
        assert_eq!(
            state.presence_projection().state,
            RealtimePresenceState::Speaking
        );
        assert_eq!(
            state
                .project_fairy_speech(false)
                .expect("restored projection")
                .state,
            RealtimePresenceState::Searching
        );

        state
            .project_fairy_speech(true)
            .expect("speaking projection");
        let barge_in = WorkerEvent::BargeIn {
            session_id: "session-1".to_owned(),
            segment_id: "segment-1".to_owned(),
            context_epoch: 1,
        };
        assert_eq!(
            state
                .project_worker_event(&barge_in)
                .expect("barge-in projection")
                .state,
            RealtimePresenceState::Listening
        );
        assert!(state.project_fairy_speech(false).is_none());
    }
}
