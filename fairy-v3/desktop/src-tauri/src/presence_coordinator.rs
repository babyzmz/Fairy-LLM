use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, Ordering};
use std::sync::{Arc, RwLock};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Emitter, Manager};

pub use crate::presence_interaction::{CursorBand, PresenceInteractionPhase};
use crate::presence_interaction::{PresenceInteractionSignal, PresenceInteractionStateMachine};
use crate::presence_runtime::{
    sample_presence_runtime_policy, PresenceRuntimePolicy, PRESENCE_RUNTIME_POLICY_EVENT,
};
use crate::presence_window_policy::{
    input_follows_render, relation_for_phase, PresenceWindowRelation,
};
use crate::{PET_INPUT_LABEL, PET_RENDER_LABEL};

pub const PRESENCE_INTERACTION_EVENT: &str = "presence-interaction-snapshot";
pub const PET_CORE_ANCHOR_X_LOGICAL: f64 = 96.0;
pub const PET_CORE_ANCHOR_Y_LOGICAL: f64 = 88.0;
pub const PET_CORE_EXTENT_LOGICAL: f64 = 144.0;
pub const PET_INPUT_COMPACT_MIN_WIDTH_LOGICAL: f64 = 220.0;
pub const PET_INPUT_COMPACT_WIDTH_LOGICAL: f64 = 240.0;
pub const PET_INPUT_COMPACT_MAX_WIDTH_LOGICAL: f64 = 360.0;
pub const PET_INPUT_COMPACT_HEIGHT_LOGICAL: f64 = 260.0;
pub const PET_INPUT_EXPANDED_WIDTH_LOGICAL: f64 = 616.0;
pub const PET_INPUT_EXPANDED_HEIGHT_LOGICAL: f64 = 360.0;
const PET_INPUT_EDGE_INSET_LOGICAL: f64 = 24.0;
const AWARE_RADIUS: f64 = 220.0;
const ACTIVE_RADIUS: f64 = 120.0;
const ACTIVE_POLL_INTERVAL: Duration = Duration::from_millis(16);
const IDLE_POLL_INTERVAL: Duration = Duration::from_millis(50);
const HIDDEN_POLL_INTERVAL: Duration = Duration::from_millis(250);
const PLACEMENT_REFRESH_INTERVAL: Duration = Duration::from_millis(250);
const RUNTIME_POLICY_REFRESH_INTERVAL: Duration = Duration::from_secs(1);
const PROJECTION_RECOVERY_HEARTBEAT_MS: u64 = 30_000;
const CURSOR_FAILURE_LIMIT: u8 = 3;
const NATIVE_DRAG_HOLD_MS: u64 = 320;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PresenceCoordinatorConfig {
    pub reduced_motion: bool,
    pub hover_enabled: bool,
    pub hover_dwell_ms: u16,
}

impl Default for PresenceCoordinatorConfig {
    fn default() -> Self {
        Self {
            reduced_motion: false,
            hover_enabled: true,
            hover_dwell_ms: 250,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct PhysicalPoint {
    pub x: i32,
    pub y: i32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct PhysicalFrame {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

impl PhysicalFrame {
    pub fn right(self) -> i64 {
        i64::from(self.x) + i64::from(self.width)
    }

    pub fn bottom(self) -> i64 {
        i64::from(self.y) + i64::from(self.height)
    }

    fn contains(self, point: PhysicalPoint) -> bool {
        i64::from(point.x) >= i64::from(self.x)
            && i64::from(point.x) < self.right()
            && i64::from(point.y) >= i64::from(self.y)
            && i64::from(point.y) < self.bottom()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExpansionDirection {
    Left,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct CursorMetrics {
    pub point: PhysicalPoint,
    pub direction: NormalizedDirection,
    pub distance_px: f64,
    pub speed_px_s: f64,
    pub dwell_ms: u64,
    pub band: CursorBand,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct NormalizedDirection {
    pub x: f64,
    pub y: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct PresenceWindowPlacement {
    pub anchor: PhysicalPoint,
    pub render_frame: PhysicalFrame,
    pub input_compact_frame: PhysicalFrame,
    pub input_expanded_frame: PhysicalFrame,
    pub monitor_work_area: PhysicalFrame,
    pub scale_factor: f64,
    pub expansion_direction: ExpansionDirection,
}

impl PresenceWindowPlacement {
    pub fn core_frame(self, extent: u32) -> PhysicalFrame {
        let radius = i64::from(extent) / 2;
        PhysicalFrame {
            x: (i64::from(self.anchor.x) - radius) as i32,
            y: (i64::from(self.anchor.y) - radius) as i32,
            width: extent,
            height: extent,
        }
    }

    pub fn input_frame(self, width: u32, height: u32, compact_height: u32) -> PhysicalFrame {
        input_frame(
            self.render_frame,
            self.expansion_direction,
            width,
            height,
            compact_height,
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct PresenceInteractionSnapshot {
    pub schema_version: u16,
    pub sequence: u64,
    pub sampled_at_ms: u64,
    pub phase: PresenceInteractionPhase,
    pub phase_started_at_ms: u64,
    pub reduced_motion: bool,
    pub cursor: CursorMetrics,
    pub placement: PresenceWindowPlacement,
}

#[derive(Default)]
pub struct CursorTracker {
    previous: Option<(PhysicalPoint, u64)>,
    active_since_ms: Option<u64>,
}

impl CursorTracker {
    pub fn observe(
        &mut self,
        point: PhysicalPoint,
        sampled_at_ms: u64,
        anchor: PhysicalPoint,
        scale_factor: f64,
    ) -> CursorMetrics {
        let distance_px = point_distance(point, anchor);
        let direction = if distance_px <= f64::EPSILON {
            NormalizedDirection { x: 0.0, y: 0.0 }
        } else {
            NormalizedDirection {
                x: (f64::from(point.x) - f64::from(anchor.x)) / distance_px,
                y: (f64::from(point.y) - f64::from(anchor.y)) / distance_px,
            }
        };
        let speed_px_s = self
            .previous
            .and_then(|(previous, previous_ms)| {
                let elapsed_ms = sampled_at_ms.saturating_sub(previous_ms);
                (elapsed_ms > 0)
                    .then(|| point_distance(previous, point) * 1_000.0 / elapsed_ms as f64)
            })
            .unwrap_or(0.0);
        self.previous = Some((point, sampled_at_ms));

        let scale = scale_factor.clamp(0.5, 4.0);
        let band = if distance_px <= ACTIVE_RADIUS * scale {
            CursorBand::Active
        } else if distance_px <= AWARE_RADIUS * scale {
            CursorBand::Aware
        } else {
            CursorBand::Outside
        };
        let dwell_ms = if band == CursorBand::Active {
            let active_since = *self.active_since_ms.get_or_insert(sampled_at_ms);
            sampled_at_ms.saturating_sub(active_since)
        } else {
            self.active_since_ms = None;
            0
        };

        CursorMetrics {
            point,
            direction,
            distance_px,
            speed_px_s,
            dwell_ms,
            band,
        }
    }

    pub fn reset(&mut self) {
        self.previous = None;
        self.active_since_ms = None;
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct InteractionEmissionMarker {
    phase: PresenceInteractionPhase,
    cursor_band: CursorBand,
    cursor_point: PhysicalPoint,
    reduced_motion: bool,
    repositioning: bool,
    anchor: PhysicalPoint,
    render_frame: PhysicalFrame,
    expansion_direction: ExpansionDirection,
}

#[derive(Default)]
pub struct InteractionEmissionGate {
    last: Option<InteractionEmissionMarker>,
    last_emitted_at_ms: Option<u64>,
}

impl InteractionEmissionGate {
    pub fn should_emit(
        &mut self,
        sampled_at_ms: u64,
        phase: PresenceInteractionPhase,
        cursor: CursorMetrics,
        reduced_motion: bool,
        repositioning: bool,
        placement: PresenceWindowPlacement,
    ) -> bool {
        let marker = InteractionEmissionMarker {
            phase,
            cursor_band: cursor.band,
            cursor_point: cursor.point,
            reduced_motion,
            repositioning,
            anchor: placement.anchor,
            render_frame: placement.render_frame,
            expansion_direction: placement.expansion_direction,
        };
        let changed = self.last != Some(marker);
        let heartbeat_due = self.last_emitted_at_ms.is_none_or(|last| {
            sampled_at_ms.saturating_sub(last) >= PROJECTION_RECOVERY_HEARTBEAT_MS
        });
        let transition_progress_due = matches!(
            phase,
            PresenceInteractionPhase::InputReveal | PresenceInteractionPhase::Returning
        );
        let emit = changed || transition_progress_due || heartbeat_due;
        if emit {
            self.last = Some(marker);
            self.last_emitted_at_ms = Some(sampled_at_ms);
        }
        emit
    }
}

#[derive(Default)]
pub struct RuntimePolicyEmissionGate {
    last: Option<PresenceRuntimePolicy>,
    last_emitted_at_ms: Option<u64>,
}

impl RuntimePolicyEmissionGate {
    pub fn should_emit(&mut self, sampled_at_ms: u64, policy: PresenceRuntimePolicy) -> bool {
        let changed = self.last != Some(policy);
        let heartbeat_due = self.last_emitted_at_ms.is_none_or(|last| {
            sampled_at_ms.saturating_sub(last) >= PROJECTION_RECOVERY_HEARTBEAT_MS
        });
        if changed || heartbeat_due {
            self.last = Some(policy);
            self.last_emitted_at_ms = Some(sampled_at_ms);
            return true;
        }
        false
    }
}

#[derive(Default)]
pub struct CursorSamplingHealth {
    consecutive_failures: u8,
}

impl CursorSamplingHealth {
    pub fn record_success(&mut self) {
        self.consecutive_failures = 0;
    }

    pub fn record_failure(&mut self) -> bool {
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        self.consecutive_failures >= CURSOR_FAILURE_LIMIT
    }
}

pub fn resolve_presence_placement(
    render_frame: PhysicalFrame,
    work_area: PhysicalFrame,
    scale_factor: f64,
    previous_direction: Option<ExpansionDirection>,
) -> PresenceWindowPlacement {
    let scale = scale_factor.clamp(0.5, 4.0);
    let anchor_x_offset = (PET_CORE_ANCHOR_X_LOGICAL * scale).round() as i64;
    let anchor_y_offset = (PET_CORE_ANCHOR_Y_LOGICAL * scale).round() as i64;
    let core_radius = ((PET_CORE_EXTENT_LOGICAL * scale).round() as i64) / 2;
    let transparent_edge_overhang = anchor_x_offset.saturating_sub(core_radius);
    let compact_width = (PET_INPUT_COMPACT_WIDTH_LOGICAL * scale).round() as u32;
    let compact_height = (PET_INPUT_COMPACT_HEIGHT_LOGICAL * scale).round() as u32;
    let expanded_width = (PET_INPUT_EXPANDED_WIDTH_LOGICAL * scale).round() as u32;
    let expanded_height = (PET_INPUT_EXPANDED_HEIGHT_LOGICAL * scale).round() as u32;
    let prior = previous_direction.unwrap_or(ExpansionDirection::Right);
    let original_anchor = PhysicalPoint {
        x: match prior {
            ExpansionDirection::Right => i64::from(render_frame.x) + anchor_x_offset,
            ExpansionDirection::Left => render_frame.right() - anchor_x_offset,
        } as i32,
        y: (i64::from(render_frame.y) + anchor_y_offset) as i32,
    };
    let needed = i64::from(render_frame.width).saturating_sub(anchor_x_offset);
    let room_right = work_area.right() - i64::from(original_anchor.x);
    let room_left = i64::from(original_anchor.x) - i64::from(work_area.x);
    let expansion_direction = match (room_right >= needed, room_left >= needed) {
        (true, true) => prior,
        (true, false) => ExpansionDirection::Right,
        (false, true) => ExpansionDirection::Left,
        (false, false) if room_left > room_right => ExpansionDirection::Left,
        (false, false) => ExpansionDirection::Right,
    };
    let desired_x = match expansion_direction {
        ExpansionDirection::Right => i64::from(original_anchor.x) - anchor_x_offset,
        ExpansionDirection::Left => {
            i64::from(original_anchor.x) - i64::from(render_frame.width) + anchor_x_offset
        }
    };
    let desired_y = i64::from(original_anchor.y) - anchor_y_offset;
    let render_x = clamp_axis(
        desired_x,
        i64::from(work_area.x) - transparent_edge_overhang,
        work_area.right() - i64::from(render_frame.width) + transparent_edge_overhang,
    );
    let expanded_top_offset = i64::from(render_frame.height) / 2 + i64::from(compact_height) / 2
        - i64::from(expanded_height);
    let expanded_bottom_offset = i64::from(render_frame.height) / 2 + i64::from(compact_height) / 2;
    let minimum_y = i64::from(work_area.y).max(i64::from(work_area.y) - expanded_top_offset);
    let maximum_y = (work_area.bottom() - i64::from(render_frame.height))
        .min(work_area.bottom() - expanded_bottom_offset);
    let render_y = clamp_axis(desired_y, minimum_y, maximum_y);
    let resolved_render = PhysicalFrame {
        x: render_x as i32,
        y: render_y as i32,
        ..render_frame
    };
    let anchor = PhysicalPoint {
        x: match expansion_direction {
            ExpansionDirection::Right => render_x + anchor_x_offset,
            ExpansionDirection::Left => resolved_render.right() - anchor_x_offset,
        } as i32,
        y: (render_y + anchor_y_offset) as i32,
    };
    PresenceWindowPlacement {
        anchor,
        render_frame: resolved_render,
        input_compact_frame: input_frame(
            resolved_render,
            expansion_direction,
            compact_width,
            compact_height,
            compact_height,
        ),
        input_expanded_frame: input_frame(
            resolved_render,
            expansion_direction,
            expanded_width,
            expanded_height,
            compact_height,
        ),
        monitor_work_area: work_area,
        scale_factor: scale,
        expansion_direction,
    }
}

pub fn resolve_presence_placement_for_anchor(
    anchor: PhysicalPoint,
    render_size: (u32, u32),
    work_area: PhysicalFrame,
    scale_factor: f64,
    previous_direction: Option<ExpansionDirection>,
) -> PresenceWindowPlacement {
    let scale = scale_factor.clamp(0.5, 4.0);
    let direction = previous_direction.unwrap_or(ExpansionDirection::Right);
    let anchor_x_offset = (PET_CORE_ANCHOR_X_LOGICAL * scale).round() as i64;
    let anchor_y_offset = (PET_CORE_ANCHOR_Y_LOGICAL * scale).round() as i64;
    let frame = PhysicalFrame {
        x: match direction {
            ExpansionDirection::Right => i64::from(anchor.x) - anchor_x_offset,
            ExpansionDirection::Left => {
                i64::from(anchor.x) - i64::from(render_size.0) + anchor_x_offset
            }
        } as i32,
        y: (i64::from(anchor.y) - anchor_y_offset) as i32,
        width: render_size.0,
        height: render_size.1,
    };
    resolve_presence_placement(frame, work_area, scale, Some(direction))
}

pub fn resolve_drag_presence_placement_for_anchor(
    anchor: PhysicalPoint,
    render_size: (u32, u32),
    work_area: PhysicalFrame,
    scale_factor: f64,
    expansion_direction: ExpansionDirection,
) -> PresenceWindowPlacement {
    let scale = scale_factor.clamp(0.5, 4.0);
    let anchor_x_offset = (PET_CORE_ANCHOR_X_LOGICAL * scale).round() as i64;
    let anchor_y_offset = (PET_CORE_ANCHOR_Y_LOGICAL * scale).round() as i64;
    let compact_width = (PET_INPUT_COMPACT_WIDTH_LOGICAL * scale).round() as u32;
    let compact_height = (PET_INPUT_COMPACT_HEIGHT_LOGICAL * scale).round() as u32;
    let expanded_width = (PET_INPUT_EXPANDED_WIDTH_LOGICAL * scale).round() as u32;
    let expanded_height = (PET_INPUT_EXPANDED_HEIGHT_LOGICAL * scale).round() as u32;
    let render_frame = PhysicalFrame {
        x: match expansion_direction {
            ExpansionDirection::Right => i64::from(anchor.x) - anchor_x_offset,
            ExpansionDirection::Left => {
                i64::from(anchor.x) - i64::from(render_size.0) + anchor_x_offset
            }
        } as i32,
        y: (i64::from(anchor.y) - anchor_y_offset) as i32,
        width: render_size.0,
        height: render_size.1,
    };
    PresenceWindowPlacement {
        anchor,
        render_frame,
        input_compact_frame: input_frame(
            render_frame,
            expansion_direction,
            compact_width,
            compact_height,
            compact_height,
        ),
        input_expanded_frame: input_frame(
            render_frame,
            expansion_direction,
            expanded_width,
            expanded_height,
            compact_height,
        ),
        monitor_work_area: work_area,
        scale_factor: scale,
        expansion_direction,
    }
}

pub fn anchor_ratios(anchor: PhysicalPoint, work_area: PhysicalFrame) -> (f64, f64) {
    let width = f64::from(work_area.width.max(1));
    let height = f64::from(work_area.height.max(1));
    (
        ((f64::from(anchor.x) - f64::from(work_area.x)) / width).clamp(0.0, 1.0),
        ((f64::from(anchor.y) - f64::from(work_area.y)) / height).clamp(0.0, 1.0),
    )
}

pub fn anchor_from_ratios(work_area: PhysicalFrame, x_ratio: f64, y_ratio: f64) -> PhysicalPoint {
    PhysicalPoint {
        x: (f64::from(work_area.x) + x_ratio.clamp(0.0, 1.0) * f64::from(work_area.width)).round()
            as i32,
        y: (f64::from(work_area.y) + y_ratio.clamp(0.0, 1.0) * f64::from(work_area.height)).round()
            as i32,
    }
}

pub fn configured_cursor_band(
    cursor: CursorMetrics,
    hover_enabled: bool,
    hover_dwell_ms: u64,
) -> CursorBand {
    if !hover_enabled {
        CursorBand::Outside
    } else if cursor.band == CursorBand::Active && cursor.dwell_ms < hover_dwell_ms {
        CursorBand::Aware
    } else {
        cursor.band
    }
}

pub fn select_work_area(
    anchor: PhysicalPoint,
    work_areas: &[PhysicalFrame],
) -> Option<PhysicalFrame> {
    work_areas
        .iter()
        .copied()
        .find(|area| area.contains(anchor))
        .or_else(|| {
            work_areas.iter().copied().min_by_key(|area| {
                let nearest_x =
                    i64::from(anchor.x).clamp(i64::from(area.x), area.right().saturating_sub(1));
                let nearest_y =
                    i64::from(anchor.y).clamp(i64::from(area.y), area.bottom().saturating_sub(1));
                let dx = i64::from(anchor.x) - nearest_x;
                let dy = i64::from(anchor.y) - nearest_y;
                dx.saturating_mul(dx).saturating_add(dy.saturating_mul(dy))
            })
        })
}

pub struct PresenceCoordinatorHandle {
    latest_placement: Arc<RwLock<Option<PresenceWindowPlacement>>>,
    latest_interaction: Arc<RwLock<Option<PresenceInteractionSnapshot>>>,
    shutdown: Arc<AtomicBool>,
    started: AtomicBool,
    reduced_motion: Arc<AtomicBool>,
    hover_enabled: Arc<AtomicBool>,
    hover_dwell_ms: Arc<AtomicU64>,
    repositioning: Arc<AtomicBool>,
    window_relation: Arc<AtomicU8>,
}

struct CoordinatorThreadState {
    latest_placement: Arc<RwLock<Option<PresenceWindowPlacement>>>,
    latest_interaction: Arc<RwLock<Option<PresenceInteractionSnapshot>>>,
    shutdown: Arc<AtomicBool>,
    reduced_motion: Arc<AtomicBool>,
    hover_enabled: Arc<AtomicBool>,
    hover_dwell_ms: Arc<AtomicU64>,
    repositioning: Arc<AtomicBool>,
    window_relation: Arc<AtomicU8>,
}

#[derive(Clone, Copy, Debug)]
struct NativePointerPress {
    point: PhysicalPoint,
    sampled_at_ms: u64,
}

#[derive(Default)]
struct NativePointerController {
    primary_was_down: bool,
    primary_press: Option<NativePointerPress>,
    dragging: bool,
}

impl NativePointerController {
    fn update(
        &mut self,
        app: &tauri::AppHandle,
        placement: PresenceWindowPlacement,
        point: Option<PhysicalPoint>,
        sampled_at_ms: u64,
    ) {
        let primary_down = primary_pointer_down();
        let primary_pressed = primary_down && !self.primary_was_down;
        let primary_released = !primary_down && self.primary_was_down;

        if primary_pressed {
            self.primary_press = point
                .filter(|point| point_inside_native_core(*point, placement))
                .map(|point| NativePointerPress {
                    point,
                    sampled_at_ms,
                });
        }

        if primary_down && !self.dragging {
            if let Some(press) = self.primary_press {
                if native_drag_hold_elapsed(press.sampled_at_ms, sampled_at_ms) {
                    if let Some(state) = app.try_state::<crate::DesktopState>() {
                        match crate::begin_native_pet_drag(
                            app,
                            state.inner(),
                            press.point,
                            placement,
                        ) {
                            Ok(()) => {
                                self.dragging = true;
                            }
                            Err(error) => eprintln!("failed to begin native pet drag: {error}"),
                        }
                    }
                }
            }
        }

        if primary_down && self.dragging {
            if let (Some(point), Some(state)) = (point, app.try_state::<crate::DesktopState>()) {
                if let Err(error) = crate::move_native_pet_drag(app, state.inner(), point) {
                    eprintln!("failed to move native pet drag: {error}");
                }
            }
        }

        if primary_released {
            if self.dragging {
                if let Some(state) = app.try_state::<crate::DesktopState>() {
                    if let Err(error) = crate::end_native_pet_drag(app, state.inner()) {
                        eprintln!("failed to end native pet drag: {error}");
                    }
                }
                self.dragging = false;
            }
            self.primary_press = None;
        }

        self.primary_was_down = primary_down;
    }
}

fn native_drag_hold_elapsed(pressed_at_ms: u64, sampled_at_ms: u64) -> bool {
    sampled_at_ms.saturating_sub(pressed_at_ms) >= NATIVE_DRAG_HOLD_MS
}

fn point_inside_native_core(point: PhysicalPoint, placement: PresenceWindowPlacement) -> bool {
    let radius = PET_CORE_EXTENT_LOGICAL * placement.scale_factor.clamp(0.5, 4.0) / 2.0;
    let dx = f64::from(point.x.saturating_sub(placement.anchor.x));
    let dy = f64::from(point.y.saturating_sub(placement.anchor.y));
    dx.mul_add(dx, dy * dy) <= radius * radius
}

#[cfg(target_os = "windows")]
fn primary_pointer_down() -> bool {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON};
    unsafe { (GetAsyncKeyState(VK_LBUTTON as i32) as u16 & 0x8000) != 0 }
}

#[cfg(not(target_os = "windows"))]
fn primary_pointer_down() -> bool {
    false
}

impl PresenceCoordinatorHandle {
    pub fn new(config: PresenceCoordinatorConfig) -> Self {
        Self {
            latest_placement: Arc::new(RwLock::new(None)),
            latest_interaction: Arc::new(RwLock::new(None)),
            shutdown: Arc::new(AtomicBool::new(false)),
            started: AtomicBool::new(false),
            reduced_motion: Arc::new(AtomicBool::new(config.reduced_motion)),
            hover_enabled: Arc::new(AtomicBool::new(config.hover_enabled)),
            hover_dwell_ms: Arc::new(AtomicU64::new(u64::from(config.hover_dwell_ms))),
            repositioning: Arc::new(AtomicBool::new(false)),
            window_relation: Arc::new(AtomicU8::new(PresenceWindowRelation::Independent as u8)),
        }
    }

    pub fn launch(&self, app: tauri::AppHandle) -> Result<(), std::io::Error> {
        if self
            .started
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Ok(());
        }
        let thread_state = CoordinatorThreadState {
            latest_placement: Arc::clone(&self.latest_placement),
            latest_interaction: Arc::clone(&self.latest_interaction),
            shutdown: Arc::clone(&self.shutdown),
            reduced_motion: Arc::clone(&self.reduced_motion),
            hover_enabled: Arc::clone(&self.hover_enabled),
            hover_dwell_ms: Arc::clone(&self.hover_dwell_ms),
            repositioning: Arc::clone(&self.repositioning),
            window_relation: Arc::clone(&self.window_relation),
        };
        let spawn = thread::Builder::new()
            .name("fairy-presence-coordinator".to_owned())
            .spawn(move || run_coordinator(app, thread_state));
        if let Err(error) = spawn {
            self.started.store(false, Ordering::Release);
            return Err(error);
        }
        Ok(())
    }

    pub fn latest_placement(&self) -> Option<PresenceWindowPlacement> {
        self.latest_placement.read().ok().and_then(|value| *value)
    }

    pub fn latest_interaction(&self) -> Option<PresenceInteractionSnapshot> {
        self.latest_interaction.read().ok().and_then(|value| *value)
    }

    pub fn set_reduced_motion(&self, reduced_motion: bool) {
        self.reduced_motion.store(reduced_motion, Ordering::Release);
    }

    pub fn set_preferences(&self, config: PresenceCoordinatorConfig) {
        self.reduced_motion
            .store(config.reduced_motion, Ordering::Release);
        self.hover_enabled
            .store(config.hover_enabled, Ordering::Release);
        self.hover_dwell_ms
            .store(u64::from(config.hover_dwell_ms), Ordering::Release);
    }

    pub fn set_repositioning(&self, repositioning: bool) {
        self.repositioning.store(repositioning, Ordering::Release);
    }

    pub fn is_repositioning(&self) -> bool {
        self.repositioning.load(Ordering::Acquire)
    }

    pub fn window_relation(&self) -> PresenceWindowRelation {
        PresenceWindowRelation::from_atomic(self.window_relation.load(Ordering::Acquire))
    }

    pub fn set_latest_placement(&self, placement: PresenceWindowPlacement) {
        if let Ok(mut value) = self.latest_placement.write() {
            *value = Some(placement);
        }
    }
}

impl Drop for PresenceCoordinatorHandle {
    fn drop(&mut self) {
        self.shutdown.store(true, Ordering::Release);
    }
}

fn run_coordinator(app: tauri::AppHandle, state: CoordinatorThreadState) {
    let CoordinatorThreadState {
        latest_placement,
        latest_interaction,
        shutdown,
        reduced_motion,
        hover_enabled,
        hover_dwell_ms,
        repositioning,
        window_relation,
    } = state;
    let started = Instant::now();
    let mut tracker = CursorTracker::default();
    let mut interaction = PresenceInteractionStateMachine::new(0);
    let mut sequence = 0_u64;
    let mut placement = latest_placement.read().ok().and_then(|value| *value);
    let mut placement_refreshed_at = Instant::now() - PLACEMENT_REFRESH_INTERVAL;
    let mut runtime_policy_refreshed_at = Instant::now() - RUNTIME_POLICY_REFRESH_INTERVAL;
    let mut emission_gate = InteractionEmissionGate::default();
    let mut runtime_policy_emission_gate = RuntimePolicyEmissionGate::default();
    let mut cursor_sampling = CursorSamplingHealth::default();
    let mut native_pointer = NativePointerController::default();

    while !shutdown.load(Ordering::Acquire) {
        let Some(render) = app.get_webview_window(PET_RENDER_LABEL) else {
            thread::sleep(HIDDEN_POLL_INTERVAL);
            continue;
        };
        let native_surface_visible = app.try_state::<crate::DesktopState>().is_some_and(|state| {
            matches!(
                state.native_gpu.status().lifecycle,
                crate::presence_native_gpu::NativeGpuLifecycle::Starting
                    | crate::presence_native_gpu::NativeGpuLifecycle::Running
            )
        });
        if !render.is_visible().unwrap_or(false) && !native_surface_visible {
            interaction.suspend(started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64);
            window_relation.store(PresenceWindowRelation::Independent as u8, Ordering::Release);
            thread::sleep(HIDDEN_POLL_INTERVAL);
            continue;
        }
        if runtime_policy_refreshed_at.elapsed() >= RUNTIME_POLICY_REFRESH_INTERVAL {
            let policy = sample_presence_runtime_policy();
            let sampled_at_ms = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
            if runtime_policy_emission_gate.should_emit(sampled_at_ms, policy) {
                let _ = app.emit_to(PET_RENDER_LABEL, PRESENCE_RUNTIME_POLICY_EVENT, policy);
            }
            runtime_policy_refreshed_at = Instant::now();
        }
        let mut is_repositioning = repositioning.load(Ordering::Acquire);
        if is_repositioning {
            if let Ok(value) = latest_placement.read() {
                if value.is_some() {
                    placement = *value;
                }
            }
            // Native drag owns both window frames until pointer release. Refreshing through
            // Tauri here can observe an older frame and visibly pull one surface backwards.
            placement_refreshed_at = Instant::now();
        } else if placement.is_none()
            || placement_refreshed_at.elapsed() >= PLACEMENT_REFRESH_INTERVAL
        {
            let relation =
                PresenceWindowRelation::from_atomic(window_relation.load(Ordering::Acquire));
            if let Some(next) = refresh_placement(&app, &render, placement, relation) {
                placement = Some(next);
                if let Ok(mut value) = latest_placement.write() {
                    *value = Some(next);
                }
            }
            placement_refreshed_at = Instant::now();
        }
        let Some(mut current_placement) = placement else {
            thread::sleep(HIDDEN_POLL_INTERVAL);
            continue;
        };
        let sampled_at_ms = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
        let point = global_cursor_position();
        native_pointer.update(&app, current_placement, point, sampled_at_ms);
        is_repositioning = repositioning.load(Ordering::Acquire);
        if is_repositioning {
            if let Ok(value) = latest_placement.read() {
                if let Some(next) = *value {
                    current_placement = next;
                    placement = Some(next);
                }
            }
        }
        let cursor = match point {
            Some(point) => {
                cursor_sampling.record_success();
                tracker.observe(
                    point,
                    sampled_at_ms,
                    current_placement.anchor,
                    current_placement.scale_factor,
                )
            }
            None if cursor_sampling.record_failure() => {
                tracker.reset();
                unavailable_cursor(current_placement)
            }
            None => {
                thread::sleep(IDLE_POLL_INTERVAL);
                continue;
            }
        };
        let reduced_motion = reduced_motion.load(Ordering::Acquire);
        let hover_enabled = hover_enabled.load(Ordering::Acquire);
        let hover_dwell_ms = hover_dwell_ms.load(Ordering::Acquire);
        let repositioning = is_repositioning;
        let pointer_over_input = point.is_some_and(|point| input_pointer_state(&app, point));
        let effective_cursor_band = configured_cursor_band(cursor, hover_enabled, hover_dwell_ms);
        let projected_cursor = CursorMetrics {
            band: effective_cursor_band,
            ..cursor
        };
        let phase = interaction.advance(PresenceInteractionSignal {
            sampled_at_ms,
            cursor_band: effective_cursor_band,
            active_dwell_ms: cursor.dwell_ms,
            cursor_speed_px_s: cursor.speed_px_s,
            pointer_over_input,
            reduced_motion,
            suspended: false,
            repositioning,
        });
        window_relation.store(relation_for_phase(phase.phase) as u8, Ordering::Release);
        if emission_gate.should_emit(
            sampled_at_ms,
            phase.phase,
            projected_cursor,
            reduced_motion,
            repositioning,
            current_placement,
        ) {
            sequence = sequence.saturating_add(1);
            let snapshot = PresenceInteractionSnapshot {
                schema_version: 1,
                sequence,
                sampled_at_ms,
                phase: phase.phase,
                phase_started_at_ms: phase.phase_started_at_ms,
                reduced_motion,
                cursor: projected_cursor,
                placement: current_placement,
            };
            if let Ok(mut current) = latest_interaction.write() {
                *current = Some(snapshot);
            }
            let _ = app.emit_to(PET_RENDER_LABEL, PRESENCE_INTERACTION_EVENT, snapshot);
            let _ = app.emit_to(PET_INPUT_LABEL, PRESENCE_INTERACTION_EVENT, snapshot);
        }
        thread::sleep(match effective_cursor_band {
            CursorBand::Aware | CursorBand::Active => ACTIVE_POLL_INTERVAL,
            CursorBand::Outside => IDLE_POLL_INTERVAL,
        });
    }
}

fn unavailable_cursor(placement: PresenceWindowPlacement) -> CursorMetrics {
    CursorMetrics {
        point: placement.anchor,
        direction: NormalizedDirection { x: 0.0, y: 0.0 },
        distance_px: (AWARE_RADIUS + 1.0) * placement.scale_factor,
        speed_px_s: 0.0,
        dwell_ms: 0,
        band: CursorBand::Outside,
    }
}

fn input_pointer_state(app: &tauri::AppHandle, point: PhysicalPoint) -> bool {
    let Some(input) = app.get_webview_window(PET_INPUT_LABEL) else {
        return false;
    };
    if !input.is_visible().unwrap_or(false) {
        return false;
    }
    input
        .outer_position()
        .ok()
        .zip(input.outer_size().ok())
        .is_some_and(|(position, size)| {
            point_in_input_region(&input, point, position.x, position.y).unwrap_or_else(|| {
                PhysicalFrame {
                    x: position.x,
                    y: position.y,
                    width: size.width,
                    height: size.height,
                }
                .contains(point)
            })
        })
}

#[cfg(target_os = "windows")]
fn point_in_input_region(
    input: &tauri::WebviewWindow,
    point: PhysicalPoint,
    window_x: i32,
    window_y: i32,
) -> Option<bool> {
    use windows_sys::Win32::Graphics::Gdi::{
        CreateRectRgn, DeleteObject, GetWindowRgn, PtInRegion,
    };

    let hwnd = input.hwnd().ok()?.0 as windows_sys::Win32::Foundation::HWND;
    let region = unsafe { CreateRectRgn(0, 0, 0, 0) };
    if region.is_null() {
        return None;
    }
    let region_kind = unsafe { GetWindowRgn(hwnd, region) };
    if region_kind == 0 {
        unsafe { DeleteObject(region) };
        return None;
    }
    let local_x = point.x.saturating_sub(window_x);
    let local_y = point.y.saturating_sub(window_y);
    let contains = unsafe { PtInRegion(region, local_x, local_y) } != 0;
    unsafe { DeleteObject(region) };
    Some(contains)
}

#[cfg(not(target_os = "windows"))]
fn point_in_input_region(
    _input: &tauri::WebviewWindow,
    _point: PhysicalPoint,
    _window_x: i32,
    _window_y: i32,
) -> Option<bool> {
    None
}

fn refresh_placement(
    app: &tauri::AppHandle,
    render: &tauri::WebviewWindow,
    previous: Option<PresenceWindowPlacement>,
    relation: PresenceWindowRelation,
) -> Option<PresenceWindowPlacement> {
    let state = app.try_state::<crate::DesktopState>()?;
    let native_windows = crate::presence_native_windows(&state).ok()?;
    let render_handle = native_windows.handle_for(PET_RENDER_LABEL).ok()?;
    let tracking_frame = crate::presence_window_frame(render, render_handle).ok()?;
    let current_frame = crate::presence_visible_render_frame(render, render_handle, &state).ok()?;
    let monitors = crate::presence_monitors_for_app(app).ok()?;
    let placement = if let Some(presentation) = state.native_gpu.presentation() {
        crate::placement_from_native_presentation(current_frame, &monitors, presentation).ok()?
    } else {
        let provisional_scale = previous.map_or(1.0, |placement| placement.scale_factor);
        let provisional = provisional_anchor(
            current_frame,
            provisional_scale,
            previous.map(|value| value.expansion_direction),
        );
        let monitor = crate::monitor_for_anchor(&monitors, provisional)?;
        resolve_presence_placement(
            current_frame,
            monitor.work_area,
            monitor.scale_factor,
            previous.map(|value| value.expansion_direction),
        )
    };
    let scale = placement.scale_factor;
    let render_changed = placement.render_frame != tracking_frame;
    let mut input_frame_to_apply = None;
    if let Some(input) = app.get_webview_window(PET_INPUT_LABEL) {
        if input.is_visible().unwrap_or(false) {
            if let Ok(input_handle) = native_windows.handle_for(PET_INPUT_LABEL) {
                let current_input_frame =
                    crate::presence_window_frame(&input, input_handle).ok()?;
                let geometry = crate::pet_input_geometry_from_frame(current_input_frame, scale);
                let input_is_core_proxy = matches!(geometry.layout, crate::PetInputLayout::Core);
                if render_changed || input_follows_render(relation, input_is_core_proxy) {
                    let frame = crate::pet_input_frame_for_geometry(placement, geometry);
                    if frame != current_input_frame || render_changed {
                        input_frame_to_apply = Some(frame);
                    }
                }
            }
        }
    }
    if render_changed || input_frame_to_apply.is_some() {
        let _ = crate::synchronize_presence_window_frames(
            app,
            placement.render_frame,
            input_frame_to_apply,
        );
    }
    Some(placement)
}

fn provisional_anchor(
    render: PhysicalFrame,
    scale_factor: f64,
    direction: Option<ExpansionDirection>,
) -> PhysicalPoint {
    let offset_x = (PET_CORE_ANCHOR_X_LOGICAL * scale_factor.clamp(0.5, 4.0)).round() as i64;
    let offset_y = (PET_CORE_ANCHOR_Y_LOGICAL * scale_factor.clamp(0.5, 4.0)).round() as i64;
    PhysicalPoint {
        x: match direction.unwrap_or(ExpansionDirection::Right) {
            ExpansionDirection::Right => i64::from(render.x) + offset_x,
            ExpansionDirection::Left => render.right() - offset_x,
        } as i32,
        y: (i64::from(render.y) + offset_y) as i32,
    }
}

fn input_frame(
    render: PhysicalFrame,
    direction: ExpansionDirection,
    width: u32,
    height: u32,
    compact_height: u32,
) -> PhysicalFrame {
    let scale = f64::from(render.width) / 640.0;
    let edge_inset = (PET_INPUT_EDGE_INSET_LOGICAL * scale.clamp(0.5, 4.0)).round() as i64;
    let y = if height <= compact_height {
        i64::from(render.y)
    } else {
        render.bottom() - i64::from(height)
    };
    PhysicalFrame {
        x: match direction {
            ExpansionDirection::Right => i64::from(render.x) + edge_inset,
            ExpansionDirection::Left => render.right() - edge_inset - i64::from(width),
        } as i32,
        y: y as i32,
        width,
        height,
    }
}

fn clamp_axis(value: i64, minimum: i64, maximum: i64) -> i64 {
    if maximum < minimum {
        minimum
    } else {
        value.clamp(minimum, maximum)
    }
}

fn point_distance(left: PhysicalPoint, right: PhysicalPoint) -> f64 {
    let dx = f64::from(left.x) - f64::from(right.x);
    let dy = f64::from(left.y) - f64::from(right.y);
    dx.hypot(dy)
}

#[cfg(target_os = "windows")]
pub(crate) fn global_cursor_position() -> Option<PhysicalPoint> {
    use windows_sys::Win32::Foundation::POINT;
    use windows_sys::Win32::UI::WindowsAndMessaging::GetCursorPos;

    let _dpi_scope = crate::PerMonitorDpiScope::enter();
    let mut point = POINT { x: 0, y: 0 };
    // GetCursorPos samples global state and does not install a hook or intercept input.
    (unsafe { GetCursorPos(&mut point) } != 0).then_some(PhysicalPoint {
        x: point.x,
        y: point.y,
    })
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn global_cursor_position() -> Option<PhysicalPoint> {
    None
}

#[cfg(test)]
mod native_pointer_tests {
    use super::*;

    fn placement(scale_factor: f64) -> PresenceWindowPlacement {
        let empty = PhysicalFrame {
            x: 0,
            y: 0,
            width: 640,
            height: 260,
        };
        PresenceWindowPlacement {
            anchor: PhysicalPoint { x: 100, y: 200 },
            render_frame: empty,
            input_compact_frame: empty,
            input_expanded_frame: empty,
            monitor_work_area: empty,
            scale_factor,
            expansion_direction: ExpansionDirection::Right,
        }
    }

    #[test]
    fn native_pointer_hit_test_is_circular_and_dpi_aware() {
        let placement = placement(1.25);
        assert!(point_inside_native_core(
            PhysicalPoint { x: 100, y: 110 },
            placement,
        ));
        assert!(!point_inside_native_core(
            PhysicalPoint { x: 10, y: 110 },
            placement,
        ));
    }

    #[test]
    fn native_drag_requires_the_full_long_press_interval() {
        assert!(!native_drag_hold_elapsed(1_000, 1_319));
        assert!(native_drag_hold_elapsed(1_000, 1_320));
        assert!(native_drag_hold_elapsed(1_000, 1_600));
    }

    #[test]
    fn native_drag_preserves_the_pointer_anchor_at_work_area_edges() {
        let work_area = PhysicalFrame {
            x: 0,
            y: 0,
            width: 1_920,
            height: 1_040,
        };
        let anchor = PhysicalPoint { x: 1_918, y: 1_038 };
        let placement = resolve_drag_presence_placement_for_anchor(
            anchor,
            (640, 260),
            work_area,
            1.0,
            ExpansionDirection::Right,
        );

        assert_eq!(placement.anchor, anchor);
        assert_eq!(placement.render_frame.x, anchor.x - 96);
        assert_eq!(placement.render_frame.y, anchor.y - 88);
        assert_eq!(placement.monitor_work_area, work_area);
    }
}
