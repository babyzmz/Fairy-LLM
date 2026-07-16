use std::sync::atomic::{AtomicU8, Ordering};
use std::time::Duration;

const IDLE: u8 = 0;
const STARTING: u8 = 1;
const READY: u8 = 2;
const RETRY_DELAYS_MS: [u64; 5] = [250, 500, 1_000, 2_000, 4_000];

pub struct PresenceStartupGate {
    state: AtomicU8,
    failures: AtomicU8,
}

impl Default for PresenceStartupGate {
    fn default() -> Self {
        Self {
            state: AtomicU8::new(IDLE),
            failures: AtomicU8::new(0),
        }
    }
}

impl PresenceStartupGate {
    pub fn try_begin(&self) -> bool {
        self.state
            .compare_exchange(IDLE, STARTING, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    }

    pub fn succeeded(&self) {
        self.failures.store(0, Ordering::Release);
        self.state.store(READY, Ordering::Release);
    }

    pub fn failed(&self) -> Option<Duration> {
        if self
            .state
            .compare_exchange(STARTING, IDLE, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return None;
        }
        let previous = self
            .failures
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| {
                Some(value.saturating_add(1))
            })
            .unwrap_or(u8::MAX);
        let failure = previous.saturating_add(1);
        RETRY_DELAYS_MS
            .get(usize::from(failure.saturating_sub(1)))
            .copied()
            .map(Duration::from_millis)
    }

    pub fn ready(&self) -> bool {
        self.state.load(Ordering::Acquire) == READY
    }

    pub fn failure_count(&self) -> u8 {
        self.failures.load(Ordering::Acquire)
    }
}
