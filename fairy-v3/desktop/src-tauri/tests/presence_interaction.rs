use fairy_desktop_v3::presence_interaction::{
    CursorBand, PresenceInteractionPhase, PresenceInteractionSignal,
    PresenceInteractionStateMachine,
};

fn signal(sampled_at_ms: u64) -> PresenceInteractionSignal {
    PresenceInteractionSignal {
        sampled_at_ms,
        cursor_band: CursorBand::Active,
        active_dwell_ms: 0,
        cursor_speed_px_s: 80.0,
        pointer_over_input: false,
        input_focused: false,
        reduced_motion: false,
        suspended: false,
        repositioning: false,
    }
}

#[test]
fn configured_hover_dwell_starts_but_does_not_skip_the_visual_timeline() {
    let mut machine = PresenceInteractionStateMachine::new(0);
    let after_dwell = PresenceInteractionSignal {
        sampled_at_ms: 250,
        active_dwell_ms: 250,
        ..signal(250)
    };
    assert_eq!(
        machine.advance(after_dwell).phase,
        PresenceInteractionPhase::Aware
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                sampled_at_ms: 300,
                active_dwell_ms: 300,
                ..after_dwell
            })
            .phase,
        PresenceInteractionPhase::Aware,
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                sampled_at_ms: 350,
                active_dwell_ms: 350,
                ..after_dwell
            })
            .phase,
        PresenceInteractionPhase::Droplet,
    );
}

#[test]
fn normal_motion_uses_the_fixed_interaction_timeline() {
    let mut machine = PresenceInteractionStateMachine::new(0);
    assert_eq!(
        machine.advance(signal(0)).phase,
        PresenceInteractionPhase::Aware
    );
    assert_eq!(
        machine.advance(signal(99)).phase,
        PresenceInteractionPhase::Aware
    );
    assert_eq!(
        machine.advance(signal(100)).phase,
        PresenceInteractionPhase::Droplet
    );
    assert_eq!(
        machine.advance(signal(180)).phase,
        PresenceInteractionPhase::Stretching
    );
    assert_eq!(
        machine.advance(signal(300)).phase,
        PresenceInteractionPhase::InputReveal
    );
    assert_eq!(
        machine.advance(signal(519)).phase,
        PresenceInteractionPhase::InputReveal
    );
    assert_eq!(
        machine.advance(signal(520)).phase,
        PresenceInteractionPhase::Interactive
    );
}

#[test]
fn fast_pointer_passes_never_reuse_prior_dwell() {
    let mut machine = PresenceInteractionStateMachine::new(0);
    let fast = PresenceInteractionSignal {
        cursor_speed_px_s: 901.0,
        ..signal(0)
    };
    assert_eq!(machine.advance(fast).phase, PresenceInteractionPhase::Aware);
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                sampled_at_ms: 400,
                ..fast
            })
            .phase,
        PresenceInteractionPhase::Aware,
    );
    assert_eq!(
        machine.advance(signal(401)).phase,
        PresenceInteractionPhase::Aware
    );
    assert_eq!(
        machine.advance(signal(500)).phase,
        PresenceInteractionPhase::Aware
    );
    assert_eq!(
        machine.advance(signal(501)).phase,
        PresenceInteractionPhase::Droplet
    );
}

#[test]
fn interactive_surface_waits_for_leave_grace_then_runs_full_return() {
    let mut machine = PresenceInteractionStateMachine::new(0);
    machine.advance(signal(0));
    machine.advance(signal(520));
    let outside = PresenceInteractionSignal {
        cursor_band: CursorBand::Outside,
        cursor_speed_px_s: 0.0,
        ..signal(600)
    };
    assert_eq!(
        machine.advance(outside).phase,
        PresenceInteractionPhase::Interactive
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                sampled_at_ms: 949,
                ..outside
            })
            .phase,
        PresenceInteractionPhase::Interactive,
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                sampled_at_ms: 950,
                ..outside
            })
            .phase,
        PresenceInteractionPhase::Returning,
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                sampled_at_ms: 1_349,
                ..outside
            })
            .phase,
        PresenceInteractionPhase::Returning,
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                sampled_at_ms: 1_350,
                ..outside
            })
            .phase,
        PresenceInteractionPhase::Idle,
    );
}

#[test]
fn focused_input_prevents_automatic_return() {
    let mut machine = PresenceInteractionStateMachine::new(0);
    machine.advance(signal(0));
    machine.advance(signal(520));
    let focused = PresenceInteractionSignal {
        sampled_at_ms: 5_000,
        cursor_band: CursorBand::Outside,
        input_focused: true,
        ..signal(0)
    };
    assert_eq!(
        machine.advance(focused).phase,
        PresenceInteractionPhase::Interactive
    );
}

#[test]
fn reduced_motion_skips_liquid_shape_phases_and_uses_a_short_fade() {
    let mut machine = PresenceInteractionStateMachine::new(0);
    let reduced = |sampled_at_ms| PresenceInteractionSignal {
        reduced_motion: true,
        ..signal(sampled_at_ms)
    };
    assert_eq!(
        machine.advance(reduced(0)).phase,
        PresenceInteractionPhase::InputReveal
    );
    assert_eq!(
        machine.advance(reduced(199)).phase,
        PresenceInteractionPhase::InputReveal
    );
    assert_eq!(
        machine.advance(reduced(200)).phase,
        PresenceInteractionPhase::Interactive
    );
}

#[test]
fn suspended_and_repositioning_states_reset_partial_activation() {
    let mut machine = PresenceInteractionStateMachine::new(0);
    machine.advance(signal(0));
    assert_eq!(
        machine.advance(signal(180)).phase,
        PresenceInteractionPhase::Stretching
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                suspended: true,
                ..signal(181)
            })
            .phase,
        PresenceInteractionPhase::Suspended,
    );
    assert_eq!(
        machine.advance(signal(500)).phase,
        PresenceInteractionPhase::Aware
    );
    assert_eq!(
        machine
            .advance(PresenceInteractionSignal {
                repositioning: true,
                ..signal(501)
            })
            .phase,
        PresenceInteractionPhase::Repositioning,
    );
    assert_eq!(
        machine.advance(signal(800)).phase,
        PresenceInteractionPhase::Aware
    );
}
