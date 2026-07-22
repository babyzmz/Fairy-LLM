use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

const INCIDENT_WINDOW_MS: u64 = 5 * 60 * 1_000;
const CONTEXT_LOSS_LIMIT: usize = 2;
const HARD_FAILURE_LIMIT: usize = 3;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresenceRendererMode {
    Native,
    Liquid,
    Compatibility,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
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

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum PresenceRendererErrorCode {
    Webgl2Unavailable,
    Webgl2ContextError,
    WebglContextLost,
    ShaderInitializationFailed,
    Canvas2dUnavailable,
    NativeGpuUnavailable,
    NativeGpuStartFailed,
    NativeGpuUpdateFailed,
    NativeGpuRuntimeFailed,
    NativeGpuStopFailed,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresenceRendererRequestedMode {
    Auto,
    Liquid,
    Compatibility,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresenceActualRendererBackend {
    None,
    NativeLiquidGlass,
    WebglCompatibility,
    CanvasCompatibility,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresenceOpticsSource {
    None,
    HostBackdropIdentity,
    WebglTexture,
    Procedural,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PresenceRendererHealthReport {
    pub schema_version: u16,
    pub requested_mode: PresenceRendererRequestedMode,
    pub mode: PresenceRendererMode,
    pub actual_backend: PresenceActualRendererBackend,
    pub optics_source: PresenceOpticsSource,
    pub status: PresenceRendererStatus,
    pub error_code: Option<PresenceRendererErrorCode>,
    pub fallback_reason: Option<PresenceRendererErrorCode>,
    pub monitor_refresh_hz: u16,
    pub effective_fps: u16,
}

impl PresenceRendererHealthReport {
    pub fn is_valid(self) -> bool {
        self.schema_version == 2
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
    native_failures: VecDeque<u64>,
    compatibility_failures: VecDeque<u64>,
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
            match report.mode {
                PresenceRendererMode::Native => {
                    self.native_failures.push_back(now_ms);
                    if self.native_failures.len() >= HARD_FAILURE_LIMIT {
                        self.force_compatibility = true;
                    }
                }
                PresenceRendererMode::Liquid => {
                    self.force_compatibility = true;
                }
                PresenceRendererMode::Compatibility => {
                    self.compatibility_failures.push_back(now_ms);
                    if self.compatibility_failures.len() >= HARD_FAILURE_LIMIT {
                        self.session_disabled = true;
                    }
                }
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
        while self.native_failures.front().is_some_and(|at| *at < cutoff) {
            self.native_failures.pop_front();
        }
        while self
            .compatibility_failures
            .front()
            .is_some_and(|at| *at < cutoff)
        {
            self.compatibility_failures.pop_front();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn failed(mode: PresenceRendererMode) -> PresenceRendererHealthReport {
        PresenceRendererHealthReport {
            schema_version: 2,
            requested_mode: PresenceRendererRequestedMode::Liquid,
            mode,
            actual_backend: PresenceActualRendererBackend::None,
            optics_source: PresenceOpticsSource::None,
            status: PresenceRendererStatus::Failed,
            error_code: None,
            fallback_reason: None,
            monitor_refresh_hz: 0,
            effective_fps: 0,
        }
    }

    #[test]
    fn three_native_failures_disable_enhanced_optics_without_disabling_the_pet() {
        let mut supervisor = PresenceRendererSupervisor::default();
        assert_eq!(
            supervisor.observe_at(failed(PresenceRendererMode::Native), 1),
            PresenceRendererDirective::Continue
        );
        assert_eq!(
            supervisor.observe_at(failed(PresenceRendererMode::Native), 2),
            PresenceRendererDirective::Continue
        );
        assert_eq!(
            supervisor.observe_at(failed(PresenceRendererMode::Native), 3),
            PresenceRendererDirective::ForceCompatibility
        );
        assert!(!supervisor.session_disabled());
    }

    #[test]
    fn only_repeated_compatibility_failures_disable_the_pet_session() {
        let mut supervisor = PresenceRendererSupervisor::default();
        for now_ms in 1..HARD_FAILURE_LIMIT as u64 {
            assert_eq!(
                supervisor.observe_at(failed(PresenceRendererMode::Compatibility), now_ms),
                PresenceRendererDirective::Continue
            );
        }
        assert_eq!(
            supervisor.observe_at(
                failed(PresenceRendererMode::Compatibility),
                HARD_FAILURE_LIMIT as u64,
            ),
            PresenceRendererDirective::DisablePet
        );
    }

    #[test]
    fn expired_native_failures_do_not_poison_a_later_session() {
        let mut supervisor = PresenceRendererSupervisor::default();
        assert_eq!(
            supervisor.observe_at(failed(PresenceRendererMode::Native), 1),
            PresenceRendererDirective::Continue
        );
        assert_eq!(
            supervisor.observe_at(failed(PresenceRendererMode::Native), 2),
            PresenceRendererDirective::Continue
        );
        assert_eq!(
            supervisor.observe_at(failed(PresenceRendererMode::Native), INCIDENT_WINDOW_MS + 3,),
            PresenceRendererDirective::Continue
        );
    }
}
