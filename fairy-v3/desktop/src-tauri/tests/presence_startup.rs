use std::time::Duration;

use fairy_desktop_v3::presence_startup::PresenceStartupGate;

#[test]
fn transient_failure_releases_gate_and_uses_bounded_backoff() {
    let gate = PresenceStartupGate::default();
    let expected = [250, 500, 1_000, 2_000, 4_000];

    for delay_ms in expected {
        assert!(gate.try_begin());
        assert!(!gate.try_begin());
        assert_eq!(gate.failed(), Some(Duration::from_millis(delay_ms)));
    }

    assert!(gate.try_begin());
    assert_eq!(gate.failed(), None);
    assert!(gate.try_begin(), "an explicit later trigger may retry");
}

#[test]
fn success_closes_retry_sequence_without_duplicate_initialization() {
    let gate = PresenceStartupGate::default();
    assert!(gate.try_begin());
    assert_eq!(gate.failed(), Some(Duration::from_millis(250)));
    assert!(gate.try_begin());

    gate.succeeded();

    assert!(!gate.try_begin());
    assert!(gate.ready());
    assert_eq!(gate.failure_count(), 0);
}
