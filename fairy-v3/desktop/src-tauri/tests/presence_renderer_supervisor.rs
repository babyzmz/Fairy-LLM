use fairy_desktop_v3::presence_renderer_supervisor::{
    PresenceRendererDirective, PresenceRendererErrorCode, PresenceRendererHealthReport,
    PresenceRendererMode, PresenceRendererStatus, PresenceRendererSupervisor,
};

fn report(
    mode: PresenceRendererMode,
    status: PresenceRendererStatus,
    error_code: Option<PresenceRendererErrorCode>,
) -> PresenceRendererHealthReport {
    PresenceRendererHealthReport {
        schema_version: 1,
        mode,
        status,
        error_code,
    }
}

#[test]
fn repeated_context_loss_forces_compatibility_for_the_session() {
    let mut supervisor = PresenceRendererSupervisor::default();
    let lost = report(
        PresenceRendererMode::Liquid,
        PresenceRendererStatus::ContextLost,
        Some(PresenceRendererErrorCode::WebglContextLost),
    );
    assert_eq!(
        supervisor.observe_at(lost, 1_000),
        PresenceRendererDirective::Continue
    );
    assert_eq!(
        supervisor.observe_at(lost, 299_000),
        PresenceRendererDirective::ForceCompatibility
    );
    assert_eq!(
        supervisor.observe_at(
            report(
                PresenceRendererMode::Compatibility,
                PresenceRendererStatus::Running,
                None,
            ),
            300_000,
        ),
        PresenceRendererDirective::ForceCompatibility
    );
}

#[test]
fn failures_outside_the_window_do_not_trip_the_context_circuit() {
    let mut supervisor = PresenceRendererSupervisor::default();
    let lost = report(
        PresenceRendererMode::Liquid,
        PresenceRendererStatus::ContextLost,
        Some(PresenceRendererErrorCode::WebglContextLost),
    );
    assert_eq!(
        supervisor.observe_at(lost, 1_000),
        PresenceRendererDirective::Continue
    );
    assert_eq!(
        supervisor.observe_at(lost, 302_000),
        PresenceRendererDirective::Continue
    );
}

#[test]
fn three_hard_failures_disable_only_the_pet_session() {
    let mut supervisor = PresenceRendererSupervisor::default();
    let failed = report(
        PresenceRendererMode::Compatibility,
        PresenceRendererStatus::Failed,
        Some(PresenceRendererErrorCode::Canvas2dUnavailable),
    );
    assert_eq!(
        supervisor.observe_at(failed, 10),
        PresenceRendererDirective::Continue
    );
    assert_eq!(
        supervisor.observe_at(failed, 20),
        PresenceRendererDirective::Continue
    );
    assert_eq!(
        supervisor.observe_at(failed, 30),
        PresenceRendererDirective::DisablePet
    );
    assert!(supervisor.session_disabled());
}
