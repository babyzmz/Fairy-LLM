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
const CORE_ANCHOR_X: f64 = 96.0;
const CORE_ANCHOR_Y: f64 = 130.0;
pub const PET_CORE_EXTENT_LOGICAL: f64 = 144.0;
pub const PET_INPUT_COMPACT_WIDTH_LOGICAL: f64 = 616.0;
pub const PET_INPUT_COMPACT_HEIGHT_LOGICAL: f64 = 144.0;
pub const PET_INPUT_EXPANDED_WIDTH_LOGICAL: f64 = 616.0;
pub const PET_INPUT_EXPANDED_HEIGHT_LOGICAL: f64 = 360.0;
const AWARE_RADIUS: f64 = 220.0;
const ACTIVE_RADIUS: f64 = 120.0;
const ACTIVE_POLL_INTERVAL: Duration = Duration::from_millis(16);
const IDLE_POLL_INTERVAL: Duration = Duration::from_millis(50);
const HIDDEN_POLL_INTERVAL: Duration = Duration::from_millis(250);
const PLACEMENT_REFRESH_INTERVAL: Duration = Duration::from_millis(250);
const RUNTIME_POLICY_REFRESH_INTERVAL: Duration = Duration::from_secs(1);
const PROJECTION_RECOVERY_HEARTBEAT_MS: u64 = 30_000;
const CURSOR_FAILURE_LIMIT: u8 = 3;

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
    let anchor_x_offset = (CORE_ANCHOR_X * scale).round() as i64;
    let anchor_y_offset = (CORE_ANCHOR_Y * scale).round() as i64;
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
        i64::from(work_area.x),
        work_area.right() - i64::from(render_frame.width),
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
    let anchor_x_offset = (CORE_ANCHOR_X * scale).round() as i64;
    let anchor_y_offset = (CORE_ANCHOR_Y * scale).round() as i64;
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
    shutdown: Arc<AtomicBool>,
    reduced_motion: Arc<AtomicBool>,
    hover_enabled: Arc<AtomicBool>,
    hover_dwell_ms: Arc<AtomicU64>,
    repositioning: Arc<AtomicBool>,
    window_relation: Arc<AtomicU8>,
}

impl PresenceCoordinatorHandle {
    pub fn new(config: PresenceCoordinatorConfig) -> Self {
        Self {
            latest_placement: Arc::new(RwLock::new(None)),
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
    let mut placement: Option<PresenceWindowPlacement> = None;
    let mut placement_refreshed_at = Instant::now() - PLACEMENT_REFRESH_INTERVAL;
    let mut runtime_policy_refreshed_at = Instant::now() - RUNTIME_POLICY_REFRESH_INTERVAL;
    let mut emission_gate = InteractionEmissionGate::default();
    let mut runtime_policy_emission_gate = RuntimePolicyEmissionGate::default();
    let mut cursor_sampling = CursorSamplingHealth::default();

    while !shutdown.load(Ordering::Acquire) {
        let Some(render) = app.get_webview_window(PET_RENDER_LABEL) else {
            thread::sleep(HIDDEN_POLL_INTERVAL);
            continue;
        };
        if !render.is_visible().unwrap_or(false) {
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
        let is_repositioning = repositioning.load(Ordering::Acquire);
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
        let Some(current_placement) = placement else {
            thread::sleep(HIDDEN_POLL_INTERVAL);
            continue;
        };
        let sampled_at_ms = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
        let point = global_cursor_position();
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
        let (pointer_over_input, input_focused) = point.map_or_else(
            || (false, input_focus_state(&app)),
            |point| input_pointer_state(&app, point),
        );
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
            input_focused,
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

fn input_focus_state(app: &tauri::AppHandle) -> bool {
    app.get_webview_window(PET_INPUT_LABEL)
        .is_some_and(|input| {
            input.is_visible().unwrap_or(false) && input.is_focused().unwrap_or(false)
        })
}

fn input_pointer_state(app: &tauri::AppHandle, point: PhysicalPoint) -> (bool, bool) {
    let Some(input) = app.get_webview_window(PET_INPUT_LABEL) else {
        return (false, false);
    };
    if !input.is_visible().unwrap_or(false) {
        return (false, false);
    }
    let focused = input.is_focused().unwrap_or(false);
    let pointer_over = input
        .outer_position()
        .ok()
        .zip(input.outer_size().ok())
        .is_some_and(|(position, size)| {
            PhysicalFrame {
                x: position.x,
                y: position.y,
                width: size.width,
                height: size.height,
            }
            .contains(point)
        });
    (pointer_over, focused)
}

fn refresh_placement(
    app: &tauri::AppHandle,
    render: &tauri::WebviewWindow,
    previous: Option<PresenceWindowPlacement>,
    relation: PresenceWindowRelation,
) -> Option<PresenceWindowPlacement> {
    let position = render.outer_position().ok()?;
    let size = render.outer_size().ok()?;
    let scale = render.scale_factor().ok()?;
    let current_frame = PhysicalFrame {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    };
    let provisional = provisional_anchor(
        current_frame,
        scale,
        previous.map(|value| value.expansion_direction),
    );
    let work_areas = render
        .available_monitors()
        .ok()?
        .into_iter()
        .map(|monitor| PhysicalFrame {
            x: monitor.work_area().position.x,
            y: monitor.work_area().position.y,
            width: monitor.work_area().size.width,
            height: monitor.work_area().size.height,
        })
        .collect::<Vec<_>>();
    let work_area = select_work_area(provisional, &work_areas)?;
    let placement = resolve_presence_placement(
        current_frame,
        work_area,
        scale,
        previous.map(|value| value.expansion_direction),
    );
    if placement.render_frame.x != current_frame.x || placement.render_frame.y != current_frame.y {
        let _ = render.set_position(tauri::PhysicalPosition::new(
            placement.render_frame.x,
            placement.render_frame.y,
        ));
    }
    if let Some(input) = app.get_webview_window(PET_INPUT_LABEL) {
        if input.is_visible().unwrap_or(false) {
            if let Ok(input_size) = input.outer_size() {
                let core_extent = (PET_CORE_EXTENT_LOGICAL * scale).round() as u32;
                let compact_height = (PET_INPUT_COMPACT_HEIGHT_LOGICAL * scale).round() as u32;
                let input_is_core_proxy =
                    input_size.width == core_extent && input_size.height == core_extent;
                if !input_follows_render(relation, input_is_core_proxy) {
                    return Some(placement);
                }
                let frame = if input_is_core_proxy {
                    placement.core_frame(core_extent)
                } else {
                    placement.input_frame(input_size.width, input_size.height, compact_height)
                };
                let _ = input.set_position(tauri::PhysicalPosition::new(frame.x, frame.y));
            }
        }
    }
    Some(placement)
}

fn provisional_anchor(
    render: PhysicalFrame,
    scale_factor: f64,
    direction: Option<ExpansionDirection>,
) -> PhysicalPoint {
    let offset_x = (CORE_ANCHOR_X * scale_factor.clamp(0.5, 4.0)).round() as i64;
    let offset_y = (CORE_ANCHOR_Y * scale_factor.clamp(0.5, 4.0)).round() as i64;
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
    let render_center_y = i64::from(render.y) + i64::from(render.height) / 2;
    let compact_bottom = render_center_y + i64::from(compact_height) / 2;
    let y = if height <= compact_height {
        render_center_y - i64::from(height) / 2
    } else {
        compact_bottom - i64::from(height)
    };
    PhysicalFrame {
        x: match direction {
            ExpansionDirection::Right => render.right() - i64::from(width),
            ExpansionDirection::Left => i64::from(render.x),
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
