use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

const INCIDENT_WINDOW_MS: u64 = 5 * 60 * 1_000;
const CONTEXT_LOSS_LIMIT: usize = 2;
const HARD_FAILURE_LIMIT: usize = 3;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum PresenceRendererMode {
    Liquid,
    Compatibility,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum PresenceRendererStatus {
    Initializing,
    Running,
    Stopped,
    Suspended,
    ContextLost,
    Fallback,
    Failed,
    Disposed,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum PresenceRendererErrorCode {
    Webgl2Unavailable,
    Webgl2ContextError,
    WebglContextLost,
    ShaderInitializationFailed,
    Canvas2dUnavailable,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PresenceRendererHealthReport {
    pub schema_version: u16,
    pub mode: PresenceRendererMode,
    pub status: PresenceRendererStatus,
    pub error_code: Option<PresenceRendererErrorCode>,
}

impl PresenceRendererHealthReport {
    pub fn is_valid(self) -> bool {
        self.schema_version == 1
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresenceRendererDirective {
    Continue,
    ForceCompatibility,
    DisablePet,
}

#[derive(Default)]
pub struct PresenceRendererSupervisor {
    context_losses: VecDeque<u64>,
    hard_failures: VecDeque<u64>,
    force_compatibility: bool,
    session_disabled: bool,
}

impl PresenceRendererSupervisor {
    pub fn observe_at(
        &mut self,
        report: PresenceRendererHealthReport,
        now_ms: u64,
    ) -> PresenceRendererDirective {
        if self.session_disabled {
            return PresenceRendererDirective::DisablePet;
        }
        self.prune(now_ms);

        if report.status == PresenceRendererStatus::ContextLost {
            self.context_losses.push_back(now_ms);
            if self.context_losses.len() >= CONTEXT_LOSS_LIMIT {
                self.force_compatibility = true;
            }
        }
        if report.status == PresenceRendererStatus::Failed {
            self.hard_failures.push_back(now_ms);
            if self.hard_failures.len() >= HARD_FAILURE_LIMIT {
                self.session_disabled = true;
            }
        }

        if self.session_disabled {
            PresenceRendererDirective::DisablePet
        } else if self.force_compatibility {
            PresenceRendererDirective::ForceCompatibility
        } else {
            PresenceRendererDirective::Continue
        }
    }

    pub fn session_disabled(&self) -> bool {
        self.session_disabled
    }

    fn prune(&mut self, now_ms: u64) {
        let cutoff = now_ms.saturating_sub(INCIDENT_WINDOW_MS);
        while self.context_losses.front().is_some_and(|at| *at < cutoff) {
            self.context_losses.pop_front();
        }
        while self.hard_failures.front().is_some_and(|at| *at < cutoff) {
            self.hard_failures.pop_front();
        }
    }
}
