use crate::presence_coordinator::PhysicalFrame;
use crate::presence_interaction::PresenceInteractionPhase;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum PresenceWindowRelation {
    Independent = 0,
    CoupledTransition = 1,
}

impl PresenceWindowRelation {
    pub fn from_atomic(value: u8) -> Self {
        if value == Self::CoupledTransition as u8 {
            Self::CoupledTransition
        } else {
            Self::Independent
        }
    }

    pub fn is_coupled(self) -> bool {
        self == Self::CoupledTransition
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PresenceDragTarget {
    Input,
    CoupledWindows,
}

pub fn relation_for_phase(phase: PresenceInteractionPhase) -> PresenceWindowRelation {
    match phase {
        PresenceInteractionPhase::Droplet
        | PresenceInteractionPhase::Stretching
        | PresenceInteractionPhase::InputReveal => PresenceWindowRelation::CoupledTransition,
        PresenceInteractionPhase::Idle
        | PresenceInteractionPhase::Aware
        | PresenceInteractionPhase::Interactive
        | PresenceInteractionPhase::Returning
        | PresenceInteractionPhase::Suspended
        | PresenceInteractionPhase::Repositioning => PresenceWindowRelation::Independent,
    }
}

pub fn drag_target_for_relation(_relation: PresenceWindowRelation) -> PresenceDragTarget {
    PresenceDragTarget::CoupledWindows
}

pub fn input_follows_render(relation: PresenceWindowRelation, input_is_core_proxy: bool) -> bool {
    input_is_core_proxy || relation.is_coupled()
}

pub fn moved_frame(
    start: PhysicalFrame,
    delta_x: i32,
    delta_y: i32,
    work_area: PhysicalFrame,
) -> PhysicalFrame {
    clamped_frame(
        PhysicalFrame {
            x: start.x.saturating_add(delta_x),
            y: start.y.saturating_add(delta_y),
            ..start
        },
        work_area,
    )
}

pub fn resized_frame_preserving_bottom(
    current: PhysicalFrame,
    width: u32,
    height: u32,
    work_area: PhysicalFrame,
) -> PhysicalFrame {
    clamped_frame(
        PhysicalFrame {
            x: current.x,
            y: current
                .bottom()
                .saturating_sub(i64::from(height))
                .clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32,
            width,
            height,
        },
        work_area,
    )
}

pub fn clamped_frame(frame: PhysicalFrame, work_area: PhysicalFrame) -> PhysicalFrame {
    let maximum_x = work_area.right().saturating_sub(i64::from(frame.width));
    let maximum_y = work_area.bottom().saturating_sub(i64::from(frame.height));
    PhysicalFrame {
        x: clamp_axis(i64::from(frame.x), i64::from(work_area.x), maximum_x) as i32,
        y: clamp_axis(i64::from(frame.y), i64::from(work_area.y), maximum_y) as i32,
        ..frame
    }
}

fn clamp_axis(value: i64, minimum: i64, maximum: i64) -> i64 {
    if maximum < minimum {
        minimum
    } else {
        value.clamp(minimum, maximum)
    }
}
