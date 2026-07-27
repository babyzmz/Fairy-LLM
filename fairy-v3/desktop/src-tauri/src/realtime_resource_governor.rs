use fairy_realtime_worker::{RealtimeResourceLevel, RealtimeResourcePolicy};
use serde::{Deserialize, Serialize};
use thiserror::Error;

const MIN_ESCALATION_DWELL_MS: u64 = 2_000;
const MIN_RECOVERY_DWELL_MS: u64 = 5_000;
const RECOVERY_SAMPLE_COUNT: u8 = 5;
const MAX_INFERENCE_LATENCY_MS: u32 = 60_000;
const MAX_CAPTURE_BACKLOG: u16 = 120;
const MAX_ALLOCATION_FAILURES: u8 = 32;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RealtimeResourceSample {
    pub budget_bytes: Option<u64>,
    pub current_usage_bytes: Option<u64>,
    pub allocation_failure_count: u8,
    pub inference_latency_ms: u32,
    pub capture_frame_backlog: u16,
    pub device_removed: bool,
    pub renderer_healthy: bool,
    pub target_changed: bool,
}

impl RealtimeResourceSample {
    fn is_valid(self) -> bool {
        let memory_valid = match (self.budget_bytes, self.current_usage_bytes) {
            (Some(budget), Some(usage)) => budget > 0 && usage <= budget.saturating_mul(4),
            (None, None) => true,
            _ => false,
        };
        memory_valid
            && self.allocation_failure_count <= MAX_ALLOCATION_FAILURES
            && self.inference_latency_ms <= MAX_INFERENCE_LATENCY_MS
            && self.capture_frame_backlog <= MAX_CAPTURE_BACKLOG
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeResourceSnapshot {
    pub policy: RealtimeResourcePolicy,
    pub recovery_samples: u8,
    pub sample_count: u64,
}

#[derive(Clone, Copy, Debug, Error, Eq, PartialEq)]
pub enum RealtimeResourceGovernorError {
    #[error("the realtime resource sample is invalid")]
    InvalidSample,
    #[error("the realtime resource sample clock moved backwards")]
    NonMonotonicClock,
}

#[derive(Clone, Debug)]
pub struct RealtimeResourceGovernor {
    level: RealtimeResourceLevel,
    entered_at_ms: u64,
    recovery_samples: u8,
    sample_count: u64,
    last_sample_at_ms: Option<u64>,
}

impl RealtimeResourceGovernor {
    pub fn new(now_ms: u64) -> Self {
        Self {
            level: RealtimeResourceLevel::Normal,
            entered_at_ms: now_ms,
            recovery_samples: 0,
            sample_count: 0,
            last_sample_at_ms: None,
        }
    }

    pub fn snapshot(&self) -> RealtimeResourceSnapshot {
        RealtimeResourceSnapshot {
            policy: RealtimeResourcePolicy::for_level(self.level),
            recovery_samples: self.recovery_samples,
            sample_count: self.sample_count,
        }
    }

    pub fn observe(
        &mut self,
        sample: RealtimeResourceSample,
        now_ms: u64,
    ) -> Result<Option<RealtimeResourcePolicy>, RealtimeResourceGovernorError> {
        if !sample.is_valid() {
            return Err(RealtimeResourceGovernorError::InvalidSample);
        }
        if self
            .last_sample_at_ms
            .is_some_and(|previous| now_ms < previous)
        {
            return Err(RealtimeResourceGovernorError::NonMonotonicClock);
        }
        self.last_sample_at_ms = Some(now_ms);
        self.sample_count = self.sample_count.saturating_add(1);

        if self.level == RealtimeResourceLevel::DeviceRemoved {
            return Ok(None);
        }
        let candidate = classify(sample);
        if candidate == RealtimeResourceLevel::DeviceRemoved {
            return Ok(Some(self.transition(candidate, now_ms)));
        }

        if rank(candidate) > rank(self.level) {
            self.recovery_samples = 0;
            let urgent = candidate == RealtimeResourceLevel::Critical;
            if urgent || now_ms.saturating_sub(self.entered_at_ms) >= MIN_ESCALATION_DWELL_MS {
                return Ok(Some(self.transition(candidate, now_ms)));
            }
            return Ok(None);
        }
        if candidate == self.level {
            self.recovery_samples = 0;
            return Ok(None);
        }

        if sample.target_changed || !safe_to_recover(self.level, sample) {
            self.recovery_samples = 0;
            return Ok(None);
        }
        self.recovery_samples = self.recovery_samples.saturating_add(1);
        if self.recovery_samples < RECOVERY_SAMPLE_COUNT
            || now_ms.saturating_sub(self.entered_at_ms) < MIN_RECOVERY_DWELL_MS
        {
            return Ok(None);
        }
        Ok(Some(self.transition(candidate, now_ms)))
    }

    fn transition(&mut self, level: RealtimeResourceLevel, now_ms: u64) -> RealtimeResourcePolicy {
        self.level = level;
        self.entered_at_ms = now_ms;
        self.recovery_samples = 0;
        RealtimeResourcePolicy::for_level(level)
    }
}

fn classify(sample: RealtimeResourceSample) -> RealtimeResourceLevel {
    if sample.device_removed {
        return RealtimeResourceLevel::DeviceRemoved;
    }
    let mut level = memory_level(sample);
    level = maximum(
        level,
        match sample.allocation_failure_count {
            0 => RealtimeResourceLevel::Normal,
            1 => RealtimeResourceLevel::Pressure,
            2 => RealtimeResourceLevel::High,
            _ => RealtimeResourceLevel::Critical,
        },
    );
    level = maximum(
        level,
        match sample.inference_latency_ms {
            0..1_000 => RealtimeResourceLevel::Normal,
            1_000..2_000 => RealtimeResourceLevel::Pressure,
            2_000..4_000 => RealtimeResourceLevel::High,
            _ => RealtimeResourceLevel::Critical,
        },
    );
    level = maximum(
        level,
        match sample.capture_frame_backlog {
            0..2 => RealtimeResourceLevel::Normal,
            2..4 => RealtimeResourceLevel::Pressure,
            4..8 => RealtimeResourceLevel::High,
            _ => RealtimeResourceLevel::Critical,
        },
    );
    if !sample.renderer_healthy {
        level = maximum(level, RealtimeResourceLevel::High);
    }
    level
}

fn memory_level(sample: RealtimeResourceSample) -> RealtimeResourceLevel {
    let Some((budget, usage)) = sample.budget_bytes.zip(sample.current_usage_bytes) else {
        return RealtimeResourceLevel::Normal;
    };
    let basis_points = u128::from(usage)
        .saturating_mul(10_000)
        .checked_div(u128::from(budget))
        .unwrap_or(u128::MAX);
    match basis_points {
        0..8_000 => RealtimeResourceLevel::Normal,
        8_000..9_000 => RealtimeResourceLevel::Pressure,
        9_000..9_600 => RealtimeResourceLevel::High,
        _ => RealtimeResourceLevel::Critical,
    }
}

fn safe_to_recover(level: RealtimeResourceLevel, sample: RealtimeResourceSample) -> bool {
    if sample.device_removed || !sample.renderer_healthy {
        return false;
    }
    let memory_safe = sample
        .budget_bytes
        .zip(sample.current_usage_bytes)
        .is_none_or(|(budget, usage)| {
            let limit = match level {
                RealtimeResourceLevel::Pressure => 7_500_u128,
                RealtimeResourceLevel::High => 8_500,
                RealtimeResourceLevel::Critical => 9_200,
                RealtimeResourceLevel::Normal | RealtimeResourceLevel::DeviceRemoved => 0,
            };
            u128::from(usage).saturating_mul(10_000) < u128::from(budget).saturating_mul(limit)
        });
    let signal_safe = match level {
        RealtimeResourceLevel::Pressure => {
            sample.allocation_failure_count == 0
                && sample.inference_latency_ms < 800
                && sample.capture_frame_backlog < 2
        }
        RealtimeResourceLevel::High => {
            sample.allocation_failure_count < 2
                && sample.inference_latency_ms < 1_600
                && sample.capture_frame_backlog < 4
        }
        RealtimeResourceLevel::Critical => {
            sample.allocation_failure_count < 3
                && sample.inference_latency_ms < 3_000
                && sample.capture_frame_backlog < 8
        }
        RealtimeResourceLevel::Normal | RealtimeResourceLevel::DeviceRemoved => false,
    };
    memory_safe && signal_safe
}

const fn maximum(
    left: RealtimeResourceLevel,
    right: RealtimeResourceLevel,
) -> RealtimeResourceLevel {
    if rank(left) >= rank(right) {
        left
    } else {
        right
    }
}

const fn rank(level: RealtimeResourceLevel) -> u8 {
    match level {
        RealtimeResourceLevel::Normal => 0,
        RealtimeResourceLevel::Pressure => 1,
        RealtimeResourceLevel::High => 2,
        RealtimeResourceLevel::Critical => 3,
        RealtimeResourceLevel::DeviceRemoved => 4,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(percent: u64) -> RealtimeResourceSample {
        RealtimeResourceSample {
            budget_bytes: Some(100),
            current_usage_bytes: Some(percent),
            allocation_failure_count: 0,
            inference_latency_ms: 10,
            capture_frame_backlog: 0,
            device_removed: false,
            renderer_healthy: true,
            target_changed: false,
        }
    }

    #[test]
    fn thresholds_escalate_only_after_dwell_except_critical() {
        let mut governor = RealtimeResourceGovernor::new(0);
        assert_eq!(governor.observe(sample(80), 1_000).unwrap(), None);
        assert_eq!(
            governor.observe(sample(80), 2_000).unwrap(),
            Some(RealtimeResourcePolicy::for_level(
                RealtimeResourceLevel::Pressure
            ))
        );

        let mut critical = RealtimeResourceGovernor::new(0);
        assert_eq!(
            critical.observe(sample(96), 1).unwrap(),
            Some(RealtimeResourcePolicy::for_level(
                RealtimeResourceLevel::Critical
            ))
        );
    }

    #[test]
    fn recovery_requires_lower_exit_threshold_and_five_samples() {
        let mut governor = RealtimeResourceGovernor::new(0);
        governor.observe(sample(96), 1).unwrap();
        for index in 0..4 {
            assert_eq!(
                governor.observe(sample(70), 5_000 + index * 1_000).unwrap(),
                None
            );
        }
        assert_eq!(
            governor.observe(sample(70), 9_000).unwrap(),
            Some(RealtimeResourcePolicy::for_level(
                RealtimeResourceLevel::Normal
            ))
        );
    }

    #[test]
    fn composite_signals_raise_level_and_target_change_breaks_recovery() {
        let mut governor = RealtimeResourceGovernor::new(0);
        let mut pressured = sample(20);
        pressured.capture_frame_backlog = 4;
        assert_eq!(
            governor.observe(pressured, 2_000).unwrap(),
            Some(RealtimeResourcePolicy::for_level(
                RealtimeResourceLevel::High
            ))
        );
        let mut recovery = sample(20);
        recovery.target_changed = true;
        for now in [7_000, 8_000, 9_000, 10_000, 11_000] {
            assert_eq!(governor.observe(recovery, now).unwrap(), None);
        }
        assert_eq!(governor.snapshot().recovery_samples, 0);
    }

    #[test]
    fn device_removal_is_terminal_and_never_selects_a_fallback() {
        let mut governor = RealtimeResourceGovernor::new(0);
        let mut removed = sample(20);
        removed.device_removed = true;
        let policy = governor.observe(removed, 1).unwrap().unwrap();
        assert_eq!(policy.level, RealtimeResourceLevel::DeviceRemoved);
        assert!(policy.media_paused);
        assert_eq!(governor.observe(sample(10), 10_000).unwrap(), None);
    }
}
