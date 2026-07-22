use serde::Serialize;

pub const FAST_PASS_THRESHOLD_PX_S: f64 = 900.0;
pub const POINTER_LEAVE_GRACE_MS: u64 = 350;
const AWARE_DURATION_MS: u64 = 100;
const DROPLET_DURATION_END_MS: u64 = 180;
const STRETCHING_DURATION_END_MS: u64 = 300;
const INTERACTIVE_AT_MS: u64 = 520;
const REDUCED_MOTION_INTERACTIVE_AT_MS: u64 = 200;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CursorBand {
    Outside,
    Aware,
    Active,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresenceInteractionPhase {
    Idle,
    Aware,
    Droplet,
    Stretching,
    InputReveal,
    Interactive,
    Returning,
    Suspended,
    Repositioning,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PresenceInteractionSignal {
    pub sampled_at_ms: u64,
    pub cursor_band: CursorBand,
    pub active_dwell_ms: u64,
    pub cursor_speed_px_s: f64,
    pub pointer_over_input: bool,
    pub reduced_motion: bool,
    pub suspended: bool,
    pub repositioning: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PresencePhaseSnapshot {
    pub phase: PresenceInteractionPhase,
    pub phase_started_at_ms: u64,
}

pub struct PresenceInteractionStateMachine {
    phase: PresenceInteractionPhase,
    phase_started_at_ms: u64,
    activation_started_at_ms: Option<u64>,
    pointer_left_at_ms: Option<u64>,
}

impl PresenceInteractionStateMachine {
    pub fn new(started_at_ms: u64) -> Self {
        Self {
            phase: PresenceInteractionPhase::Idle,
            phase_started_at_ms: started_at_ms,
            activation_started_at_ms: None,
            pointer_left_at_ms: None,
        }
    }

    pub fn advance(&mut self, signal: PresenceInteractionSignal) -> PresencePhaseSnapshot {
        if signal.suspended {
            self.reset_candidate();
            self.transition(PresenceInteractionPhase::Suspended, signal.sampled_at_ms);
            return self.snapshot();
        }
        if signal.repositioning {
            self.reset_candidate();
            self.transition(
                PresenceInteractionPhase::Repositioning,
                signal.sampled_at_ms,
            );
            return self.snapshot();
        }
        if matches!(
            self.phase,
            PresenceInteractionPhase::Suspended | PresenceInteractionPhase::Repositioning
        ) {
            self.reset_candidate();
            self.transition(
                if signal.cursor_band == CursorBand::Outside {
                    PresenceInteractionPhase::Idle
                } else {
                    PresenceInteractionPhase::Aware
                },
                signal.sampled_at_ms,
            );
        }

        if self.phase == PresenceInteractionPhase::Returning {
            if signal.pointer_over_input || signal.cursor_band != CursorBand::Outside {
                self.reset_candidate();
                self.transition(PresenceInteractionPhase::Aware, signal.sampled_at_ms);
            } else {
                let duration = if signal.reduced_motion { 200 } else { 400 };
                if signal
                    .sampled_at_ms
                    .saturating_sub(self.phase_started_at_ms)
                    >= duration
                {
                    self.transition(PresenceInteractionPhase::Idle, signal.sampled_at_ms);
                }
                return self.snapshot();
            }
        }

        if matches!(
            self.phase,
            PresenceInteractionPhase::InputReveal | PresenceInteractionPhase::Interactive
        ) {
            if signal.pointer_over_input || signal.cursor_band != CursorBand::Outside {
                self.pointer_left_at_ms = None;
            } else {
                let left_at = *self.pointer_left_at_ms.get_or_insert(signal.sampled_at_ms);
                if signal.sampled_at_ms.saturating_sub(left_at) >= POINTER_LEAVE_GRACE_MS {
                    self.reset_candidate();
                    self.transition(PresenceInteractionPhase::Returning, signal.sampled_at_ms);
                    return self.snapshot();
                }
            }

            if self.phase == PresenceInteractionPhase::InputReveal {
                let activation_elapsed = self
                    .activation_started_at_ms
                    .map(|started| signal.sampled_at_ms.saturating_sub(started))
                    .unwrap_or_default();
                let interactive_at = if signal.reduced_motion {
                    REDUCED_MOTION_INTERACTIVE_AT_MS
                } else {
                    INTERACTIVE_AT_MS
                };
                if activation_elapsed >= interactive_at {
                    self.transition(PresenceInteractionPhase::Interactive, signal.sampled_at_ms);
                }
            }
            return self.snapshot();
        }

        if signal.cursor_band == CursorBand::Outside {
            self.reset_candidate();
            self.transition(PresenceInteractionPhase::Idle, signal.sampled_at_ms);
            return self.snapshot();
        }

        if signal.cursor_band != CursorBand::Active
            || signal.cursor_speed_px_s > FAST_PASS_THRESHOLD_PX_S
        {
            self.reset_candidate();
            self.transition(PresenceInteractionPhase::Aware, signal.sampled_at_ms);
            return self.snapshot();
        }

        let activation_started = *self
            .activation_started_at_ms
            .get_or_insert(signal.sampled_at_ms);
        let elapsed = signal.sampled_at_ms.saturating_sub(activation_started);
        let next = if signal.reduced_motion {
            if elapsed >= REDUCED_MOTION_INTERACTIVE_AT_MS {
                PresenceInteractionPhase::Interactive
            } else {
                PresenceInteractionPhase::InputReveal
            }
        } else if elapsed >= INTERACTIVE_AT_MS {
            PresenceInteractionPhase::Interactive
        } else if elapsed >= STRETCHING_DURATION_END_MS {
            PresenceInteractionPhase::InputReveal
        } else if elapsed >= DROPLET_DURATION_END_MS {
            PresenceInteractionPhase::Stretching
        } else if elapsed >= AWARE_DURATION_MS {
            PresenceInteractionPhase::Droplet
        } else {
            PresenceInteractionPhase::Aware
        };
        self.transition(next, signal.sampled_at_ms);
        self.snapshot()
    }

    pub fn suspend(&mut self, sampled_at_ms: u64) {
        self.reset_candidate();
        self.transition(PresenceInteractionPhase::Suspended, sampled_at_ms);
    }

    fn reset_candidate(&mut self) {
        self.activation_started_at_ms = None;
        self.pointer_left_at_ms = None;
    }

    fn transition(&mut self, phase: PresenceInteractionPhase, sampled_at_ms: u64) {
        if self.phase == phase {
            return;
        }
        self.phase = phase;
        self.phase_started_at_ms = sampled_at_ms;
    }

    fn snapshot(&self) -> PresencePhaseSnapshot {
        PresencePhaseSnapshot {
            phase: self.phase,
            phase_started_at_ms: self.phase_started_at_ms,
        }
    }
}
