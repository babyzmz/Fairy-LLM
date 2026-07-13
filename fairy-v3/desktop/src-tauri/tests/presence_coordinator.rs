use fairy_desktop_v3::presence_coordinator::{
    resolve_presence_placement, select_work_area, CursorBand, CursorTracker, ExpansionDirection,
    PhysicalFrame, PhysicalPoint,
};

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

    assert_eq!(placement.anchor, PhysicalPoint { x: 444, y: 486 });
    assert_eq!(placement.input_compact_frame.width, 558);
    assert_eq!(placement.input_compact_frame.height, 108);
    assert_eq!(placement.input_expanded_frame.width, 630);
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
