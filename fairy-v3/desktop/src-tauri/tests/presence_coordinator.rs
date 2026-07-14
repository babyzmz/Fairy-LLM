use fairy_desktop_v3::presence_coordinator::{
    anchor_from_ratios, anchor_ratios, configured_cursor_band, resolve_presence_placement,
    resolve_presence_placement_for_anchor, select_work_area, CursorBand, CursorMetrics,
    CursorSamplingHealth, CursorTracker, ExpansionDirection, InteractionEmissionGate,
    NormalizedDirection, PhysicalFrame, PhysicalPoint, RuntimePolicyEmissionGate,
};
use fairy_desktop_v3::presence_interaction::PresenceInteractionPhase;
use fairy_desktop_v3::presence_runtime::PresenceRuntimePolicy;

#[test]
fn cursor_tracker_reports_speed_distance_and_continuous_active_dwell() {
    let mut tracker = CursorTracker::default();
    let anchor = PhysicalPoint { x: 0, y: 0 };
    let first = tracker.observe(PhysicalPoint { x: 120, y: 0 }, 1_000, anchor, 1.0);
    assert_eq!(first.band, CursorBand::Active);
    assert_eq!(first.dwell_ms, 0);
    assert_eq!(first.speed_px_s, 0.0);

    let second = tracker.observe(PhysicalPoint { x: 60, y: 0 }, 1_060, anchor, 1.0);
    assert_eq!(second.band, CursorBand::Active);
    assert_eq!(second.dwell_ms, 60);
    assert!((second.speed_px_s - 1_000.0).abs() < 0.01);
    assert_eq!((second.direction.x, second.direction.y), (1.0, 0.0));

    let outside = tracker.observe(PhysicalPoint { x: 400, y: 0 }, 1_160, anchor, 1.0);
    assert_eq!(outside.band, CursorBand::Outside);
    assert_eq!(outside.dwell_ms, 0);
    let returned = tracker.observe(PhysicalPoint { x: 40, y: 0 }, 1_260, anchor, 1.0);
    assert_eq!(returned.dwell_ms, 0);
}

#[test]
fn placement_flips_left_near_the_right_edge_and_preserves_the_core_anchor() {
    let work_area = PhysicalFrame {
        x: 0,
        y: 0,
        width: 1920,
        height: 1040,
    };
    let render = PhysicalFrame {
        x: 1500,
        y: 500,
        width: 640,
        height: 260,
    };
    let placement =
        resolve_presence_placement(render, work_area, 1.0, Some(ExpansionDirection::Right));

    assert_eq!(placement.expansion_direction, ExpansionDirection::Left);
    assert_eq!(placement.anchor, PhysicalPoint { x: 1596, y: 630 });
    assert_eq!(placement.render_frame.x, 1052);
    assert_eq!(placement.input_compact_frame.x, 1052);
    assert_eq!(placement.input_expanded_frame.x, 1052);
    assert_eq!(
        placement.core_frame(144),
        PhysicalFrame {
            x: 1524,
            y: 558,
            width: 144,
            height: 144,
        }
    );
}

#[test]
fn placement_supports_negative_monitor_coordinates_and_work_area_clamping() {
    let work_area = PhysicalFrame {
        x: -1920,
        y: -120,
        width: 1920,
        height: 1080,
    };
    let render = PhysicalFrame {
        x: -1880,
        y: 850,
        width: 640,
        height: 260,
    };
    let placement = resolve_presence_placement(render, work_area, 1.0, None);

    assert_eq!(placement.expansion_direction, ExpansionDirection::Right);
    assert_eq!(placement.render_frame.x, -1880);
    assert_eq!(placement.render_frame.y, 700);
    assert!(placement.anchor.x < 0);
    assert_eq!(placement.anchor.y, 830);
}

#[test]
fn placement_scales_anchor_and_input_frames_in_physical_pixels() {
    let placement = resolve_presence_placement(
        PhysicalFrame {
            x: 300,
            y: 150,
            width: 960,
            height: 390,
        },
        PhysicalFrame {
            x: 0,
            y: 0,
            width: 3840,
            height: 2080,
        },
        1.5,
        None,
    );

    assert_eq!(placement.anchor, PhysicalPoint { x: 444, y: 432 });
    assert_eq!(placement.input_compact_frame.width, 924);
    assert_eq!(placement.input_compact_frame.height, 216);
    assert_eq!(placement.input_expanded_frame.width, 924);
    assert_eq!(placement.input_expanded_frame.height, 540);
    assert!(placement.input_expanded_frame.y >= placement.monitor_work_area.y);
    assert!(
        placement.input_expanded_frame.y + placement.input_expanded_frame.height as i32
            <= placement.monitor_work_area.y + placement.monitor_work_area.height as i32
    );
}

#[test]
fn disconnected_monitor_falls_back_to_the_nearest_remaining_work_area() {
    let primary = PhysicalFrame {
        x: 0,
        y: 0,
        width: 1920,
        height: 1040,
    };
    let left = PhysicalFrame {
        x: -1280,
        y: 80,
        width: 1280,
        height: 944,
    };

    assert_eq!(
        select_work_area(PhysicalPoint { x: 2_600, y: 500 }, &[left, primary]),
        Some(primary),
    );
    assert_eq!(select_work_area(PhysicalPoint { x: 0, y: 0 }, &[]), None);
}

#[test]
fn physical_anchor_round_trips_across_negative_monitor_coordinates() {
    let work_area = PhysicalFrame {
        x: -2560,
        y: -220,
        width: 2560,
        height: 1400,
    };
    let anchor = PhysicalPoint { x: -640, y: 830 };
    let (x_ratio, y_ratio) = anchor_ratios(anchor, work_area);
    assert!((x_ratio - 0.75).abs() < 0.001);
    assert!((y_ratio - 0.75).abs() < 0.001);
    assert_eq!(anchor_from_ratios(work_area, x_ratio, y_ratio), anchor);
}

#[test]
fn requested_anchor_is_preserved_when_dpi_and_edge_force_left_expansion() {
    let work_area = PhysicalFrame {
        x: 0,
        y: 0,
        width: 3840,
        height: 2080,
    };
    let placement = resolve_presence_placement_for_anchor(
        PhysicalPoint { x: 3600, y: 1500 },
        (960, 390),
        work_area,
        1.5,
        Some(ExpansionDirection::Right),
    );
    assert_eq!(placement.expansion_direction, ExpansionDirection::Left);
    assert_eq!(placement.anchor, PhysicalPoint { x: 3600, y: 1500 });
    assert!(placement.render_frame.x >= work_area.x);
    assert!(placement.render_frame.y >= work_area.y);
}

#[test]
fn hover_preferences_gate_activation_without_hiding_cursor_metrics() {
    let mut tracker = CursorTracker::default();
    let anchor = PhysicalPoint { x: 0, y: 0 };
    let first = tracker.observe(PhysicalPoint { x: 40, y: 0 }, 1_000, anchor, 1.0);
    let settled = tracker.observe(PhysicalPoint { x: 40, y: 0 }, 1_300, anchor, 1.0);
    assert_eq!(configured_cursor_band(first, true, 250), CursorBand::Aware);
    assert_eq!(
        configured_cursor_band(settled, true, 250),
        CursorBand::Active
    );
    assert_eq!(
        configured_cursor_band(settled, false, 250),
        CursorBand::Outside
    );
    assert_eq!(settled.band, CursorBand::Active);
}

#[test]
fn idle_interaction_emits_only_changes_and_recovery_heartbeats() {
    let placement = resolve_presence_placement(
        PhysicalFrame {
            x: 0,
            y: 0,
            width: 640,
            height: 260,
        },
        PhysicalFrame {
            x: 0,
            y: 0,
            width: 1920,
            height: 1040,
        },
        1.0,
        None,
    );
    let mut gate = InteractionEmissionGate::default();
    let idle_point = PhysicalPoint { x: 0, y: 0 };
    assert!(gate.should_emit(
        0,
        PresenceInteractionPhase::Idle,
        cursor_metrics(CursorBand::Outside, idle_point),
        false,
        false,
        placement,
    ));
    assert!(!gate.should_emit(
        50,
        PresenceInteractionPhase::Idle,
        cursor_metrics(CursorBand::Outside, idle_point),
        false,
        false,
        placement,
    ));
    assert!(!gate.should_emit(
        1_000,
        PresenceInteractionPhase::Idle,
        cursor_metrics(CursorBand::Outside, idle_point),
        false,
        false,
        placement,
    ));
    assert!(gate.should_emit(
        30_000,
        PresenceInteractionPhase::Idle,
        cursor_metrics(CursorBand::Outside, idle_point),
        false,
        false,
        placement,
    ));
    assert!(gate.should_emit(
        30_016,
        PresenceInteractionPhase::Aware,
        cursor_metrics(CursorBand::Aware, PhysicalPoint { x: 20, y: 0 }),
        false,
        false,
        placement,
    ));
    assert!(!gate.should_emit(
        30_032,
        PresenceInteractionPhase::Aware,
        cursor_metrics(CursorBand::Aware, PhysicalPoint { x: 20, y: 0 }),
        false,
        false,
        placement,
    ));
    assert!(gate.should_emit(
        30_048,
        PresenceInteractionPhase::Aware,
        cursor_metrics(CursorBand::Aware, PhysicalPoint { x: 21, y: 0 }),
        false,
        false,
        placement,
    ));
    assert!(gate.should_emit(
        30_064,
        PresenceInteractionPhase::InputReveal,
        cursor_metrics(CursorBand::Active, PhysicalPoint { x: 21, y: 0 }),
        false,
        false,
        placement,
    ));
    assert!(gate.should_emit(
        30_080,
        PresenceInteractionPhase::InputReveal,
        cursor_metrics(CursorBand::Active, PhysicalPoint { x: 21, y: 0 }),
        false,
        false,
        placement,
    ));
}

fn cursor_metrics(band: CursorBand, point: PhysicalPoint) -> CursorMetrics {
    CursorMetrics {
        point,
        direction: NormalizedDirection { x: 0.0, y: 0.0 },
        distance_px: 0.0,
        speed_px_s: 0.0,
        dwell_ms: 0,
        band,
    }
}

#[test]
fn runtime_policy_emits_changes_and_recovery_heartbeats_only() {
    let normal = PresenceRuntimePolicy {
        schema_version: 1,
        frame_rate_limit: 60,
        power_saver: false,
        foreground_fullscreen: false,
    };
    let saver = PresenceRuntimePolicy {
        frame_rate_limit: 15,
        power_saver: true,
        ..normal
    };
    let mut gate = RuntimePolicyEmissionGate::default();

    assert!(gate.should_emit(0, normal));
    assert!(!gate.should_emit(1_000, normal));
    assert!(gate.should_emit(2_000, saver));
    assert!(!gate.should_emit(29_999, saver));
    assert!(gate.should_emit(32_000, saver));
}

#[test]
fn cursor_sampling_failure_closes_hover_after_three_attempts_and_recovers() {
    let mut health = CursorSamplingHealth::default();
    assert!(!health.record_failure());
    assert!(!health.record_failure());
    assert!(health.record_failure());
    health.record_success();
    assert!(!health.record_failure());
}
