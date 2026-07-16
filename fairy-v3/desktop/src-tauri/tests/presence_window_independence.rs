use fairy_desktop_v3::presence_coordinator::PhysicalFrame;
use fairy_desktop_v3::presence_interaction::PresenceInteractionPhase;
use fairy_desktop_v3::presence_window_policy::{
    drag_target_for_relation, input_follows_render, moved_frame, relation_for_phase,
    resized_frame_preserving_bottom, PresenceDragTarget, PresenceWindowRelation,
};

#[test]
fn couples_windows_only_during_the_liquid_reveal_transition() {
    for phase in [
        PresenceInteractionPhase::Droplet,
        PresenceInteractionPhase::Stretching,
        PresenceInteractionPhase::InputReveal,
    ] {
        assert_eq!(
            relation_for_phase(phase),
            PresenceWindowRelation::CoupledTransition
        );
    }
    for phase in [
        PresenceInteractionPhase::Idle,
        PresenceInteractionPhase::Aware,
        PresenceInteractionPhase::Interactive,
        PresenceInteractionPhase::Returning,
        PresenceInteractionPhase::Suspended,
        PresenceInteractionPhase::Repositioning,
    ] {
        assert_eq!(
            relation_for_phase(phase),
            PresenceWindowRelation::Independent
        );
    }
}

#[test]
fn dragging_always_moves_the_core_and_input_as_one_group() {
    assert_eq!(
        drag_target_for_relation(PresenceWindowRelation::Independent),
        PresenceDragTarget::CoupledWindows
    );
    assert_eq!(
        drag_target_for_relation(PresenceWindowRelation::CoupledTransition),
        PresenceDragTarget::CoupledWindows
    );
    assert!(!input_follows_render(
        PresenceWindowRelation::Independent,
        false
    ));
    assert!(input_follows_render(
        PresenceWindowRelation::CoupledTransition,
        false
    ));
    assert!(input_follows_render(
        PresenceWindowRelation::Independent,
        true
    ));
}

#[test]
fn independent_input_movement_and_resize_remain_inside_the_work_area() {
    let work_area = PhysicalFrame {
        x: -1920,
        y: 0,
        width: 1920,
        height: 1040,
    };
    let start = PhysicalFrame {
        x: -700,
        y: 800,
        width: 616,
        height: 144,
    };
    assert_eq!(
        moved_frame(start, 900, 400, work_area),
        PhysicalFrame {
            x: -616,
            y: 896,
            width: 616,
            height: 144,
        }
    );
    assert_eq!(
        resized_frame_preserving_bottom(start, 616, 360, work_area),
        PhysicalFrame {
            x: -700,
            y: 584,
            width: 616,
            height: 360,
        }
    );
}
