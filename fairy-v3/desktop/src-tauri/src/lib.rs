use std::env;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use fairy_core_bridge::{CoreBridge, CoreBridgeError, CoreLaunchSpec};
use serde_json::{json, Value};
use tauri::ipc::{Channel, Response};
use tauri::menu::{CheckMenuItem, MenuBuilder, MenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, FilePath};

use desktop_preferences::{
    DesktopPreferences, DesktopPreferencesError, DesktopPreferencesStore, DesktopPreferencesUpdate,
    LegacyMemorySettings, PetPreferencesUpdate,
};
use obsidian_path_registry::{ObsidianPathRegistry, ObsidianVaultSelection};
use presence_coordinator::{
    anchor_from_ratios, anchor_ratios, global_cursor_position,
    resolve_drag_presence_placement_for_anchor, resolve_presence_placement,
    resolve_presence_placement_for_anchor, select_work_area, ExpansionDirection, PhysicalFrame,
    PhysicalPoint, PresenceCoordinatorConfig, PresenceCoordinatorHandle, PresenceWindowPlacement,
    PET_CORE_ANCHOR_X_LOGICAL, PET_CORE_ANCHOR_Y_LOGICAL, PET_CORE_EXTENT_LOGICAL,
    PET_INPUT_COMPACT_HEIGHT_LOGICAL, PET_INPUT_COMPACT_MAX_WIDTH_LOGICAL,
    PET_INPUT_COMPACT_MIN_WIDTH_LOGICAL, PET_INPUT_COMPACT_WIDTH_LOGICAL,
    PET_INPUT_EXPANDED_HEIGHT_LOGICAL, PET_INPUT_EXPANDED_WIDTH_LOGICAL,
};
use presence_native_gpu::{
    NativeGpuConfig, NativeGpuExpansionDirection, NativeGpuLifecycle, NativeGpuPresentation,
    NativeGpuStartRequest, NativeGpuStatus, NativePresenceGpuManager,
};
use presence_renderer_supervisor::{
    PresenceRendererDirective, PresenceRendererHealthReport, PresenceRendererStatus,
    PresenceRendererSupervisor,
};
use presence_startup::PresenceStartupGate;
use provider_configuration::{openrouter_profiles_json, ProviderConfigurationStore};
use provider_credentials::{CredentialReplacement, ProviderCredentialStore};
use realtime_worker::{
    bundled_realtime_launch, development_realtime_launch, RealtimeWorkerManager,
    RealtimeWorkerStartInput, RealtimeWorkerStatus, RealtimeWorkerStopInput,
    RealtimeWorkerToolResultInput,
};
use voice_worker::{
    bundled_voice_launch, development_voice_launch, prepared_realtime_session,
    prepared_test_session, PreparedVoiceSession, VoiceStreamEvent, VoiceStreamInput,
    VoiceWorkerManager,
};

pub mod capture;
pub mod desktop_preferences;
pub mod obsidian_path_registry;
pub mod presence_backdrop;
pub mod presence_coordinator;
pub mod presence_interaction;
pub mod presence_native_gpu;
pub mod presence_renderer_supervisor;
pub mod presence_runtime;
pub mod presence_startup;
pub mod presence_window_policy;
mod process_lifetime;
pub mod provider_configuration;
pub mod provider_credentials;
pub mod realtime_worker;
pub mod voice_worker;

pub const PET_RENDER_LABEL: &str = "pet-render";
pub const PET_INPUT_LABEL: &str = "pet-input";
const PET_INPUT_EXPANDED_CONTENT_HEIGHT_LOGICAL: f64 = 72.0;
const PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT: &str = "presence-native-renderer-lifecycle";
pub(crate) const PRESENCE_INPUT_REQUESTED_EVENT: &str = "presence-input-requested";
const TRAY_ASK_ID: &str = "fairy.tray.ask";
const TRAY_NEW_CHAT_ID: &str = "fairy.tray.new_chat";
const TRAY_AUTO_PLAY_ID: &str = "fairy.tray.auto_play";
const TRAY_MUTED_ID: &str = "fairy.tray.muted";
const TRAY_ALWAYS_ON_TOP_ID: &str = "fairy.tray.always_on_top";
const TRAY_OPEN_ID: &str = "fairy.tray.open";
const TRAY_SETTINGS_ID: &str = "fairy.tray.settings";
const TRAY_RESET_ID: &str = "fairy.tray.reset";
const TRAY_EXIT_ID: &str = "fairy.tray.exit";

#[derive(Clone, Debug, serde::Serialize)]
struct PetRenderSettings {
    schema_version: u16,
    mode: desktop_preferences::PetRendererMode,
    optics_mode: desktop_preferences::PetOpticsMode,
    activation_style: desktop_preferences::PetActivationStyle,
    size_scale: f32,
    opacity: f32,
    motion_enabled: bool,
    particles_enabled: bool,
    target_frame_rate: u16,
}

impl From<&DesktopPreferences> for PetRenderSettings {
    fn from(preferences: &DesktopPreferences) -> Self {
        Self {
            schema_version: 4,
            mode: preferences.pet_renderer_mode.clone(),
            optics_mode: preferences.pet_optics_mode,
            activation_style: preferences.pet_activation_style,
            size_scale: f32::from(preferences.pet_size_percent) / 100.0,
            opacity: f32::from(preferences.pet_opacity_percent) / 100.0,
            motion_enabled: preferences.pet_motion_enabled,
            particles_enabled: preferences.pet_particles_enabled,
            target_frame_rate: preferences.pet_target_fps,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FairyTrayAction {
    Ask,
    NewChat,
    ToggleAutoPlay,
    ToggleMuted,
    ToggleAlwaysOnTop,
    Open,
    Settings,
    Reset,
    Exit,
}

pub fn fairy_tray_action(menu_id: &str) -> Option<FairyTrayAction> {
    match menu_id {
        TRAY_ASK_ID => Some(FairyTrayAction::Ask),
        TRAY_NEW_CHAT_ID => Some(FairyTrayAction::NewChat),
        TRAY_AUTO_PLAY_ID => Some(FairyTrayAction::ToggleAutoPlay),
        TRAY_MUTED_ID => Some(FairyTrayAction::ToggleMuted),
        TRAY_ALWAYS_ON_TOP_ID => Some(FairyTrayAction::ToggleAlwaysOnTop),
        TRAY_OPEN_ID => Some(FairyTrayAction::Open),
        TRAY_SETTINGS_ID => Some(FairyTrayAction::Settings),
        TRAY_RESET_ID => Some(FairyTrayAction::Reset),
        TRAY_EXIT_ID => Some(FairyTrayAction::Exit),
        _ => None,
    }
}

#[derive(Debug)]
pub struct WindowScopeError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AuxiliaryWindowPolicy {
    pub ignore_cursor_events: bool,
    pub focusable: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PresenceWindowCreationSpec {
    pub label: &'static str,
    pub width: u32,
    pub height: u32,
    pub min_width: u32,
    pub min_height: u32,
    pub max_width: u32,
    pub max_height: u32,
    pub focusable: bool,
}

pub const fn presence_window_creation_specs() -> [PresenceWindowCreationSpec; 2] {
    [
        PresenceWindowCreationSpec {
            label: PET_RENDER_LABEL,
            width: 640,
            height: 260,
            min_width: 640,
            min_height: 260,
            max_width: 640,
            max_height: 260,
            focusable: false,
        },
        PresenceWindowCreationSpec {
            label: PET_INPUT_LABEL,
            // Keep the transparent WebView surface allocation stable. Resizing WebView2 while it
            // becomes visible exposes an opaque resize buffer for one compositor frame.
            width: 616,
            height: 360,
            min_width: 616,
            min_height: 360,
            max_width: 616,
            max_height: 360,
            focusable: true,
        },
    ]
}

pub fn authorize_core_rpc_window(label: &str) -> Result<(), WindowScopeError> {
    if label == "main" {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn authorize_settings_window(label: &str) -> Result<(), WindowScopeError> {
    if label == "settings" {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn authorize_preferences_reader(label: &str) -> Result<(), WindowScopeError> {
    if ["main", "settings", PET_INPUT_LABEL].contains(&label) {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn authorize_pet_input_window(label: &str) -> Result<(), WindowScopeError> {
    if label == PET_INPUT_LABEL {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn authorize_pet_render_window(label: &str) -> Result<(), WindowScopeError> {
    if label == PET_RENDER_LABEL {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn authorize_voice_health_window(label: &str) -> Result<(), WindowScopeError> {
    if ["main", "settings"].contains(&label) {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn authorize_voice_settings_window(label: &str) -> Result<(), WindowScopeError> {
    authorize_settings_window(label)
}

pub fn settings_method_allowed(method: &str) -> bool {
    matches!(
        method,
        "health"
            | "capabilities.get"
            | "permissions.get"
            | "permissions.update"
            | "providers.list"
            | "providers.health"
            | "models.catalog.list"
            | "models.catalog.refresh"
            | "models.selection.get"
            | "models.selection.update"
            | "memory.proposals.accept"
            | "memory.proposals.list"
            | "memory.proposals.reject"
            | "memory.settings.get"
            | "memory.settings.update"
            | "knowledge.sources.list"
            | "obsidian.health.get"
            | "projects.archived.delete"
            | "projects.archived.list"
            | "projects.archived.restore"
            | "projects.list"
            | "skills.list"
            | "extensions.catalog.list"
            | "skills.install"
            | "skills.import.inspect"
            | "skills.import.install"
            | "skills.create"
            | "skills.update"
            | "skills.set_enabled"
            | "skills.remove"
            | "tasks.list"
            | "mcp.servers.list"
            | "mcp.presets.install"
            | "mcp.servers.configure"
            | "mcp.servers.discover"
            | "mcp.servers.accept"
            | "mcp.servers.set_enabled"
            | "mcp.servers.delete"
            | "trash.items.list"
            | "trash.items.purge"
            | "trash.items.purge_all"
            | "trash.items.restore"
    )
}

pub fn auxiliary_window_policy(label: &str) -> Option<AuxiliaryWindowPolicy> {
    match label {
        PET_RENDER_LABEL => Some(AuxiliaryWindowPolicy {
            ignore_cursor_events: true,
            focusable: false,
        }),
        PET_INPUT_LABEL => Some(AuxiliaryWindowPolicy {
            ignore_cursor_events: true,
            focusable: false,
        }),
        _ => None,
    }
}

pub fn anchored_pet_input_frame(
    render: PetWindowFrame,
    target_width: u32,
    target_height: u32,
    compact_height: u32,
) -> PetWindowFrame {
    let scale = f64::from(render.width) / 640.0;
    let edge_inset = (24.0 * scale.clamp(0.5, 4.0)).round() as i64;
    let y = if target_height <= compact_height {
        i64::from(render.y)
    } else {
        i64::from(render.y) + i64::from(render.height) - i64::from(target_height)
    };
    PetWindowFrame {
        x: (i64::from(render.x) + edge_inset) as i32,
        y: y as i32,
        width: target_width,
        height: target_height,
    }
}

pub fn anchored_pet_core_frame(
    render: PetWindowFrame,
    target_extent: u32,
    scale_factor: f64,
    direction: ExpansionDirection,
) -> PetWindowFrame {
    let scale = scale_factor.clamp(0.5, 4.0);
    let anchor_x_offset = (PET_CORE_ANCHOR_X_LOGICAL * scale).round() as i64;
    let anchor_y_offset = (PET_CORE_ANCHOR_Y_LOGICAL * scale).round() as i64;
    let radius = i64::from(target_extent) / 2;
    let anchor_x = match direction {
        ExpansionDirection::Right => i64::from(render.x) + anchor_x_offset,
        ExpansionDirection::Left => i64::from(render.x) + i64::from(render.width) - anchor_x_offset,
    };
    PetWindowFrame {
        x: (anchor_x - radius) as i32,
        y: (i64::from(render.y) + anchor_y_offset - radius) as i32,
        width: target_extent,
        height: target_extent,
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PetWindowFrame {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

pub fn anchored_pet_frame(
    current: PetWindowFrame,
    target_width: u32,
    target_height: u32,
    monitor: PetWindowFrame,
) -> PetWindowFrame {
    let right = i64::from(current.x) + i64::from(current.width);
    let bottom = i64::from(current.y) + i64::from(current.height);
    let minimum_x = i64::from(monitor.x);
    let minimum_y = i64::from(monitor.y);
    let maximum_x = minimum_x + i64::from(monitor.width.saturating_sub(target_width));
    let maximum_y = minimum_y + i64::from(monitor.height.saturating_sub(target_height));
    PetWindowFrame {
        x: (right - i64::from(target_width)).clamp(minimum_x, maximum_x) as i32,
        y: (bottom - i64::from(target_height)).clamp(minimum_y, maximum_y) as i32,
        width: target_width,
        height: target_height,
    }
}

pub fn bridge_failure_response(id: Value, error: &CoreBridgeError) -> Value {
    let error_code = if matches!(error, CoreBridgeError::WorkerInterrupted) {
        "WORKER_INTERRUPTED"
    } else {
        "CORE_PROTOCOL_ERROR"
    };
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": -32050,
            "message": error.to_string(),
            "data": { "error_code": error_code }
        }
    })
}

struct DesktopState {
    core: Arc<Mutex<Option<CoreBridge>>>,
    voice: Arc<VoiceWorkerManager>,
    realtime: Arc<RealtimeWorkerManager>,
    preferences: Mutex<()>,
    provider_update_in_progress: AtomicBool,
    data_dir: PathBuf,
    desktop_program: PathBuf,
    resource_dir: PathBuf,
    presence: PresenceCoordinatorHandle,
    native_gpu: Arc<NativePresenceGpuManager>,
    native_gpu_reset_in_flight: AtomicBool,
    presence_windows: Mutex<Option<PresenceNativeWindows>>,
    pet_drag: Mutex<Option<PetGroupDragSession>>,
    pet_input_geometry: Mutex<PetInputGeometry>,
    pet_input_presentation: Mutex<PetInputPresentationFence>,
    renderer_supervisor: Mutex<PresenceRendererSupervisor>,
    renderer_health: Mutex<Option<PresenceRendererHealthReport>>,
    presence_startup: PresenceStartupGate,
    pet_placement_reconciled: AtomicBool,
    started_at: Instant,
}

#[tauri::command]
async fn realtime_worker_status(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<RealtimeWorkerStatus, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    Ok(state.realtime.status())
}

#[tauri::command]
async fn realtime_worker_start(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
    input: RealtimeWorkerStartInput,
) -> Result<RealtimeWorkerStatus, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let credential_provider = input.provider.credential_provider();
    let credential = ProviderCredentialStore::new(&state.data_dir)
        .load(credential_provider)
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "REALTIME_CREDENTIAL_MISSING".to_owned())?;
    state
        .realtime
        .start(&app, input, zeroize::Zeroizing::new(credential))
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn realtime_worker_stop(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: RealtimeWorkerStopInput,
) -> Result<RealtimeWorkerStatus, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    state
        .realtime
        .stop(&input.session_id)
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn realtime_worker_tool_result(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: RealtimeWorkerToolResultInput,
) -> Result<(), String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    state
        .realtime
        .tool_result(input)
        .map_err(|error| error.to_string())
}

struct ProviderUpdateGuard<'a> {
    flag: &'a AtomicBool,
}

impl Drop for ProviderUpdateGuard<'_> {
    fn drop(&mut self) {
        self.flag.store(false, Ordering::Release);
    }
}

fn begin_provider_update(state: &DesktopState) -> Result<ProviderUpdateGuard<'_>, String> {
    state
        .provider_update_in_progress
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .map_err(|_| "Another provider update is already in progress".to_owned())?;
    Ok(ProviderUpdateGuard {
        flag: &state.provider_update_in_progress,
    })
}

#[cfg(target_os = "windows")]
type PresenceNativeHandle = isize;

#[cfg(not(target_os = "windows"))]
type PresenceNativeHandle = ();

#[cfg(target_os = "windows")]
struct PerMonitorDpiScope {
    previous: windows_sys::Win32::UI::HiDpi::DPI_AWARENESS_CONTEXT,
}

#[cfg(target_os = "windows")]
impl PerMonitorDpiScope {
    fn enter() -> Self {
        use windows_sys::Win32::UI::HiDpi::{
            SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        };

        Self {
            previous: unsafe {
                SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            },
        }
    }
}

#[cfg(target_os = "windows")]
impl Drop for PerMonitorDpiScope {
    fn drop(&mut self) {
        use windows_sys::Win32::UI::HiDpi::SetThreadDpiAwarenessContext;

        if !self.previous.is_null() {
            unsafe {
                SetThreadDpiAwarenessContext(self.previous);
            }
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct PresenceNativeWindows {
    render: PresenceNativeHandle,
    input: PresenceNativeHandle,
}

impl PresenceNativeWindows {
    fn handle_for(self, label: &str) -> Result<PresenceNativeHandle, String> {
        match label {
            PET_RENDER_LABEL => Ok(self.render),
            PET_INPUT_LABEL => Ok(self.input),
            _ => Err(format!("Unsupported presence window: {label}")),
        }
    }
}

fn presence_native_windows(state: &DesktopState) -> Result<PresenceNativeWindows, String> {
    state
        .presence_windows
        .lock()
        .map_err(|_| "Pet native window lock is unavailable".to_owned())?
        .ok_or_else(|| "Pet native windows are unavailable".to_owned())
}

#[cfg(target_os = "windows")]
fn native_gpu_render_handle(windows: PresenceNativeWindows) -> Result<isize, String> {
    Ok(windows.render)
}

#[cfg(target_os = "windows")]
fn native_gpu_input_handle(windows: PresenceNativeWindows) -> Result<isize, String> {
    Ok(windows.input)
}

#[cfg(not(target_os = "windows"))]
fn native_gpu_input_handle(_windows: PresenceNativeWindows) -> Result<isize, String> {
    Err("PRESENCE_NATIVE_GPU_UNAVAILABLE".to_owned())
}

#[cfg(not(target_os = "windows"))]
fn native_gpu_render_handle(_windows: PresenceNativeWindows) -> Result<isize, String> {
    Err("PRESENCE_NATIVE_GPU_UNAVAILABLE".to_owned())
}

fn set_presence_placement(state: &DesktopState, placement: PresenceWindowPlacement) {
    state.presence.set_latest_placement(placement);
}

struct FairyTrayState {
    _tray: TrayIcon,
    auto_play: CheckMenuItem<tauri::Wry>,
    muted: CheckMenuItem<tauri::Wry>,
    always_on_top: CheckMenuItem<tauri::Wry>,
}

#[derive(Clone, Debug)]
struct PresenceMonitor {
    id: String,
    work_area: PhysicalFrame,
    scale_factor: f64,
    is_primary: bool,
}

#[derive(Debug)]
struct PetGroupDragSession {
    // Kept only for the fenced legacy command contract; native pointer sessions always use the
    // fixed owner token below and no WebView can create or replace them.
    session_id: String,
    start_pointer: PhysicalPoint,
    start_anchor: PhysicalPoint,
    current_placement: PresenceWindowPlacement,
    input_geometry: PetInputGeometry,
    monitors: Vec<PresenceMonitor>,
    native_windows: PresenceNativeWindows,
    native_renderer_drag_active: bool,
    native_renderer_suspended: bool,
}

fn scope_failure_response(id: Value, message: &str) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": -32001,
            "message": message,
            "data": { "error_code": "SCOPE_MISMATCH" }
        }
    })
}

async fn call_core(state: &DesktopState, request: Value) -> Value {
    let request_id = request.get("id").cloned().unwrap_or(Value::Null);
    let core = Arc::clone(&state.core);
    match tauri::async_runtime::spawn_blocking(move || {
        let guard = core.lock().map_err(|_| CoreBridgeError::LockPoisoned)?;
        guard
            .as_ref()
            .ok_or(CoreBridgeError::WorkerInterrupted)?
            .call(request)
    })
    .await
    {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => bridge_failure_response(request_id, &error),
        Err(error) => json!({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32050,
                "message": error.to_string(),
                "data": { "error_code": "WORKER_INTERRUPTED" }
            }
        }),
    }
}

#[tauri::command]
async fn core_rpc(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    request: Value,
) -> Result<Value, String> {
    let request_id = request.get("id").cloned().unwrap_or(Value::Null);
    if authorize_core_rpc_window(window.label()).is_err() {
        return Ok(scope_failure_response(
            request_id,
            "Window is not authorized to access Fairy Core",
        ));
    }
    Ok(call_core(&state, request).await)
}

#[tauri::command]
async fn settings_rpc(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    request: Value,
) -> Result<Value, String> {
    let request_id = request.get("id").cloned().unwrap_or(Value::Null);
    if authorize_settings_window(window.label()).is_err() {
        return Ok(scope_failure_response(
            request_id,
            "Window is not authorized to access settings methods",
        ));
    }
    let method = request
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !settings_method_allowed(method) {
        return Ok(scope_failure_response(
            request_id,
            "Method is outside the settings allow list",
        ));
    }
    Ok(call_core(&state, request).await)
}

#[derive(serde::Deserialize)]
struct OpenRouterConfigureInput {
    api_key: String,
}

#[derive(serde::Serialize)]
struct OpenRouterStatus {
    configured: bool,
    account_id: Option<String>,
}

#[derive(serde::Deserialize)]
struct RealtimeProviderCredentialInput {
    provider: String,
    api_key: String,
}

#[derive(serde::Deserialize)]
struct RealtimeProviderCredentialTarget {
    provider: String,
}

#[derive(serde::Serialize)]
struct RealtimeProviderCredentialStatus {
    provider: String,
    configured: bool,
}

fn realtime_credential_provider(value: &str) -> Result<&str, String> {
    match value {
        "gemini" | "zhipu" => Ok(value),
        _ => Err("Unsupported realtime provider".to_owned()),
    }
}

#[tauri::command]
async fn provider_realtime_status(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: RealtimeProviderCredentialTarget,
) -> Result<RealtimeProviderCredentialStatus, String> {
    if authorize_settings_window(window.label()).is_err()
        && authorize_core_rpc_window(window.label()).is_err()
    {
        return Err("Window is not authorized".to_owned());
    }
    let provider = realtime_credential_provider(&input.provider)?;
    let configured = ProviderCredentialStore::new(&state.data_dir)
        .configured_for(provider)
        .map_err(|error| error.to_string())?;
    Ok(RealtimeProviderCredentialStatus {
        provider: provider.to_owned(),
        configured,
    })
}

#[tauri::command]
async fn provider_realtime_configure(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: RealtimeProviderCredentialInput,
) -> Result<RealtimeProviderCredentialStatus, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let provider = realtime_credential_provider(&input.provider)?;
    let _update_guard = begin_provider_update(&state)?;
    ProviderCredentialStore::new(&state.data_dir)
        .save(provider, &input.api_key)
        .map_err(|error| error.to_string())?;
    Ok(RealtimeProviderCredentialStatus {
        provider: provider.to_owned(),
        configured: true,
    })
}

#[tauri::command]
async fn provider_realtime_delete(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: RealtimeProviderCredentialTarget,
) -> Result<RealtimeProviderCredentialStatus, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let provider = realtime_credential_provider(&input.provider)?;
    let _update_guard = begin_provider_update(&state)?;
    ProviderCredentialStore::new(&state.data_dir)
        .delete(provider)
        .map_err(|error| error.to_string())?;
    Ok(RealtimeProviderCredentialStatus {
        provider: provider.to_owned(),
        configured: false,
    })
}

#[tauri::command]
async fn provider_openrouter_status(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<OpenRouterStatus, String> {
    if authorize_settings_window(window.label()).is_err()
        && authorize_core_rpc_window(window.label()).is_err()
    {
        return Err("Window is not authorized".to_owned());
    }
    let configuration = ProviderConfigurationStore::new(&state.data_dir)
        .load_openrouter()
        .map_err(|error| error.to_string())?;
    Ok(OpenRouterStatus {
        configured: ProviderCredentialStore::new(&state.data_dir).configured()
            && configuration.is_some(),
        account_id: configuration.map(|value| value.account_id),
    })
}

#[tauri::command]
async fn provider_openrouter_configure(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: OpenRouterConfigureInput,
) -> Result<OpenRouterStatus, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let _update_guard = begin_provider_update(&state)?;
    let credentials = ProviderCredentialStore::new(&state.data_dir);
    let configurations = ProviderConfigurationStore::new(&state.data_dir);
    let previous_configuration = configurations
        .load_openrouter()
        .map_err(|error| error.to_string())?;
    let replacement = credentials
        .begin_openrouter_replacement(&input.api_key)
        .map_err(|error| error.to_string())?;
    let configuration = match configurations.save_openrouter() {
        Ok(configuration) => configuration,
        Err(error) => {
            replacement.rollback().map_err(|_| {
                "Provider configuration failed and the prior credential could not be restored"
                    .to_owned()
            })?;
            return Err(error.to_string());
        }
    };
    if restart_core(&state).is_err() {
        restore_openrouter_candidate(
            &state,
            replacement,
            &configurations,
            previous_configuration.is_some(),
        )
        .await?;
        return Err("OpenRouter credential validation could not start".to_owned());
    }
    let response = call_core(&state, model_catalog_refresh_request()).await;
    if !catalog_refresh_is_configured(&response) {
        restore_openrouter_candidate(
            &state,
            replacement,
            &configurations,
            previous_configuration.is_some(),
        )
        .await?;
        return Err("OpenRouter credential validation failed".to_owned());
    }
    replacement.commit();
    Ok(OpenRouterStatus {
        configured: true,
        account_id: Some(configuration.account_id),
    })
}

fn model_catalog_refresh_request() -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": "provider-openrouter-validation",
        "method": "models.catalog.refresh",
        "params": {}
    })
}

fn catalog_refresh_is_configured(response: &Value) -> bool {
    response
        .pointer("/result/account/credential_status")
        .and_then(Value::as_str)
        == Some("configured")
        && response
            .pointer("/result/last_error_code")
            .is_some_and(Value::is_null)
        && response.pointer("/result/stale").and_then(Value::as_bool) == Some(false)
}

async fn restore_openrouter_candidate(
    state: &DesktopState,
    replacement: CredentialReplacement,
    configurations: &ProviderConfigurationStore,
    had_previous_configuration: bool,
) -> Result<(), String> {
    replacement.rollback().map_err(|_| {
        "OpenRouter validation failed and the prior credential could not be restored".to_owned()
    })?;
    let configuration_restored = if had_previous_configuration {
        configurations.save_openrouter().map(|_| ())
    } else {
        configurations.delete_openrouter()
    };
    configuration_restored.map_err(|_| {
        "OpenRouter validation failed and the prior provider configuration could not be restored"
            .to_owned()
    })?;
    restart_core(state).map_err(|_| {
        "OpenRouter validation failed and the prior provider could not be restarted".to_owned()
    })?;
    let _ = call_core(state, model_catalog_refresh_request()).await;
    Ok(())
}

#[cfg(test)]
mod provider_validation_tests {
    use super::catalog_refresh_is_configured;
    use serde_json::json;

    #[test]
    fn accepts_only_a_fresh_configured_catalog() {
        assert!(catalog_refresh_is_configured(&json!({
            "result": {
                "account": { "credential_status": "configured" },
                "last_error_code": null,
                "stale": false
            }
        })));
        assert!(!catalog_refresh_is_configured(&json!({
            "result": {
                "account": { "credential_status": "configured" },
                "last_error_code": "MODEL_CATALOG_TIMEOUT",
                "stale": true
            }
        })));
        assert!(!catalog_refresh_is_configured(&json!({
            "result": {
                "account": { "credential_status": "invalid" },
                "last_error_code": "CREDENTIAL_INVALID",
                "stale": true
            }
        })));
    }
}

#[tauri::command]
async fn provider_openrouter_delete(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<OpenRouterStatus, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let _update_guard = begin_provider_update(&state)?;
    ProviderCredentialStore::new(&state.data_dir)
        .delete_openrouter()
        .map_err(|error| error.to_string())?;
    ProviderConfigurationStore::new(&state.data_dir)
        .delete_openrouter()
        .map_err(|error| error.to_string())?;
    restart_core(&state).map_err(|error| error.to_string())?;
    Ok(OpenRouterStatus {
        configured: false,
        account_id: None,
    })
}

#[tauri::command]
async fn desktop_preferences_get(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<DesktopPreferences, String> {
    authorize_preferences_reader(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    DesktopPreferencesStore::new(&state.data_dir)
        .load()
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn desktop_preferences_update(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
    input: DesktopPreferencesUpdate,
) -> Result<DesktopPreferences, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let _guard = state
        .preferences
        .lock()
        .map_err(|_| "Desktop preferences lock is unavailable".to_owned())?;
    let next = DesktopPreferencesStore::new(&state.data_dir)
        .update(input)
        .map_err(|error| match error {
            DesktopPreferencesError::RevisionConflict => "PREFERENCES_REVISION_CONFLICT".to_owned(),
            other => other.to_string(),
        })?;
    apply_persisted_preferences(&app, &state, &next);
    Ok(next)
}

#[tauri::command]
async fn pet_preferences_update(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
    input: PetPreferencesUpdate,
) -> Result<DesktopPreferences, String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let anchor_changed = input.pet_anchor.is_some() || input.clear_pet_anchor;
    let next = persist_pet_preferences(&app, &state, input)?;
    if anchor_changed && next.pet_remember_position {
        let placement = presence_native_windows(&state)
            .and_then(|windows| place_pet_windows(&app, &next, Some(windows)));
        match placement {
            Ok(placement) => set_presence_placement(&state, placement),
            Err(error) => eprintln!("failed to apply persisted pet position: {error}"),
        }
    }
    Ok(next)
}

fn persist_pet_preferences(
    app: &tauri::AppHandle,
    state: &DesktopState,
    input: PetPreferencesUpdate,
) -> Result<DesktopPreferences, String> {
    let _guard = state
        .preferences
        .lock()
        .map_err(|_| "Desktop preferences lock is unavailable".to_owned())?;
    let next = DesktopPreferencesStore::new(&state.data_dir)
        .update_pet(input)
        .map_err(|error| match error {
            DesktopPreferencesError::RevisionConflict => "PREFERENCES_REVISION_CONFLICT".to_owned(),
            other => other.to_string(),
        })?;
    apply_persisted_preferences(app, state, &next);
    Ok(next)
}

fn apply_persisted_preferences(
    app: &tauri::AppHandle,
    state: &DesktopState,
    next: &DesktopPreferences,
) {
    state.presence.set_preferences(coordinator_config(next));
    if let Err(error) = apply_pet_window_preferences(app, next) {
        eprintln!("failed to apply persisted pet window preferences: {error}");
    }
    if let Err(error) = app.emit("desktop-preferences-changed", next) {
        eprintln!("failed to publish persisted desktop preferences: {error}");
    }
    sync_tray_preferences(app, next);
}

#[cfg(target_os = "windows")]
fn primary_pointer_pressed() -> bool {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON};

    unsafe { (GetAsyncKeyState(VK_LBUTTON as i32) as u16 & 0x8000) != 0 }
}

#[cfg(not(target_os = "windows"))]
fn primary_pointer_pressed() -> bool {
    true
}

fn validate_pet_drag_session_id(session_id: &str) -> Result<(), String> {
    if session_id.is_empty()
        || session_id.len() > 128
        || !session_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err("PET_DRAG_SESSION_INVALID".to_owned());
    }
    Ok(())
}

pub(crate) fn begin_native_pet_drag(
    app: &tauri::AppHandle,
    state: &DesktopState,
    start_pointer: PhysicalPoint,
    fallback_placement: PresenceWindowPlacement,
) -> Result<(), String> {
    {
        let drag = state
            .pet_drag
            .lock()
            .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
        if drag.is_some() {
            return Ok(());
        }
    }
    state.presence.set_repositioning(true);
    if let Err(error) = state.native_gpu.set_drag_active(true) {
        state.presence.set_repositioning(false);
        return Err(error.to_string());
    }
    let result = (|| {
        let native_windows = presence_native_windows(state)?;
        let monitors = presence_monitors_for_app(app)?;
        let placement = app
            .get_webview_window(PET_RENDER_LABEL)
            .and_then(|render| {
                let render_handle = native_windows.handle_for(PET_RENDER_LABEL).ok()?;
                let render_frame =
                    presence_visible_render_frame(&render, render_handle, state).ok()?;
                let presentation = state.native_gpu.presentation()?;
                placement_from_native_presentation(render_frame, &monitors, presentation).ok()
            })
            .unwrap_or(fallback_placement);
        set_presence_placement(state, placement);
        let input_geometry = current_pet_input_geometry(state)?;
        let session = PetGroupDragSession {
            session_id: "native-pointer".to_owned(),
            start_pointer,
            start_anchor: placement.anchor,
            current_placement: placement,
            input_geometry,
            monitors,
            native_windows,
            native_renderer_drag_active: true,
            native_renderer_suspended: false,
        };
        let mut drag = state
            .pet_drag
            .lock()
            .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
        *drag = Some(session);
        Ok(())
    })();
    if result.is_err() {
        let _ = state.native_gpu.set_drag_active(false);
        state.presence.set_repositioning(false);
    }
    result
}

pub(crate) fn move_native_pet_drag(
    app: &tauri::AppHandle,
    state: &DesktopState,
    current_pointer: PhysicalPoint,
) -> Result<(), String> {
    let mut drag = state
        .pet_drag
        .lock()
        .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
    let session = drag
        .as_mut()
        .ok_or_else(|| "Pet drag has not started".to_owned())?;
    let desired_anchor = PhysicalPoint {
        x: session
            .start_anchor
            .x
            .saturating_add(current_pointer.x.saturating_sub(session.start_pointer.x)),
        y: session
            .start_anchor
            .y
            .saturating_add(current_pointer.y.saturating_sub(session.start_pointer.y)),
    };
    let monitor = monitor_for_anchor(&session.monitors, current_pointer)
        .ok_or_else(|| "No monitor is available".to_owned())?;
    let placement = resolve_drag_presence_placement_for_anchor(
        desired_anchor,
        render_size_for_scale(monitor.scale_factor),
        monitor.work_area,
        monitor.scale_factor,
        session.current_placement.expansion_direction,
    );
    if placement == session.current_placement {
        return Ok(());
    }
    let crossed_surface = placement.monitor_work_area
        != session.current_placement.monitor_work_area
        || (placement.scale_factor - session.current_placement.scale_factor).abs() > f64::EPSILON;
    move_pet_window_group_during_drag(
        app,
        session.native_windows,
        &placement,
        session.input_geometry,
        crossed_surface,
    )?;
    session.current_placement = placement;
    set_presence_placement(state, placement);
    Ok(())
}

pub(crate) fn end_native_pet_drag(
    app: &tauri::AppHandle,
    state: &DesktopState,
) -> Result<(), String> {
    let session = {
        let mut drag = state
            .pet_drag
            .lock()
            .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
        drag.take()
    };
    let native_renderer_drag_active = session
        .as_ref()
        .is_some_and(|session| session.native_renderer_drag_active);
    let result = (|| {
        let current = DesktopPreferencesStore::new(&state.data_dir)
            .load()
            .map_err(|error| error.to_string())?;
        let placement = match session.as_ref() {
            Some(session) => settle_pet_drag_placement(app, state, session)?,
            None => return Ok(()),
        };
        if current.pet_remember_position {
            let monitor = presence_monitors_for_app(app)?
                .into_iter()
                .find(|candidate| candidate.work_area == placement.monitor_work_area)
                .ok_or_else(|| "Pet monitor is unavailable".to_owned())?;
            let (x_ratio, y_ratio) = anchor_ratios(placement.anchor, monitor.work_area);
            persist_pet_preferences(
                app,
                state,
                PetPreferencesUpdate {
                    expected_revision: current.revision,
                    voice_auto_play_pet: None,
                    pet_muted: None,
                    pet_always_on_top: None,
                    pet_anchor: Some(desktop_preferences::PetAnchorPreference {
                        monitor_id: monitor.id,
                        x_ratio,
                        y_ratio,
                    }),
                    clear_pet_anchor: false,
                },
            )?;
        }
        Ok(())
    })();
    state.presence.set_repositioning(false);
    let resume_result = if native_renderer_drag_active {
        state
            .native_gpu
            .set_drag_active(false)
            .map_err(|error| error.to_string())
    } else {
        Ok(())
    };
    let _ = app.emit_to(
        PET_RENDER_LABEL,
        PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT,
        NativeRendererLifecycleSignal {
            schema_version: 1,
            reason: "drag_ended",
        },
    );
    result.and(resume_result)
}

#[tauri::command]
async fn pet_window_group_begin_drag(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    session_id: String,
    initial_delta_x: i32,
    initial_delta_y: i32,
) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    // Physical drag is exclusively owned by NativePointerController. Keep the legacy command
    // fenced while old dev WebViews can still be alive during hot reload; they must never replace
    // the native-pointer session in DesktopState.
    if window.label() == PET_INPUT_LABEL {
        return Err("PET_DRAG_OWNED_BY_NATIVE_POINTER".to_owned());
    }
    validate_pet_drag_session_id(&session_id)?;
    // Native liquid rendering can become visible before the transparent input proxy receives
    // its first presentation update. Reconcile the proxy synchronously so the first long-press
    // cannot start from a stale blank rectangle or snap the visible core back to that position.
    let _ = align_pet_input_to_native_presentation(window.app_handle(), state.inner())?;
    state.presence.set_repositioning(true);
    if let Err(error) = state.native_gpu.set_drag_active(true) {
        state.presence.set_repositioning(false);
        return Err(error.to_string());
    }
    let result = (|| {
        let app = window.app_handle();
        let native_windows = presence_native_windows(&state)?;
        let render = app
            .get_webview_window(PET_RENDER_LABEL)
            .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
        let input = app
            .get_webview_window(PET_INPUT_LABEL)
            .ok_or_else(|| "Pet input window is unavailable".to_owned())?;
        let render_handle = native_windows.handle_for(PET_RENDER_LABEL)?;
        let render_frame = presence_visible_render_frame(&render, render_handle, state.inner())?;
        let input_frame =
            presence_window_frame(&input, native_windows.handle_for(PET_INPUT_LABEL)?)?;
        let monitors = presence_monitors_for_app(app)?;
        let (placement, _) = placement_from_actual_pet_windows(
            render_frame,
            input_frame,
            &monitors,
            state.presence.latest_placement(),
            state.native_gpu.presentation(),
        )?;
        let input_geometry = current_pet_input_geometry(&state)?;
        let current_pointer = global_cursor_position()
            .ok_or_else(|| "Pet cursor position is unavailable".to_owned())?;
        let start_pointer = calibrated_pet_drag_start_pointer(
            current_pointer,
            (initial_delta_x, initial_delta_y),
            placement.scale_factor,
        );
        let session = PetGroupDragSession {
            session_id,
            // The WebView can move past the drag threshold before the async command reaches
            // Rust. Reconstruct pointer-down in physical coordinates so native deltas never
            // jump back when they replace the first CSS fallback sample.
            start_pointer,
            start_anchor: placement.anchor,
            current_placement: placement,
            input_geometry,
            monitors,
            native_windows,
            native_renderer_drag_active: true,
            native_renderer_suspended: false,
        };
        let mut drag = state
            .pet_drag
            .lock()
            .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
        *drag = Some(session);
        Ok(())
    })();
    if result.is_err() {
        let _ = state.native_gpu.set_drag_active(false);
        state.presence.set_repositioning(false);
    }
    result
}

#[tauri::command]
async fn pet_window_group_move(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    session_id: String,
    delta_x: i32,
    delta_y: i32,
) -> Result<bool, String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    validate_pet_drag_session_id(&session_id)?;
    if !primary_pointer_pressed() {
        return Ok(false);
    }
    let app = window.app_handle();
    let mut drag = state
        .pet_drag
        .lock()
        .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
    let session = drag
        .as_mut()
        .ok_or_else(|| "Pet drag has not started".to_owned())?;
    if session.session_id != session_id {
        return Err("PET_DRAG_SESSION_MISMATCH".to_owned());
    }
    let current_pointer = global_cursor_position();
    let (pointer_delta_x, pointer_delta_y) = resolve_pet_drag_delta(
        session.start_pointer,
        current_pointer,
        (delta_x, delta_y),
        session.current_placement.scale_factor,
    );
    let desired_anchor = PhysicalPoint {
        x: session.start_anchor.x.saturating_add(pointer_delta_x),
        y: session.start_anchor.y.saturating_add(pointer_delta_y),
    };
    let monitor = monitor_for_anchor(&session.monitors, current_pointer.unwrap_or(desired_anchor))
        .ok_or_else(|| "No monitor is available".to_owned())?;
    let placement = resolve_drag_presence_placement_for_anchor(
        desired_anchor,
        render_size_for_scale(monitor.scale_factor),
        monitor.work_area,
        monitor.scale_factor,
        session.current_placement.expansion_direction,
    );
    if placement == session.current_placement {
        return Ok(true);
    }
    let crossed_surface = placement.monitor_work_area
        != session.current_placement.monitor_work_area
        || (placement.scale_factor - session.current_placement.scale_factor).abs() > f64::EPSILON;
    if crossed_surface && !session.native_renderer_suspended {
        let _ = app.emit_to(
            PET_RENDER_LABEL,
            PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT,
            NativeRendererLifecycleSignal {
                schema_version: 1,
                reason: "drag_suspended",
            },
        );
        session.native_renderer_suspended = true;
    }
    // Keep WebView2 stationary while the pointer is captured. Moving its transparent top-level
    // surface every frame can preserve stale compositor pixels; the native visual follows the
    // pointer and the input surface is synchronized once when the drag settles.
    move_pet_window_visual(app, Some(session.native_windows), &placement)?;
    session.current_placement = placement;
    set_presence_placement(&state, placement);
    Ok(true)
}

fn resolve_pet_drag_delta(
    start_pointer: PhysicalPoint,
    current_pointer: Option<PhysicalPoint>,
    requested_css_delta: (i32, i32),
    scale_factor: f64,
) -> (i32, i32) {
    current_pointer.map_or_else(
        || scaled_pet_drag_delta(requested_css_delta, scale_factor),
        |pointer| {
            (
                pointer.x.saturating_sub(start_pointer.x),
                pointer.y.saturating_sub(start_pointer.y),
            )
        },
    )
}

fn scaled_pet_drag_delta(requested_css_delta: (i32, i32), scale_factor: f64) -> (i32, i32) {
    let scale = if scale_factor.is_finite() {
        scale_factor.clamp(0.5, 4.0)
    } else {
        1.0
    };
    (
        (f64::from(requested_css_delta.0) * scale).round() as i32,
        (f64::from(requested_css_delta.1) * scale).round() as i32,
    )
}

fn calibrated_pet_drag_start_pointer(
    current_pointer: PhysicalPoint,
    initial_css_delta: (i32, i32),
    scale_factor: f64,
) -> PhysicalPoint {
    let delta = scaled_pet_drag_delta(initial_css_delta, scale_factor);
    PhysicalPoint {
        x: current_pointer.x.saturating_sub(delta.0),
        y: current_pointer.y.saturating_sub(delta.1),
    }
}

fn settle_pet_drag_placement(
    app: &tauri::AppHandle,
    state: &DesktopState,
    session: &PetGroupDragSession,
) -> Result<PresenceWindowPlacement, String> {
    let settled = session.current_placement;
    move_pet_window_group_with_geometry(
        app,
        Some(session.native_windows),
        &settled,
        Some(session.input_geometry),
    )?;
    set_presence_placement(state, settled);
    Ok(settled)
}

#[tauri::command]
async fn pet_window_group_end_drag(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
    session_id: String,
    expected_revision: u64,
) -> Result<DesktopPreferences, String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    validate_pet_drag_session_id(&session_id)?;
    let session = {
        let mut drag = state
            .pet_drag
            .lock()
            .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
        if drag.as_ref().map(|session| session.session_id.as_str()) != Some(session_id.as_str()) {
            return Err("PET_DRAG_SESSION_MISMATCH".to_owned());
        }
        drag.take()
    };
    let native_renderer_suspended = session
        .as_ref()
        .is_some_and(|session| session.native_renderer_suspended);
    let native_renderer_drag_active = session
        .as_ref()
        .is_some_and(|session| session.native_renderer_drag_active);
    let result = (|| {
        let current = DesktopPreferencesStore::new(&state.data_dir)
            .load()
            .map_err(|error| error.to_string())?;
        let placement = match session.as_ref() {
            Some(session) => settle_pet_drag_placement(&app, state.inner(), session)?,
            None => state
                .presence
                .latest_placement()
                .ok_or_else(|| "Pet placement is unavailable".to_owned())?,
        };
        if !current.pet_remember_position {
            return Ok(current);
        }
        let monitor = presence_monitors_for_app(&app)?
            .into_iter()
            .find(|candidate| candidate.work_area == placement.monitor_work_area)
            .ok_or_else(|| "Pet monitor is unavailable".to_owned())?;
        let (x_ratio, y_ratio) = anchor_ratios(placement.anchor, monitor.work_area);
        persist_pet_preferences(
            &app,
            &state,
            PetPreferencesUpdate {
                expected_revision,
                voice_auto_play_pet: None,
                pet_muted: None,
                pet_always_on_top: None,
                pet_anchor: Some(desktop_preferences::PetAnchorPreference {
                    monitor_id: monitor.id,
                    x_ratio,
                    y_ratio,
                }),
                clear_pet_anchor: false,
            },
        )
    })();
    state.presence.set_repositioning(false);
    let resume_result = if native_renderer_drag_active && !native_renderer_suspended {
        state
            .native_gpu
            .set_drag_active(false)
            .map_err(|error| error.to_string())
    } else {
        Ok(())
    };
    let _ = app.emit_to(
        PET_RENDER_LABEL,
        PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT,
        NativeRendererLifecycleSignal {
            schema_version: 1,
            reason: "drag_ended",
        },
    );
    match (result, resume_result) {
        (Err(error), _) => Err(error),
        (Ok(_), Err(error)) => Err(error),
        (Ok(preferences), Ok(())) => Ok(preferences),
    }
}

#[tauri::command]
async fn pet_window_group_reset_position(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
    expected_revision: u64,
) -> Result<DesktopPreferences, String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    state.presence.set_repositioning(false);
    let (native_renderer_drag_active, native_renderer_suspended) = state
        .pet_drag
        .lock()
        .map(|mut drag| {
            drag.take().map_or((false, false), |session| {
                (
                    session.native_renderer_drag_active,
                    session.native_renderer_suspended,
                )
            })
        })
        .unwrap_or((false, false));
    let result = (|| {
        let next = persist_pet_preferences(
            &app,
            &state,
            PetPreferencesUpdate {
                expected_revision,
                voice_auto_play_pet: None,
                pet_muted: None,
                pet_always_on_top: None,
                pet_anchor: None,
                clear_pet_anchor: true,
            },
        )?;
        let placement = place_pet_windows(&app, &next, Some(presence_native_windows(&state)?))?;
        set_presence_placement(&state, placement);
        Ok(next)
    })();
    let resume_result = if native_renderer_drag_active && !native_renderer_suspended {
        state
            .native_gpu
            .set_drag_active(false)
            .map_err(|error| error.to_string())
    } else {
        Ok(())
    };
    let _ = app.emit_to(
        PET_RENDER_LABEL,
        PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT,
        NativeRendererLifecycleSignal {
            schema_version: 1,
            reason: "drag_ended",
        },
    );
    match (result, resume_result) {
        (Err(error), _) => Err(error),
        (Ok(_), Err(error)) => Err(error),
        (Ok(preferences), Ok(())) => Ok(preferences),
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
enum PetInputLayout {
    Hidden,
    Core,
    Compact,
    Expanded,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct PetInputGeometry {
    layout: PetInputLayout,
    compact_width_logical: f64,
    compact_height_logical: f64,
    expanded_content_height_logical: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct PetInputWindowRegion {
    layout: PetInputLayout,
    width: u32,
    height: u32,
    compact_content_width: u32,
    compact_content_height: u32,
    expanded_content_height: u32,
    scale_factor: f64,
    direction: ExpansionDirection,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct PetInputPresentationFence {
    session_id: u64,
    revision: u64,
}

#[derive(Clone, Copy, Debug, serde::Deserialize)]
struct PetInputPresentationRequest {
    session_id: u64,
    revision: u64,
    layout: PetInputLayout,
    compact_width: Option<f64>,
    compact_height: Option<f64>,
    expanded_content_height: Option<f64>,
    interactive: bool,
    request_focus: bool,
}

fn pet_input_presentation_is_interactive(input: &PetInputPresentationRequest) -> bool {
    input.interactive && !matches!(input.layout, PetInputLayout::Hidden)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize)]
struct PetInputPresentationCommit {
    session_id: u64,
    revision: u64,
}

impl PetInputPresentationFence {
    fn begin(&mut self) -> PetInputPresentationCommit {
        self.session_id = self.session_id.saturating_add(1).max(1);
        self.revision = 0;
        self.commit()
    }

    fn validate(&self, session_id: u64, revision: u64) -> Result<(), &'static str> {
        if session_id == 0 || revision == 0 {
            return Err("PET_INPUT_PRESENTATION_INVALID_REVISION");
        }
        if session_id != self.session_id {
            return Err("PET_INPUT_PRESENTATION_STALE_SESSION");
        }
        if revision <= self.revision {
            return Err("PET_INPUT_PRESENTATION_STALE_REVISION");
        }
        Ok(())
    }

    fn commit(&self) -> PetInputPresentationCommit {
        PetInputPresentationCommit {
            session_id: self.session_id,
            revision: self.revision,
        }
    }
}

fn pet_input_geometry_from_frame(frame: PhysicalFrame, scale_factor: f64) -> PetInputGeometry {
    let scale = scale_factor.clamp(0.5, 4.0);
    let scaled = |logical: f64| (logical * scale).round() as u32;
    let core_extent = scaled(PET_CORE_EXTENT_LOGICAL);
    let compact_height = scaled(PET_INPUT_COMPACT_HEIGHT_LOGICAL);
    let expanded_height = scaled(PET_INPUT_EXPANDED_HEIGHT_LOGICAL);
    let core_distance = frame.width.abs_diff(core_extent) + frame.height.abs_diff(core_extent);
    let compact_distance = frame.height.abs_diff(compact_height);
    let expanded_distance = frame.height.abs_diff(expanded_height);
    let layout = if core_distance <= 4 {
        PetInputLayout::Core
    } else if expanded_distance < compact_distance {
        PetInputLayout::Expanded
    } else {
        PetInputLayout::Compact
    };
    PetInputGeometry {
        layout,
        compact_width_logical: (f64::from(frame.width) / scale).clamp(
            PET_INPUT_COMPACT_MIN_WIDTH_LOGICAL,
            PET_INPUT_COMPACT_MAX_WIDTH_LOGICAL,
        ),
        compact_height_logical: 64.0,
        expanded_content_height_logical: PET_INPUT_EXPANDED_CONTENT_HEIGHT_LOGICAL,
    }
}

fn pet_input_frame_for_geometry(
    placement: PresenceWindowPlacement,
    _geometry: PetInputGeometry,
) -> PhysicalFrame {
    let scale = placement.scale_factor.clamp(0.5, 4.0);
    let scaled = |logical: f64| (logical * scale).round() as u32;
    let compact_height = scaled(PET_INPUT_COMPACT_HEIGHT_LOGICAL);
    placement.input_frame(
        scaled(PET_INPUT_EXPANDED_WIDTH_LOGICAL),
        scaled(PET_INPUT_EXPANDED_HEIGHT_LOGICAL),
        compact_height,
    )
}

fn current_pet_input_geometry(state: &DesktopState) -> Result<PetInputGeometry, String> {
    state
        .pet_input_geometry
        .lock()
        .map(|geometry| *geometry)
        .map_err(|_| "Pet input geometry lock is unavailable".to_owned())
}

#[cfg(target_os = "windows")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PetInputRegionKind {
    Ellipse,
    RoundedRectangle,
}

#[cfg(target_os = "windows")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct PetInputRegionPart {
    kind: PetInputRegionKind,
    left: i32,
    top: i32,
    right: i32,
    bottom: i32,
    corner_diameter: i32,
}

#[cfg(target_os = "windows")]
fn pet_input_region_parts(region: PetInputWindowRegion) -> Vec<PetInputRegionPart> {
    let width = region.width.min(i32::MAX as u32) as i32;
    let height = region.height.min(i32::MAX as u32) as i32;
    let scale = region.scale_factor.clamp(0.5, 4.0);
    let scaled = |logical: f64| (logical * scale).round() as i32;
    let mut parts = Vec::with_capacity(2);

    if matches!(
        region.layout,
        PetInputLayout::Core | PetInputLayout::Compact | PetInputLayout::Expanded
    ) {
        let core_extent = scaled(PET_CORE_EXTENT_LOGICAL)
            .max(1)
            .min(width)
            .min(height);
        let core_top = scaled(116.0).clamp(0, height.saturating_sub(core_extent));
        let core_left = match region.direction {
            ExpansionDirection::Right => 0,
            ExpansionDirection::Left => width.saturating_sub(core_extent),
        };
        parts.push(PetInputRegionPart {
            kind: PetInputRegionKind::Ellipse,
            left: core_left,
            top: core_top,
            right: core_left.saturating_add(core_extent),
            bottom: core_top.saturating_add(core_extent),
            corner_diameter: 0,
        });
    }

    match region.layout {
        PetInputLayout::Compact => {
            let content_width = i32::try_from(region.compact_content_width)
                .unwrap_or(i32::MAX)
                .clamp(scaled(PET_INPUT_COMPACT_MIN_WIDTH_LOGICAL), width);
            let inset = scaled(8.0).clamp(0, content_width / 2);
            let (left, right) = match region.direction {
                ExpansionDirection::Right => (inset, content_width.saturating_sub(inset)),
                ExpansionDirection::Left => (
                    width.saturating_sub(content_width).saturating_add(inset),
                    width.saturating_sub(inset),
                ),
            };
            parts.push(PetInputRegionPart {
                kind: PetInputRegionKind::RoundedRectangle,
                left,
                top: height
                    .saturating_sub(scaled(8.0))
                    .saturating_sub(
                        i32::try_from(region.compact_content_height).unwrap_or(i32::MAX),
                    )
                    .clamp(0, height),
                right,
                bottom: height.saturating_sub(scaled(8.0)).clamp(0, height),
                corner_diameter: i32::try_from(region.compact_content_height)
                    .unwrap_or(i32::MAX)
                    .max(1),
            });
        }
        PetInputLayout::Expanded => {
            let content_inset = scaled(8.0).clamp(0, width / 2);
            let core_gutter = scaled(156.0).clamp(content_inset, width);
            let content_height = i32::try_from(region.expanded_content_height)
                .unwrap_or(i32::MAX)
                .clamp(scaled(56.0), height.saturating_sub(content_inset * 2));
            let (left, right) = match region.direction {
                ExpansionDirection::Right => (core_gutter, width.saturating_sub(content_inset)),
                ExpansionDirection::Left => (content_inset, width.saturating_sub(core_gutter)),
            };
            parts.push(PetInputRegionPart {
                kind: PetInputRegionKind::RoundedRectangle,
                left,
                top: height
                    .saturating_sub(content_inset)
                    .saturating_sub(content_height),
                right,
                bottom: height.saturating_sub(content_inset),
                corner_diameter: scaled(16.0).max(1),
            });
        }
        PetInputLayout::Hidden | PetInputLayout::Core => {}
    }
    parts
}

#[cfg(target_os = "windows")]
fn apply_pet_input_window_region(
    handle: PresenceNativeHandle,
    region: PetInputWindowRegion,
) -> Result<(), String> {
    use windows_sys::Win32::Foundation::HWND;
    use windows_sys::Win32::Graphics::Gdi::{
        CombineRgn, CreateEllipticRgn, CreateRectRgn, CreateRoundRectRgn, DeleteObject,
        SetWindowRgn, ERROR, HRGN, RGN_OR,
    };

    let _dpi_scope = PerMonitorDpiScope::enter();
    if matches!(region.layout, PetInputLayout::Hidden) {
        let empty = unsafe { CreateRectRgn(0, 0, 0, 0) };
        if empty.is_null() {
            return Err("PET_INPUT_REGION_CREATE_FAILED".to_owned());
        }
        if unsafe { SetWindowRgn(handle as HWND, empty, 1) } == 0 {
            unsafe { DeleteObject(empty) };
            return Err(format!(
                "PET_INPUT_REGION_APPLY_FAILED: {}",
                std::io::Error::last_os_error()
            ));
        }
        return Ok(());
    }
    let mut combined: HRGN = std::ptr::null_mut();
    for part in pet_input_region_parts(region) {
        let region = unsafe {
            match part.kind {
                PetInputRegionKind::Ellipse => {
                    CreateEllipticRgn(part.left, part.top, part.right, part.bottom)
                }
                PetInputRegionKind::RoundedRectangle => CreateRoundRectRgn(
                    part.left,
                    part.top,
                    part.right,
                    part.bottom,
                    part.corner_diameter,
                    part.corner_diameter,
                ),
            }
        };
        if region.is_null() {
            if !combined.is_null() {
                unsafe { DeleteObject(combined) };
            }
            return Err("PET_INPUT_REGION_CREATE_FAILED".to_owned());
        }
        if combined.is_null() {
            combined = region;
            continue;
        }
        let result = unsafe { CombineRgn(combined, combined, region, RGN_OR) };
        unsafe { DeleteObject(region) };
        if result == ERROR {
            unsafe { DeleteObject(combined) };
            return Err("PET_INPUT_REGION_COMBINE_FAILED".to_owned());
        }
    }

    if combined.is_null() {
        return Err("PET_INPUT_REGION_EMPTY".to_owned());
    }
    if unsafe { SetWindowRgn(handle as HWND, combined, 1) } == 0 {
        unsafe { DeleteObject(combined) };
        return Err(format!(
            "PET_INPUT_REGION_APPLY_FAILED: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn apply_pet_input_window_region(
    _handle: PresenceNativeHandle,
    _region: PetInputWindowRegion,
) -> Result<(), String> {
    Ok(())
}

fn apply_pet_input_layout(
    window: &WebviewWindow,
    state: &DesktopState,
    layout: PetInputLayout,
    compact_width: Option<f64>,
    compact_height: Option<f64>,
    expanded_content_height: Option<f64>,
) -> Result<(), String> {
    if state.presence.is_repositioning() {
        return Err("PET_INPUT_PRESENTATION_REPOSITIONING".to_owned());
    }
    if matches!(layout, PetInputLayout::Hidden) {
        #[cfg(target_os = "windows")]
        apply_pet_input_window_region(
            window.hwnd().map_err(|error| error.to_string())?.0 as isize,
            PetInputWindowRegion {
                layout,
                width: 0,
                height: 0,
                compact_content_width: 0,
                compact_content_height: 0,
                expanded_content_height: 0,
                scale_factor: 1.0,
                direction: ExpansionDirection::Right,
            },
        )?;
        *state
            .pet_input_geometry
            .lock()
            .map_err(|_| "Pet input geometry lock is unavailable".to_owned())? = PetInputGeometry {
            layout,
            compact_width_logical: compact_width.unwrap_or(PET_INPUT_COMPACT_WIDTH_LOGICAL),
            compact_height_logical: compact_height.unwrap_or(64.0),
            expanded_content_height_logical: expanded_content_height
                .unwrap_or(PET_INPUT_EXPANDED_CONTENT_HEIGHT_LOGICAL),
        };
        window
            .set_ignore_cursor_events(true)
            .map_err(|error| error.to_string())?;
        window
            .set_focusable(false)
            .map_err(|error| error.to_string())?;
        return window.hide().map_err(|error| error.to_string());
    }

    let app = window.app_handle();
    let _ = align_pet_input_to_native_presentation(app, state)?;
    let input = app
        .get_webview_window(PET_INPUT_LABEL)
        .ok_or_else(|| "Pet input window is unavailable".to_owned())?;
    let placement = state
        .presence
        .latest_placement()
        .ok_or_else(|| "Pet placement is unavailable".to_owned())?;
    let scale = placement.scale_factor.clamp(0.5, 4.0);
    let compact_width = compact_width
        .unwrap_or(PET_INPUT_COMPACT_WIDTH_LOGICAL)
        .clamp(
            PET_INPUT_COMPACT_MIN_WIDTH_LOGICAL,
            PET_INPUT_COMPACT_MAX_WIDTH_LOGICAL,
        );
    let compact_surface_height = compact_height.unwrap_or(64.0).clamp(64.0, 104.0);
    let expanded_content_height = expanded_content_height
        .unwrap_or(PET_INPUT_EXPANDED_CONTENT_HEIGHT_LOGICAL)
        .clamp(56.0, 344.0);
    let target_width = (PET_INPUT_EXPANDED_WIDTH_LOGICAL * scale).round() as u32;
    let target_height = (PET_INPUT_EXPANDED_HEIGHT_LOGICAL * scale).round() as u32;
    let compact_content_width = (compact_width * scale).round() as u32;
    let compact_content_height = (compact_surface_height * scale).round() as u32;
    let compact_frame_height = (PET_INPUT_COMPACT_HEIGHT_LOGICAL * scale).round() as u32;
    let anchored_frame = placement.input_frame(target_width, target_height, compact_frame_height);
    let frame = PhysicalFrame {
        x: anchored_frame.x,
        y: anchored_frame.y,
        width: anchored_frame.width,
        height: anchored_frame.height,
    };
    let native_windows = presence_native_windows(state)?;
    let input_handle = native_windows.handle_for(PET_INPUT_LABEL)?;
    // The top-level WebView remains at one allocation for core, input, cards, and menus. Only its
    // exact native hit region changes, eliminating the opaque resize frame seen on hover.
    move_presence_input_window(&input, input_handle, frame)?;
    apply_pet_input_window_region(
        input_handle,
        PetInputWindowRegion {
            layout,
            width: frame.width,
            height: frame.height,
            compact_content_width,
            compact_content_height,
            expanded_content_height: (expanded_content_height * scale).round() as u32,
            scale_factor: scale,
            direction: placement.expansion_direction,
        },
    )?;
    input.show().map_err(|error| error.to_string())?;
    *state
        .pet_input_geometry
        .lock()
        .map_err(|_| "Pet input geometry lock is unavailable".to_owned())? = PetInputGeometry {
        layout,
        compact_width_logical: compact_width,
        compact_height_logical: compact_surface_height,
        expanded_content_height_logical: expanded_content_height,
    };
    #[cfg(target_os = "windows")]
    enforce_presence_window_shell_policy(input_handle, None)?;
    Ok(())
}

#[tauri::command]
async fn pet_input_set_layout(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    layout: PetInputLayout,
    compact_width: Option<f64>,
    compact_height: Option<f64>,
    expanded_content_height: Option<f64>,
) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    apply_pet_input_layout(
        &window,
        state.inner(),
        layout,
        compact_width,
        compact_height,
        expanded_content_height,
    )
}

fn apply_pet_input_interactive(
    window: &WebviewWindow,
    state: &DesktopState,
    interactive: bool,
    focusable: bool,
) -> Result<(), String> {
    if state.presence.is_repositioning() && !interactive {
        return Err("PET_INPUT_PRESENTATION_REPOSITIONING".to_owned());
    }
    let result = if interactive {
        window
            .set_focusable(focusable)
            .map_err(|error| error.to_string())?;
        window
            .set_ignore_cursor_events(false)
            .map_err(|error| error.to_string())
    } else {
        window
            .set_ignore_cursor_events(true)
            .map_err(|error| error.to_string())?;
        window
            .set_focusable(false)
            .map_err(|error| error.to_string())
    };
    result?;
    #[cfg(target_os = "windows")]
    enforce_presence_window_shell_policy(
        window.hwnd().map_err(|error| error.to_string())?.0 as isize,
        Some(interactive && focusable),
    )?;
    Ok(())
}

#[tauri::command]
async fn pet_input_set_interactive(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    interactive: bool,
) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    apply_pet_input_interactive(&window, state.inner(), interactive, interactive)
}

fn apply_pet_input_focus(window: &WebviewWindow, state: &DesktopState) -> Result<(), String> {
    if state.presence.is_repositioning() {
        return Err("PET_INPUT_PRESENTATION_REPOSITIONING".to_owned());
    }
    window
        .set_focusable(true)
        .map_err(|error| error.to_string())?;
    window
        .set_ignore_cursor_events(false)
        .map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    #[cfg(target_os = "windows")]
    enforce_presence_window_shell_policy(
        window.hwnd().map_err(|error| error.to_string())?.0 as isize,
        Some(true),
    )?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
async fn pet_input_request_focus(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    apply_pet_input_focus(&window, state.inner())
}

#[tauri::command]
async fn pet_input_presentation_begin(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<PetInputPresentationCommit, String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let mut fence = state
        .pet_input_presentation
        .lock()
        .map_err(|_| "Pet input presentation lock is unavailable".to_owned())?;
    Ok(fence.begin())
}

#[tauri::command]
fn pet_interaction_snapshot_get(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<Option<presence_coordinator::PresenceInteractionSnapshot>, String> {
    if !matches!(window.label(), PET_INPUT_LABEL | PET_RENDER_LABEL) {
        return Err("Window is not authorized".to_owned());
    }
    Ok(state.presence.latest_interaction())
}

#[tauri::command]
async fn pet_input_presentation_apply(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: PetInputPresentationRequest,
) -> Result<PetInputPresentationCommit, String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    if input.request_focus && (!input.interactive || matches!(input.layout, PetInputLayout::Hidden))
    {
        return Err("PET_INPUT_PRESENTATION_FOCUS_REQUIRES_INTERACTION".to_owned());
    }

    // Hidden WebView2 windows must remain both non-focusable and click-through. Re-enabling
    // interaction after hide can make WebView2 show its full fixed allocation, which appears as
    // an opaque strip and blocks the desktop outside Fairy's circular core.
    let interactive = pet_input_presentation_is_interactive(&input);

    let mut fence = state
        .pet_input_presentation
        .lock()
        .map_err(|_| "Pet input presentation lock is unavailable".to_owned())?;
    fence
        .validate(input.session_id, input.revision)
        .map_err(str::to_owned)?;

    // A visible interactive surface must never become click-through for a presentation update:
    // that brief gap lets a held drag reach the application underneath Fairy. Non-interactive
    // transitions are disabled before geometry changes; interactive transitions keep the old
    // exact HRGN until the replacement region is committed.
    if !interactive {
        apply_pet_input_interactive(&window, state.inner(), false, false)?;
    }
    apply_pet_input_layout(
        &window,
        state.inner(),
        input.layout,
        input.compact_width,
        input.compact_height,
        input.expanded_content_height,
    )?;
    if interactive {
        apply_pet_input_interactive(
            &window,
            state.inner(),
            true,
            !matches!(input.layout, PetInputLayout::Core),
        )?;
    }
    if input.request_focus {
        apply_pet_input_focus(&window, state.inner())?;
    }

    fence.revision = input.revision;
    Ok(fence.commit())
}

#[tauri::command]
async fn pet_exit(window: WebviewWindow) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    window.app_handle().exit(0);
    Ok(())
}

fn show_and_focus(window: &WebviewWindow) -> Result<(), String> {
    window.show().map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

fn main_window(app: &tauri::AppHandle) -> Result<WebviewWindow, String> {
    if let Some(window) = app.get_webview_window("main") {
        return Ok(window);
    }
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Fairy")
        .inner_size(1440.0, 900.0)
        .min_inner_size(880.0, 680.0)
        .resizable(true)
        .visible(false)
        .build()
        .map_err(|error| error.to_string())
}

fn settings_window(app: &tauri::AppHandle) -> Result<WebviewWindow, String> {
    if let Some(window) = app.get_webview_window("settings") {
        return Ok(window);
    }
    WebviewWindowBuilder::new(
        app,
        "settings",
        WebviewUrl::App("index.html?surface=settings".into()),
    )
    .title("Fairy Settings")
    .inner_size(980.0, 720.0)
    .min_inner_size(760.0, 560.0)
    .resizable(true)
    .visible(false)
    .build()
    .map_err(|error| error.to_string())
}

#[tauri::command]
async fn open_main_window(window: WebviewWindow) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    show_and_focus(&main_window(window.app_handle())?)
}

#[tauri::command]
fn pet_renderer_report_health(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    report: PresenceRendererHealthReport,
) -> Result<PresenceRendererDirective, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    if !report.is_valid() {
        return Err("Unsupported renderer health schema".to_owned());
    }
    *state
        .renderer_health
        .lock()
        .map_err(|_| "Renderer health is unavailable".to_owned())? = Some(report.clone());
    let renderer_ready = matches!(
        report.status,
        PresenceRendererStatus::Running | PresenceRendererStatus::Fallback
    );
    if renderer_ready
        && state
            .pet_placement_reconciled
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    {
        if let Err(error) = reconcile_pet_window_placement(window.app_handle()) {
            state
                .pet_placement_reconciled
                .store(false, Ordering::Release);
            return Err(format!("Pet window placement is unavailable: {error}"));
        }
    }
    let now_ms = state
        .started_at
        .elapsed()
        .as_millis()
        .min(u128::from(u64::MAX)) as u64;
    let directive = state
        .renderer_supervisor
        .lock()
        .map_err(|_| "Renderer supervisor is unavailable".to_owned())?
        .observe_at(report, now_ms);
    if directive == PresenceRendererDirective::DisablePet {
        hide_pet_windows(window.app_handle());
    }
    Ok(directive)
}

#[tauri::command]
fn pet_renderer_get_health(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<Option<PresenceRendererHealthReport>, String> {
    if authorize_settings_window(window.label()).is_err()
        && authorize_core_rpc_window(window.label()).is_err()
    {
        return Err("Window is not authorized".to_owned());
    }
    state
        .renderer_health
        .lock()
        .map(|health| health.clone())
        .map_err(|_| "Renderer health is unavailable".to_owned())
}

fn hide_pet_windows(app: &tauri::AppHandle) {
    stop_native_presence(app);
    for label in [PET_RENDER_LABEL, PET_INPUT_LABEL] {
        if let Some(window) = app.get_webview_window(label) {
            let _ = window.hide();
        }
    }
}

fn stop_native_presence(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<DesktopState>() {
        let _ = state.native_gpu.stop();
    }
}

#[derive(Clone, Copy, serde::Serialize)]
struct NativeRendererLifecycleSignal {
    schema_version: u16,
    reason: &'static str,
}

fn reset_native_presence(app: &tauri::AppHandle, reason: &'static str) {
    let Some(state) = app.try_state::<DesktopState>() else {
        return;
    };
    if state
        .native_gpu_reset_in_flight
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return;
    }
    let lifecycle = state.native_gpu.status().lifecycle;
    if !matches!(
        lifecycle,
        NativeGpuLifecycle::Starting | NativeGpuLifecycle::Running | NativeGpuLifecycle::Failed
    ) {
        state
            .native_gpu_reset_in_flight
            .store(false, Ordering::Release);
        return;
    }
    let manager = Arc::clone(&state.native_gpu);
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Err(error) = manager.stop() {
            eprintln!("[presence-native-gpu] reset stop failed: reason={reason}; error={error}");
        }
        let _ = app.emit_to(
            PET_RENDER_LABEL,
            PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT,
            NativeRendererLifecycleSignal {
                schema_version: 1,
                reason,
            },
        );
        if let Some(state) = app.try_state::<DesktopState>() {
            state
                .native_gpu_reset_in_flight
                .store(false, Ordering::Release);
        }
    });
}

fn reset_native_presence_after_resume(app: &tauri::AppHandle) {
    reset_native_presence(app, "resume");
    let Some(state) = app.try_state::<DesktopState>() else {
        return;
    };
    if let Ok(preferences) = DesktopPreferencesStore::new(&state.data_dir).load() {
        let _ = apply_pet_window_preferences(app, &preferences);
    }
}

fn pet_session_disabled(app: &tauri::AppHandle) -> bool {
    app.try_state::<DesktopState>().is_some_and(|state| {
        state
            .renderer_supervisor
            .lock()
            .map(|supervisor| supervisor.session_disabled())
            .unwrap_or(true)
    })
}

fn apply_pet_window_preferences(
    app: &tauri::AppHandle,
    preferences: &DesktopPreferences,
) -> Result<(), String> {
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    let input = app.get_webview_window(PET_INPUT_LABEL);
    render
        .set_always_on_top(preferences.pet_always_on_top)
        .map_err(|error| error.to_string())?;
    if let Some(input) = &input {
        input
            .set_always_on_top(preferences.pet_always_on_top)
            .map_err(|error| error.to_string())?;
    }
    if let Some(state) = app.try_state::<DesktopState>() {
        state
            .native_gpu
            .set_always_on_top(preferences.pet_always_on_top)
            .map_err(|error| error.to_string())?;
    }
    let enabled = preferences.pet_enabled && !pet_session_disabled(app);
    if !enabled {
        stop_native_presence(app);
        render.hide().map_err(|error| error.to_string())?;
        if let Some(input) = input {
            input.hide().map_err(|error| error.to_string())?;
        }
        return Ok(());
    }
    if input.is_none() {
        render.hide().map_err(|error| error.to_string())?;
        return main_window(app).and_then(|window| show_and_focus(&window));
    }
    let native_owns_surface = app.try_state::<DesktopState>().is_some_and(|state| {
        matches!(
            state.native_gpu.status().lifecycle,
            NativeGpuLifecycle::Starting | NativeGpuLifecycle::Running
        )
    });
    let startup_ready = app
        .try_state::<DesktopState>()
        .is_some_and(|state| state.presence_startup.ready());
    if native_owns_surface || !startup_ready {
        render.hide().map_err(|error| error.to_string())?;
    } else {
        show_presence_tracking_window(app)?;
    }
    Ok(())
}

fn coordinator_config(preferences: &DesktopPreferences) -> PresenceCoordinatorConfig {
    PresenceCoordinatorConfig {
        reduced_motion: preferences.reduced_motion || !preferences.pet_motion_enabled,
        hover_enabled: preferences.pet_hover_enabled && !preferences.pet_do_not_disturb,
        hover_dwell_ms: preferences.pet_hover_dwell_ms,
    }
}

#[cfg(not(target_os = "windows"))]
fn presence_monitors(render: &WebviewWindow) -> Result<Vec<PresenceMonitor>, String> {
    let primary = render
        .primary_monitor()
        .map_err(|error| error.to_string())?;
    render
        .available_monitors()
        .map_err(|error| error.to_string())?
        .into_iter()
        .map(|monitor| {
            let work = monitor.work_area();
            let work_area = PhysicalFrame {
                x: work.position.x,
                y: work.position.y,
                width: work.size.width,
                height: work.size.height,
            };
            let id = format!(
                "{}:{}:{}:{}:{}",
                monitor.name().map_or("monitor", String::as_str),
                work_area.x,
                work_area.y,
                work_area.width,
                work_area.height,
            );
            let is_primary = primary.as_ref().is_some_and(|candidate| {
                candidate.position() == monitor.position() && candidate.size() == monitor.size()
            });
            Ok(PresenceMonitor {
                id,
                work_area,
                scale_factor: monitor.scale_factor(),
                is_primary,
            })
        })
        .collect()
}

#[cfg(target_os = "windows")]
fn presence_monitors_for_app(_app: &tauri::AppHandle) -> Result<Vec<PresenceMonitor>, String> {
    use windows_sys::core::BOOL;
    use windows_sys::Win32::Foundation::{LPARAM, RECT};
    use windows_sys::Win32::Graphics::Gdi::{
        EnumDisplayMonitors, GetMonitorInfoW, HDC, HMONITOR, MONITORINFO, MONITORINFOEXW,
    };
    use windows_sys::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
    use windows_sys::Win32::UI::WindowsAndMessaging::MONITORINFOF_PRIMARY;

    unsafe extern "system" fn collect_monitor(
        monitor: HMONITOR,
        _device_context: HDC,
        _monitor_rect: *mut RECT,
        context: LPARAM,
    ) -> BOOL {
        let monitors = unsafe { &mut *(context as *mut Vec<PresenceMonitor>) };
        let mut info = MONITORINFOEXW::default();
        info.monitorInfo.cbSize = std::mem::size_of::<MONITORINFOEXW>() as u32;
        if unsafe { GetMonitorInfoW(monitor, std::ptr::addr_of_mut!(info).cast::<MONITORINFO>()) }
            == 0
        {
            return 1;
        }
        let work = info.monitorInfo.rcWork;
        let width = work.right.saturating_sub(work.left);
        let height = work.bottom.saturating_sub(work.top);
        if width <= 0 || height <= 0 {
            return 1;
        }
        let name_end = info
            .szDevice
            .iter()
            .position(|value| *value == 0)
            .unwrap_or(info.szDevice.len());
        let mut id = String::from_utf16_lossy(&info.szDevice[..name_end]);
        if id.is_empty() {
            id = format!("monitor-{:x}", monitor as usize);
        }
        let mut dpi_x = 96;
        let mut dpi_y = 96;
        if unsafe {
            GetDpiForMonitor(
                monitor,
                MDT_EFFECTIVE_DPI,
                std::ptr::addr_of_mut!(dpi_x),
                std::ptr::addr_of_mut!(dpi_y),
            )
        } < 0
        {
            dpi_x = 96;
        }
        monitors.push(PresenceMonitor {
            id,
            work_area: PhysicalFrame {
                x: work.left,
                y: work.top,
                width: width as u32,
                height: height as u32,
            },
            scale_factor: (f64::from(dpi_x) / 96.0).clamp(0.5, 4.0),
            is_primary: info.monitorInfo.dwFlags & MONITORINFOF_PRIMARY != 0,
        });
        1
    }

    let _dpi_scope = PerMonitorDpiScope::enter();
    let mut monitors: Vec<PresenceMonitor> = Vec::new();
    if unsafe {
        EnumDisplayMonitors(
            std::ptr::null_mut(),
            std::ptr::null(),
            Some(collect_monitor),
            std::ptr::addr_of_mut!(monitors) as LPARAM,
        )
    } == 0
    {
        return Err("PRESENCE_MONITOR_ENUMERATION_FAILED".to_owned());
    }
    monitors.sort_by_key(|monitor| (monitor.work_area.x, monitor.work_area.y));
    if monitors.is_empty() {
        return Err("PRESENCE_MONITOR_UNAVAILABLE".to_owned());
    }
    Ok(monitors)
}

#[cfg(not(target_os = "windows"))]
fn presence_monitors_for_app(app: &tauri::AppHandle) -> Result<Vec<PresenceMonitor>, String> {
    presence_monitors(&main_window(app)?)
}

fn monitor_for_anchor(
    monitors: &[PresenceMonitor],
    anchor: PhysicalPoint,
) -> Option<&PresenceMonitor> {
    let work_areas = monitors
        .iter()
        .map(|monitor| monitor.work_area)
        .collect::<Vec<_>>();
    let selected = select_work_area(anchor, &work_areas)?;
    monitors
        .iter()
        .find(|monitor| monitor.work_area == selected)
}

fn render_size_for_scale(scale_factor: f64) -> (u32, u32) {
    let scale = scale_factor.clamp(0.5, 4.0);
    (
        (640.0 * scale).round() as u32,
        (260.0 * scale).round() as u32,
    )
}

fn default_pet_anchor(monitor: &PresenceMonitor) -> PhysicalPoint {
    let margin = (104.0 * monitor.scale_factor.clamp(0.5, 4.0)).round() as i64;
    PhysicalPoint {
        x: (monitor.work_area.right() - margin) as i32,
        y: (monitor.work_area.bottom() - margin) as i32,
    }
}

fn place_pet_windows(
    app: &tauri::AppHandle,
    preferences: &DesktopPreferences,
    native_windows: Option<PresenceNativeWindows>,
) -> Result<PresenceWindowPlacement, String> {
    let monitors = presence_monitors_for_app(app)
        .map_err(|error| format!("Pet monitor discovery failed: {error}"))?;
    let remembered = preferences
        .pet_remember_position
        .then_some(preferences.pet_anchor.as_ref())
        .flatten();
    let monitor = remembered
        .and_then(|anchor| {
            monitors
                .iter()
                .find(|monitor| monitor.id == anchor.monitor_id)
        })
        .or_else(|| monitors.iter().find(|monitor| monitor.is_primary))
        .or_else(|| monitors.first())
        .ok_or_else(|| "No monitor is available".to_owned())?;
    let anchor = remembered
        .filter(|anchor| anchor.monitor_id == monitor.id)
        .map(|anchor| anchor_from_ratios(monitor.work_area, anchor.x_ratio, anchor.y_ratio))
        .unwrap_or_else(|| default_pet_anchor(monitor));
    let direction = if anchor.x
        >= monitor.work_area.x + i32::try_from(monitor.work_area.width / 2).unwrap_or(i32::MAX)
    {
        ExpansionDirection::Left
    } else {
        ExpansionDirection::Right
    };
    let placement = resolve_presence_placement_for_anchor(
        anchor,
        render_size_for_scale(monitor.scale_factor),
        monitor.work_area,
        monitor.scale_factor,
        Some(direction),
    );
    move_pet_window_group(app, native_windows, &placement)
        .map_err(|error| format!("Initial pet group placement failed: {error}"))?;
    Ok(placement)
}

fn placement_from_actual_pet_windows(
    render_frame: PhysicalFrame,
    input_frame: PhysicalFrame,
    monitors: &[PresenceMonitor],
    previous: Option<PresenceWindowPlacement>,
    native_presentation: Option<NativeGpuPresentation>,
) -> Result<(PresenceWindowPlacement, PetInputGeometry), String> {
    if let Some(presentation) = native_presentation {
        let placement = placement_from_native_presentation(render_frame, monitors, presentation)?;
        return Ok((
            placement,
            pet_input_geometry_from_frame(input_frame, placement.scale_factor),
        ));
    }
    let input_center = physical_frame_center(input_frame);
    let render_center = physical_frame_center(render_frame);
    let monitor = monitor_for_anchor(monitors, input_center)
        .or_else(|| monitor_for_anchor(monitors, render_center))
        .ok_or_else(|| "No monitor is available".to_owned())?;
    let geometry = pet_input_geometry_from_frame(input_frame, monitor.scale_factor);
    let mut candidates = [ExpansionDirection::Right, ExpansionDirection::Left].map(|direction| {
        let placement = resolve_presence_placement(
            render_frame,
            monitor.work_area,
            monitor.scale_factor,
            Some(direction),
        );
        let score = if geometry.layout == PetInputLayout::Core {
            point_distance_squared(placement.anchor, input_center)
        } else {
            frame_distance(
                pet_input_frame_for_geometry(placement, geometry),
                input_frame,
            )
        };
        let tie_break = u8::from(
            previous
                .is_some_and(|value| value.expansion_direction != placement.expansion_direction),
        );
        (score, tie_break, placement)
    });
    candidates.sort_by_key(|(score, tie_break, _)| (*score, *tie_break));
    let mut placement = candidates[0].2;
    if geometry.layout == PetInputLayout::Core {
        placement = resolve_presence_placement_for_anchor(
            input_center,
            (render_frame.width, render_frame.height),
            monitor.work_area,
            monitor.scale_factor,
            Some(placement.expansion_direction),
        );
    }
    Ok((placement, geometry))
}

fn placement_from_native_presentation(
    render_frame: PhysicalFrame,
    monitors: &[PresenceMonitor],
    presentation: NativeGpuPresentation,
) -> Result<PresenceWindowPlacement, String> {
    let render_scale = (f64::from(render_frame.width) / 640.0).clamp(0.5, 4.0);
    let anchor = PhysicalPoint {
        x: (f64::from(render_frame.x) + f64::from(presentation.core_x) * render_scale).round()
            as i32,
        y: (f64::from(render_frame.y) + f64::from(presentation.core_y) * render_scale).round()
            as i32,
    };
    let monitor =
        monitor_for_anchor(monitors, anchor).ok_or_else(|| "No monitor is available".to_owned())?;
    let direction = match presentation.expansion_direction {
        NativeGpuExpansionDirection::Left => ExpansionDirection::Left,
        NativeGpuExpansionDirection::Right => ExpansionDirection::Right,
    };
    // The DirectComposition surface is already the visible fact. Re-resolving it through the
    // work-area clamp here can move the renderer before the first drag sample and produce the
    // characteristic flash back to an older edge position. Preserve this exact frame and only
    // derive the companion frames around it.
    let scale_factor = render_scale;
    let compact_width = (PET_INPUT_COMPACT_WIDTH_LOGICAL * scale_factor).round() as u32;
    let compact_height = (PET_INPUT_COMPACT_HEIGHT_LOGICAL * scale_factor).round() as u32;
    let expanded_width = (PET_INPUT_EXPANDED_WIDTH_LOGICAL * scale_factor).round() as u32;
    let expanded_height = (PET_INPUT_EXPANDED_HEIGHT_LOGICAL * scale_factor).round() as u32;
    let mut placement = PresenceWindowPlacement {
        anchor,
        render_frame,
        input_compact_frame: render_frame,
        input_expanded_frame: render_frame,
        monitor_work_area: monitor.work_area,
        scale_factor,
        expansion_direction: direction,
    };
    placement.input_compact_frame =
        placement.input_frame(compact_width, compact_height, compact_height);
    placement.input_expanded_frame =
        placement.input_frame(expanded_width, expanded_height, compact_height);
    Ok(placement)
}

fn presence_visible_render_frame(
    render: &WebviewWindow,
    tracking_handle: PresenceNativeHandle,
    state: &DesktopState,
) -> Result<PhysicalFrame, String> {
    let tracking_frame = presence_window_frame(render, tracking_handle)?;
    #[cfg(target_os = "windows")]
    if let Some(surface_handle) = state.native_gpu.surface_handle() {
        if let Ok(surface_frame) = native_presence_window_frame(surface_handle) {
            return Ok(surface_frame);
        }
    }
    Ok(tracking_frame)
}

fn align_pet_input_to_native_presentation(
    app: &tauri::AppHandle,
    state: &DesktopState,
) -> Result<Option<PresenceWindowPlacement>, String> {
    if state.presence.is_repositioning() {
        return Ok(None);
    }
    let Some(presentation) = state.native_gpu.presentation() else {
        return Ok(None);
    };
    let native_windows = presence_native_windows(state)?;
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    let input = app
        .get_webview_window(PET_INPUT_LABEL)
        .ok_or_else(|| "Pet input window is unavailable".to_owned())?;
    let render_handle = native_windows.handle_for(PET_RENDER_LABEL)?;
    let input_handle = native_windows.handle_for(PET_INPUT_LABEL)?;
    let tracking_frame = presence_window_frame(&render, render_handle)?;
    let render_frame = presence_visible_render_frame(&render, render_handle, state)?;
    let input_frame = presence_window_frame(&input, input_handle)?;
    let monitors = presence_monitors_for_app(app)?;
    let placement = placement_from_native_presentation(render_frame, &monitors, presentation)?;
    let geometry = current_pet_input_geometry(state)?;
    let target_input_frame = pet_input_frame_for_geometry(placement, geometry);
    if tracking_frame != render_frame {
        move_presence_window_positions(
            &render,
            render_handle,
            render_frame,
            Some((&input, input_handle, target_input_frame)),
        )?;
    } else if target_input_frame != input_frame {
        move_presence_input_window(&input, input_handle, target_input_frame)?;
    }
    // The frame can remain unchanged while the expansion direction flips. Reapply the region so
    // the native hit surface follows the visible core instead of retaining the old-side mask.
    apply_pet_input_window_region(
        input_handle,
        PetInputWindowRegion {
            layout: geometry.layout,
            width: target_input_frame.width,
            height: target_input_frame.height,
            compact_content_width: (geometry.compact_width_logical * placement.scale_factor)
                .round()
                .max(0.0) as u32,
            compact_content_height: (geometry.compact_height_logical * placement.scale_factor)
                .round()
                .max(0.0) as u32,
            expanded_content_height: (geometry.expanded_content_height_logical
                * placement.scale_factor)
                .round()
                .max(0.0) as u32,
            scale_factor: placement.scale_factor,
            direction: placement.expansion_direction,
        },
    )?;
    set_presence_placement(state, placement);
    Ok(Some(placement))
}

fn physical_frame_center(frame: PhysicalFrame) -> PhysicalPoint {
    PhysicalPoint {
        x: frame
            .x
            .saturating_add(i32::try_from(frame.width / 2).unwrap_or(i32::MAX)),
        y: frame
            .y
            .saturating_add(i32::try_from(frame.height / 2).unwrap_or(i32::MAX)),
    }
}

fn point_distance_squared(left: PhysicalPoint, right: PhysicalPoint) -> u64 {
    let dx = i64::from(left.x) - i64::from(right.x);
    let dy = i64::from(left.y) - i64::from(right.y);
    dx.unsigned_abs()
        .saturating_mul(dx.unsigned_abs())
        .saturating_add(dy.unsigned_abs().saturating_mul(dy.unsigned_abs()))
}

fn frame_distance(left: PhysicalFrame, right: PhysicalFrame) -> u64 {
    i64::from(left.x)
        .abs_diff(i64::from(right.x))
        .saturating_add(i64::from(left.y).abs_diff(i64::from(right.y)))
        .saturating_add(u64::from(left.width.abs_diff(right.width)))
        .saturating_add(u64::from(left.height.abs_diff(right.height)))
}

fn move_pet_window_group(
    app: &tauri::AppHandle,
    native_windows: Option<PresenceNativeWindows>,
    placement: &PresenceWindowPlacement,
) -> Result<(), String> {
    move_pet_window_group_with_geometry(app, native_windows, placement, None)
}

fn move_pet_window_visual(
    app: &tauri::AppHandle,
    native_windows: Option<PresenceNativeWindows>,
    placement: &PresenceWindowPlacement,
) -> Result<(), String> {
    let native_windows =
        native_windows.ok_or_else(|| "Pet native windows are unavailable".to_owned())?;
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    let render_handle = native_windows.handle_for(PET_RENDER_LABEL)?;
    move_presence_window_positions(&render, render_handle, placement.render_frame, None)
}

fn move_pet_window_group_during_drag(
    app: &tauri::AppHandle,
    native_windows: PresenceNativeWindows,
    placement: &PresenceWindowPlacement,
    input_geometry: PetInputGeometry,
    scale_changed: bool,
) -> Result<(), String> {
    if matches!(input_geometry.layout, PetInputLayout::Hidden) {
        return move_pet_window_visual(app, Some(native_windows), placement);
    }
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    let input = app
        .get_webview_window(PET_INPUT_LABEL)
        .ok_or_else(|| "Pet input window is unavailable".to_owned())?;
    let render_handle = native_windows.handle_for(PET_RENDER_LABEL)?;
    let input_handle = native_windows.handle_for(PET_INPUT_LABEL)?;
    let input_frame = pet_input_frame_for_geometry(*placement, input_geometry);
    move_presence_window_positions(
        &render,
        render_handle,
        placement.render_frame,
        Some((&input, input_handle, input_frame)),
    )?;
    if scale_changed {
        let scale = placement.scale_factor.clamp(0.5, 4.0);
        apply_pet_input_window_region(
            input_handle,
            PetInputWindowRegion {
                layout: input_geometry.layout,
                width: input_frame.width,
                height: input_frame.height,
                compact_content_width: (input_geometry.compact_width_logical * scale)
                    .round()
                    .max(0.0) as u32,
                compact_content_height: (input_geometry.compact_height_logical * scale)
                    .round()
                    .max(0.0) as u32,
                expanded_content_height: (input_geometry.expanded_content_height_logical * scale)
                    .round()
                    .max(0.0) as u32,
                scale_factor: scale,
                direction: placement.expansion_direction,
            },
        )?;
    }
    Ok(())
}

fn move_pet_window_group_with_geometry(
    app: &tauri::AppHandle,
    native_windows: Option<PresenceNativeWindows>,
    placement: &PresenceWindowPlacement,
    input_geometry: Option<PetInputGeometry>,
) -> Result<(), String> {
    let native_windows =
        native_windows.ok_or_else(|| "Pet native windows are unavailable".to_owned())?;
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    let render_handle = native_windows.handle_for(PET_RENDER_LABEL)?;
    let Some(input) = app.get_webview_window(PET_INPUT_LABEL) else {
        return move_presence_window_positions(
            &render,
            render_handle,
            placement.render_frame,
            None,
        );
    };
    let input_handle = native_windows.handle_for(PET_INPUT_LABEL)?;
    let input_frame = presence_window_frame(&input, input_handle)?;
    let scale = placement.scale_factor.clamp(0.5, 4.0);
    let geometry = input_geometry
        .unwrap_or_else(|| pet_input_geometry_from_frame(input_frame, placement.scale_factor));
    if matches!(geometry.layout, PetInputLayout::Hidden) {
        input
            .set_ignore_cursor_events(true)
            .map_err(|error| error.to_string())?;
        input
            .set_focusable(false)
            .map_err(|error| error.to_string())?;
        input.hide().map_err(|error| error.to_string())?;
        return move_presence_window_positions(
            &render,
            render_handle,
            placement.render_frame,
            None,
        );
    }
    let frame = pet_input_frame_for_geometry(*placement, geometry);
    move_presence_window_positions(
        &render,
        render_handle,
        placement.render_frame,
        Some((&input, input_handle, frame)),
    )?;
    apply_pet_input_window_region(
        input_handle,
        PetInputWindowRegion {
            layout: geometry.layout,
            width: frame.width,
            height: frame.height,
            compact_content_width: (geometry.compact_width_logical * scale).round().max(0.0) as u32,
            compact_content_height: (geometry.compact_height_logical * scale).round().max(0.0)
                as u32,
            expanded_content_height: (geometry.expanded_content_height_logical * scale)
                .round()
                .max(0.0) as u32,
            scale_factor: scale,
            direction: placement.expansion_direction,
        },
    )
}

fn show_presence_tracking_window(app: &tauri::AppHandle) -> Result<(), String> {
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    render.set_title("").map_err(|error| error.to_string())?;
    render
        .set_skip_taskbar(true)
        .map_err(|error| error.to_string())?;
    render
        .set_focusable(false)
        .map_err(|error| error.to_string())?;
    #[cfg(target_os = "windows")]
    enforce_presence_window_shell_policy(
        render.hwnd().map_err(|error| error.to_string())?.0 as isize,
        Some(false),
    )?;
    render.show().map_err(|error| error.to_string())?;
    #[cfg(target_os = "windows")]
    enforce_presence_window_shell_policy(
        render.hwnd().map_err(|error| error.to_string())?.0 as isize,
        Some(false),
    )?;
    render
        .set_ignore_cursor_events(true)
        .map_err(|error| error.to_string())
}

#[cfg(target_os = "windows")]
fn enforce_presence_window_shell_policy(
    handle: PresenceNativeHandle,
    focusable: Option<bool>,
) -> Result<(), String> {
    use windows_sys::Win32::Foundation::{GetLastError, SetLastError};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, SetWindowPos, SetWindowTextW, GWL_EXSTYLE,
        SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, WS_EX_APPWINDOW,
        WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
    };

    let hwnd = native_presence_hwnd(handle);
    let current = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) };
    let mut next = (current as u32 & !WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW;
    match focusable {
        Some(true) => next &= !WS_EX_NOACTIVATE,
        Some(false) => next |= WS_EX_NOACTIVATE,
        None => {}
    }
    unsafe { SetLastError(0) };
    let previous = unsafe { SetWindowLongPtrW(hwnd, GWL_EXSTYLE, next as isize) };
    if previous == 0 && unsafe { GetLastError() } != 0 {
        return Err("PRESENCE_WINDOW_STYLE_FAILED".to_owned());
    }
    let empty_title = [0_u16];
    if unsafe { SetWindowTextW(hwnd, empty_title.as_ptr()) } == 0 {
        return Err("PRESENCE_WINDOW_TITLE_FAILED".to_owned());
    }
    if unsafe {
        SetWindowPos(
            hwnd,
            std::ptr::null_mut(),
            0,
            0,
            0,
            0,
            SWP_FRAMECHANGED | SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER,
        )
    } == 0
    {
        return Err("PRESENCE_WINDOW_STYLE_REFRESH_FAILED".to_owned());
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn native_presence_hwnd(handle: PresenceNativeHandle) -> windows_sys::Win32::Foundation::HWND {
    handle as windows_sys::Win32::Foundation::HWND
}

#[cfg(target_os = "windows")]
fn native_presence_window_frame(handle: PresenceNativeHandle) -> Result<PhysicalFrame, String> {
    use windows_sys::Win32::Foundation::RECT;
    use windows_sys::Win32::UI::WindowsAndMessaging::GetWindowRect;

    let _dpi_scope = PerMonitorDpiScope::enter();
    let mut rect = RECT::default();
    if unsafe { GetWindowRect(native_presence_hwnd(handle), std::ptr::addr_of_mut!(rect)) } == 0 {
        return Err("PRESENCE_NATIVE_FRAME_UNAVAILABLE".to_owned());
    }
    let width = rect.right.saturating_sub(rect.left);
    let height = rect.bottom.saturating_sub(rect.top);
    if width <= 0 || height <= 0 {
        return Err("PRESENCE_NATIVE_FRAME_INVALID".to_owned());
    }
    Ok(PhysicalFrame {
        x: rect.left,
        y: rect.top,
        width: width as u32,
        height: height as u32,
    })
}

#[cfg(target_os = "windows")]
fn move_presence_input_window(
    _window: &WebviewWindow,
    handle: PresenceNativeHandle,
    frame: PhysicalFrame,
) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, SWP_NOACTIVATE, SWP_NOCOPYBITS, SWP_NOOWNERZORDER, SWP_NOSIZE, SWP_NOZORDER,
    };

    let _dpi_scope = PerMonitorDpiScope::enter();
    let current = native_presence_window_frame(handle)?;
    let mut flags = SWP_NOACTIVATE | SWP_NOCOPYBITS | SWP_NOOWNERZORDER | SWP_NOZORDER;
    if current.width == frame.width && current.height == frame.height {
        flags |= SWP_NOSIZE;
    }
    if unsafe {
        SetWindowPos(
            native_presence_hwnd(handle),
            std::ptr::null_mut(),
            frame.x,
            frame.y,
            i32::try_from(frame.width).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?,
            i32::try_from(frame.height).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?,
            flags,
        )
    } == 0
    {
        return Err("PRESENCE_INPUT_MOVE_FAILED".to_owned());
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn presence_window_frame(
    _window: &WebviewWindow,
    handle: PresenceNativeHandle,
) -> Result<PhysicalFrame, String> {
    native_presence_window_frame(handle)
}

#[cfg(not(target_os = "windows"))]
fn presence_window_frame(
    window: &WebviewWindow,
    _handle: PresenceNativeHandle,
) -> Result<PhysicalFrame, String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    Ok(PhysicalFrame {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    })
}

#[cfg(not(target_os = "windows"))]
fn set_presence_window_frame(
    window: &WebviewWindow,
    _handle: PresenceNativeHandle,
    frame: PhysicalFrame,
) -> Result<(), String> {
    window
        .set_size(tauri::PhysicalSize::new(frame.width, frame.height))
        .map_err(|error| error.to_string())?;
    window
        .set_position(tauri::PhysicalPosition::new(frame.x, frame.y))
        .map_err(|error| error.to_string())
}

#[cfg(not(target_os = "windows"))]
fn move_presence_input_window(
    window: &WebviewWindow,
    handle: PresenceNativeHandle,
    frame: PhysicalFrame,
) -> Result<(), String> {
    set_presence_window_frame(window, handle, frame)
}

#[cfg(target_os = "windows")]
fn move_presence_window_positions(
    render: &WebviewWindow,
    render_handle: PresenceNativeHandle,
    render_frame: PhysicalFrame,
    input: Option<(&WebviewWindow, PresenceNativeHandle, PhysicalFrame)>,
) -> Result<(), String> {
    let native_surface_handle = render
        .app_handle()
        .try_state::<DesktopState>()
        .and_then(|state| state.native_gpu.surface_handle());
    move_native_presence_window_positions(
        render_handle,
        render_frame,
        native_surface_handle,
        input.map(|(_window, handle, frame)| (handle, frame)),
    )
}

#[cfg(target_os = "windows")]
fn move_native_presence_window_positions(
    render_handle: PresenceNativeHandle,
    render_frame: PhysicalFrame,
    native_surface_handle: Option<PresenceNativeHandle>,
    input: Option<(PresenceNativeHandle, PhysicalFrame)>,
) -> Result<(), String> {
    let result = move_native_presence_window_positions_once(
        render_handle,
        render_frame,
        native_surface_handle,
        input,
    );
    if result.is_err() && native_surface_handle.is_some() {
        // The capture thread can invalidate its DirectComposition HWND between validation and
        // EndDeferWindowPos. Retry the authoritative Tauri pair without that optional surface;
        // the renderer lifecycle will independently rebuild the native layer.
        return move_native_presence_window_positions_once(
            render_handle,
            render_frame,
            None,
            input,
        );
    }
    result
}

#[cfg(target_os = "windows")]
fn move_native_presence_window_positions_once(
    render_handle: PresenceNativeHandle,
    render_frame: PhysicalFrame,
    native_surface_handle: Option<PresenceNativeHandle>,
    input: Option<(PresenceNativeHandle, PhysicalFrame)>,
) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        BeginDeferWindowPos, DeferWindowPos, EndDeferWindowPos, SWP_NOACTIVATE, SWP_NOCOPYBITS,
        SWP_NOOWNERZORDER, SWP_NOSIZE, SWP_NOZORDER,
    };

    let _dpi_scope = PerMonitorDpiScope::enter();
    let render_hwnd = native_presence_hwnd(render_handle);
    let render_current = native_presence_window_frame(render_handle)?;
    let render_flags = SWP_NOACTIVATE
        | SWP_NOCOPYBITS
        | if input.is_some() {
            SWP_NOOWNERZORDER
        } else {
            SWP_NOZORDER
        }
        | if render_current.width == render_frame.width
            && render_current.height == render_frame.height
        {
            SWP_NOSIZE
        } else {
            0
        };
    let input = input
        .map(|(handle, frame)| {
            let current = native_presence_window_frame(handle)?;
            let flags = SWP_NOACTIVATE
                | SWP_NOCOPYBITS
                | SWP_NOZORDER
                | if current.width == frame.width && current.height == frame.height {
                    SWP_NOSIZE
                } else {
                    0
                };
            Ok::<_, String>((native_presence_hwnd(handle), frame, flags))
        })
        .transpose()?;
    // The DirectComposition surface is created on the capture thread and can disappear while a
    // monitor, DPI, or renderer recovery transition is in flight. A stale surface must never make
    // the Tauri render/input pair immovable; the renderer supervisor will rebuild it separately.
    let native_surface = native_surface_handle.and_then(|handle| {
        let current = native_presence_window_frame(handle).ok()?;
        let flags = SWP_NOACTIVATE
            | SWP_NOCOPYBITS
            | if input.is_some() {
                SWP_NOOWNERZORDER
            } else {
                SWP_NOZORDER
            }
            | if current.width == render_frame.width && current.height == render_frame.height {
                SWP_NOSIZE
            } else {
                0
            };
        Some((native_presence_hwnd(handle), flags))
    });
    let render_width =
        i32::try_from(render_frame.width).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?;
    let render_height =
        i32::try_from(render_frame.height).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?;
    let input = input
        .map(|(handle, frame, flags)| {
            let width =
                i32::try_from(frame.width).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?;
            let height =
                i32::try_from(frame.height).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?;
            Ok::<_, String>((handle, frame, flags, width, height))
        })
        .transpose()?;
    let count = 1 + i32::from(native_surface.is_some()) + i32::from(input.is_some());
    let mut batch = unsafe { BeginDeferWindowPos(count) };
    if batch.is_null() {
        return Err("PRESENCE_GROUP_MOVE_UNAVAILABLE".to_owned());
    }
    batch = unsafe {
        DeferWindowPos(
            batch,
            render_hwnd,
            native_surface.as_ref().map_or_else(
                || {
                    input
                        .as_ref()
                        .map_or(std::ptr::null_mut(), |(handle, ..)| *handle)
                },
                |(handle, _)| *handle,
            ),
            render_frame.x,
            render_frame.y,
            render_width,
            render_height,
            render_flags,
        )
    };
    if batch.is_null() {
        return Err("PRESENCE_GROUP_MOVE_FAILED".to_owned());
    }
    if let Some((native_surface_hwnd, flags)) = native_surface {
        batch = unsafe {
            DeferWindowPos(
                batch,
                native_surface_hwnd,
                input
                    .as_ref()
                    .map_or(std::ptr::null_mut(), |(handle, ..)| *handle),
                render_frame.x,
                render_frame.y,
                render_width,
                render_height,
                flags,
            )
        };
        if batch.is_null() {
            return Err("PRESENCE_GROUP_MOVE_FAILED".to_owned());
        }
    }
    if let Some((input_hwnd, frame, flags, width, height)) = input {
        batch = unsafe {
            DeferWindowPos(
                batch,
                input_hwnd,
                std::ptr::null_mut(),
                frame.x,
                frame.y,
                width,
                height,
                flags,
            )
        };
        if batch.is_null() {
            return Err("PRESENCE_GROUP_MOVE_FAILED".to_owned());
        }
    }
    if unsafe { EndDeferWindowPos(batch) } == 0 {
        return Err("PRESENCE_GROUP_MOVE_FAILED".to_owned());
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn move_presence_window_positions(
    render: &WebviewWindow,
    render_handle: PresenceNativeHandle,
    render_frame: PhysicalFrame,
    input: Option<(&WebviewWindow, PresenceNativeHandle, PhysicalFrame)>,
) -> Result<(), String> {
    set_presence_window_frame(render, render_handle, render_frame)?;
    if let Some((input, input_handle, frame)) = input {
        set_presence_window_frame(input, input_handle, frame)?;
    }
    Ok(())
}

pub(crate) fn synchronize_presence_window_frames(
    app: &tauri::AppHandle,
    render_frame: PhysicalFrame,
    input_frame: Option<PhysicalFrame>,
) -> Result<(), String> {
    let state = app
        .try_state::<DesktopState>()
        .ok_or_else(|| "Desktop state is unavailable".to_owned())?;
    let native_windows = presence_native_windows(&state)?;
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    let render_handle = native_windows.handle_for(PET_RENDER_LABEL)?;
    let input = input_frame
        .zip(app.get_webview_window(PET_INPUT_LABEL))
        .map(|(frame, window)| {
            native_windows
                .handle_for(PET_INPUT_LABEL)
                .map(|handle| (window, handle, frame))
        })
        .transpose()?;
    move_presence_window_positions(
        &render,
        render_handle,
        render_frame,
        input
            .as_ref()
            .map(|(window, handle, frame)| (window, *handle, *frame)),
    )
}

fn build_fairy_tray(
    app: &tauri::App,
    preferences: &DesktopPreferences,
) -> Result<FairyTrayState, tauri::Error> {
    let ask = MenuItem::with_id(app, TRAY_ASK_ID, "Ask Fairy", true, None::<&str>)?;
    let new_chat = MenuItem::with_id(app, TRAY_NEW_CHAT_ID, "New chat", true, None::<&str>)?;
    let auto_play = CheckMenuItem::with_id(
        app,
        TRAY_AUTO_PLAY_ID,
        "Auto-play replies",
        true,
        preferences.voice_auto_play_pet,
        None::<&str>,
    )?;
    let muted = CheckMenuItem::with_id(
        app,
        TRAY_MUTED_ID,
        "Mute",
        true,
        preferences.pet_muted,
        None::<&str>,
    )?;
    let always_on_top = CheckMenuItem::with_id(
        app,
        TRAY_ALWAYS_ON_TOP_ID,
        "Always on top",
        true,
        preferences.pet_always_on_top,
        None::<&str>,
    )?;
    let open = MenuItem::with_id(app, TRAY_OPEN_ID, "Open Fairy", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, TRAY_SETTINGS_ID, "Settings", true, None::<&str>)?;
    let reset = MenuItem::with_id(app, TRAY_RESET_ID, "Reset position", true, None::<&str>)?;
    let exit = MenuItem::with_id(app, TRAY_EXIT_ID, "Exit Fairy", true, None::<&str>)?;
    let menu = MenuBuilder::new(app)
        .item(&ask)
        .item(&new_chat)
        .separator()
        .item(&auto_play)
        .item(&muted)
        .item(&always_on_top)
        .separator()
        .item(&open)
        .item(&settings)
        .item(&reset)
        .separator()
        .item(&exit)
        .build()?;
    let mut builder = TrayIconBuilder::with_id("fairy")
        .menu(&menu)
        .tooltip("Fairy")
        .on_menu_event(handle_tray_menu_event);
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
    }
    let tray = builder.build(app)?;
    Ok(FairyTrayState {
        _tray: tray,
        auto_play,
        muted,
        always_on_top,
    })
}

fn handle_tray_menu_event(app: &tauri::AppHandle, event: tauri::menu::MenuEvent) {
    let Some(action) = fairy_tray_action(event.id().as_ref()) else {
        return;
    };
    match action {
        FairyTrayAction::Ask => {
            if pet_session_disabled(app)
                || app.get_webview_window(PET_INPUT_LABEL).is_none()
                || app
                    .emit_to(PET_INPUT_LABEL, PRESENCE_INPUT_REQUESTED_EVENT, ())
                    .is_err()
            {
                let _ = main_window(app).and_then(|window| show_and_focus(&window));
            }
        }
        FairyTrayAction::NewChat => {
            if pet_session_disabled(app)
                || app.get_webview_window(PET_INPUT_LABEL).is_none()
                || app
                    .emit_to(PET_INPUT_LABEL, "presence-new-chat-requested", ())
                    .is_err()
            {
                let _ = main_window(app).and_then(|window| show_and_focus(&window));
            }
        }
        FairyTrayAction::ToggleAutoPlay
        | FairyTrayAction::ToggleMuted
        | FairyTrayAction::ToggleAlwaysOnTop => {
            let _ = toggle_pet_preference_from_tray(app, action);
        }
        FairyTrayAction::Open => {
            let _ = main_window(app).and_then(|window| show_and_focus(&window));
        }
        FairyTrayAction::Settings => {
            let _ = settings_window(app).and_then(|window| show_and_focus(&window));
        }
        FairyTrayAction::Reset => {
            let _ = reset_pet_position_from_tray(app);
        }
        FairyTrayAction::Exit => app.exit(0),
    }
}

fn toggle_pet_preference_from_tray(
    app: &tauri::AppHandle,
    action: FairyTrayAction,
) -> Result<DesktopPreferences, String> {
    let state = app.state::<DesktopState>();
    let current = DesktopPreferencesStore::new(&state.data_dir)
        .load()
        .map_err(|error| error.to_string())?;
    let update = PetPreferencesUpdate {
        expected_revision: current.revision,
        voice_auto_play_pet: (action == FairyTrayAction::ToggleAutoPlay)
            .then_some(!current.voice_auto_play_pet),
        pet_muted: (action == FairyTrayAction::ToggleMuted).then_some(!current.pet_muted),
        pet_always_on_top: (action == FairyTrayAction::ToggleAlwaysOnTop)
            .then_some(!current.pet_always_on_top),
        pet_anchor: None,
        clear_pet_anchor: false,
    };
    persist_pet_preferences(app, state.inner(), update)
}

fn reset_pet_position_from_tray(app: &tauri::AppHandle) -> Result<DesktopPreferences, String> {
    let state = app.state::<DesktopState>();
    let current = DesktopPreferencesStore::new(&state.data_dir)
        .load()
        .map_err(|error| error.to_string())?;
    let next = persist_pet_preferences(
        app,
        state.inner(),
        PetPreferencesUpdate {
            expected_revision: current.revision,
            voice_auto_play_pet: None,
            pet_muted: None,
            pet_always_on_top: None,
            pet_anchor: None,
            clear_pet_anchor: true,
        },
    )?;
    let placement = place_pet_windows(app, &next, Some(presence_native_windows(&state)?))?;
    set_presence_placement(&state, placement);
    Ok(next)
}

fn sync_tray_preferences(app: &tauri::AppHandle, preferences: &DesktopPreferences) {
    let Some(tray) = app.try_state::<FairyTrayState>() else {
        return;
    };
    let _ = tray.auto_play.set_checked(preferences.voice_auto_play_pet);
    let _ = tray.muted.set_checked(preferences.pet_muted);
    let _ = tray
        .always_on_top
        .set_checked(preferences.pet_always_on_top);
}

#[tauri::command]
async fn open_settings_window(window: WebviewWindow) -> Result<(), String> {
    if !["main", PET_INPUT_LABEL, "settings"].contains(&window.label()) {
        return Err("Window is not authorized".to_owned());
    }
    show_and_focus(&settings_window(window.app_handle())?)
}

#[tauri::command]
async fn pet_render_settings_get(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<PetRenderSettings, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    DesktopPreferencesStore::new(&state.data_dir)
        .load()
        .map(|preferences| PetRenderSettings::from(&preferences))
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn pet_native_gpu_start(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    request: NativeGpuStartRequest,
) -> Result<NativeGpuStatus, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    if !state.presence_startup.ready() {
        return Err("PRESENCE_NATIVE_GPU_NOT_READY".to_owned());
    }
    let config = native_gpu_config(&window, state.inner(), request)?;
    // Keep the compatibility tracking WebView hidden while native DirectComposition owns the
    // visible surface. The HWND remains the authoritative placement target for the render worker.
    window.hide().map_err(|error| error.to_string())?;
    let manager = Arc::clone(&state.native_gpu);
    let starting_manager = Arc::clone(&manager);
    let start_result =
        tauri::async_runtime::spawn_blocking(move || match starting_manager.start(config) {
            Err(presence_native_gpu::NativeGpuError::AlreadyRunning)
                if starting_manager.status().target_frame_rate == config.target_frame_rate =>
            {
                starting_manager.update(config.presentation)
            }
            result => result,
        })
        .await
        .map_err(|_| "PRESENCE_NATIVE_GPU_WORKER_INTERRUPTED".to_owned())?;
    let status = match start_result {
        Ok(status) => status,
        Err(error) => {
            let failed = manager.status();
            eprintln!(
                "[presence-native-gpu] start failed: error={error}; stage={}; hresult={}; target=({},{} {}x{}); monitor=({},{} {}x{}); monitor_handle={}; device={}; adapter={}; output={}",
                failed.composition_stage,
                failed.composition_hresult.as_deref().unwrap_or("none"),
                failed.target_x,
                failed.target_y,
                failed.surface_width,
                failed.surface_height,
                failed.monitor_x,
                failed.monitor_y,
                failed.monitor_width,
                failed.monitor_height,
                failed.monitor_handle.as_deref().unwrap_or("none"),
                failed.monitor_device_name.as_deref().unwrap_or("none"),
                failed.adapter_name.as_deref().unwrap_or("none"),
                failed.output_device_name.as_deref().unwrap_or("none"),
            );
            if !state.presence.is_repositioning() {
                let _ = show_presence_tracking_window(window.app_handle());
            }
            return Err(error.to_string());
        }
    };
    if let Err(error) = align_pet_input_to_native_presentation(window.app_handle(), state.inner()) {
        let stopping_manager = Arc::clone(&manager);
        let _ = tauri::async_runtime::spawn_blocking(move || stopping_manager.stop()).await;
        if !state.presence.is_repositioning() {
            let _ = show_presence_tracking_window(window.app_handle());
        }
        return Err(format!(
            "PRESENCE_NATIVE_GPU_INPUT_ALIGNMENT_FAILED: {error}"
        ));
    }
    Ok(status)
}

fn native_gpu_config(
    window: &WebviewWindow,
    state: &DesktopState,
    request: NativeGpuStartRequest,
) -> Result<NativeGpuConfig, String> {
    let preferences = DesktopPreferencesStore::new(&state.data_dir)
        .load()
        .map_err(|_| "PRESENCE_NATIVE_GPU_PREFERENCES_UNAVAILABLE".to_owned())?;
    if preferences.pet_optics_mode != desktop_preferences::PetOpticsMode::Enhanced {
        return Err("PRESENCE_NATIVE_GPU_PRIVACY_MODE".to_owned());
    }
    let native_windows = presence_native_windows(state)?;
    let render_hwnd = native_gpu_render_handle(native_windows)?;
    let input_hwnd = native_gpu_input_handle(native_windows)?;
    let render_frame = presence_window_frame(window, render_hwnd)?;
    Ok(NativeGpuConfig {
        render_hwnd,
        input_hwnd,
        render_frame,
        always_on_top: preferences.pet_always_on_top,
        target_frame_rate: request.target_frame_rate,
        presentation: request.presentation,
    })
}

#[tauri::command]
async fn pet_native_gpu_rebind(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    request: NativeGpuStartRequest,
) -> Result<NativeGpuStatus, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    if !state.presence_startup.ready() {
        return Err("PRESENCE_NATIVE_GPU_NOT_READY".to_owned());
    }
    let config = native_gpu_config(&window, state.inner(), request)?;
    let manager = Arc::clone(&state.native_gpu);
    tauri::async_runtime::spawn_blocking(move || manager.rebind(config))
        .await
        .map_err(|_| "PRESENCE_NATIVE_GPU_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn pet_native_gpu_status(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<NativeGpuStatus, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    Ok(state.native_gpu.status())
}

#[tauri::command]
async fn pet_native_gpu_update(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    request: NativeGpuPresentation,
) -> Result<NativeGpuStatus, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let manager = Arc::clone(&state.native_gpu);
    let status = tauri::async_runtime::spawn_blocking(move || manager.update(request))
        .await
        .map_err(|_| "PRESENCE_NATIVE_GPU_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.to_string())?;
    align_pet_input_to_native_presentation(window.app_handle(), state.inner())?;
    Ok(status)
}

#[tauri::command]
async fn pet_native_gpu_prepare_visual_test(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<NativeGpuStatus, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    if !cfg!(debug_assertions) {
        return Err("PRESENCE_NATIVE_GPU_VISUAL_TEST_UNAVAILABLE".to_owned());
    }
    let manager = Arc::clone(&state.native_gpu);
    tauri::async_runtime::spawn_blocking(move || manager.prepare_visual_test())
        .await
        .map_err(|_| "PRESENCE_NATIVE_GPU_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn pet_native_gpu_stop(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<NativeGpuStatus, String> {
    authorize_pet_render_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let manager = Arc::clone(&state.native_gpu);
    let status = tauri::async_runtime::spawn_blocking(move || manager.stop())
        .await
        .map_err(|_| "PRESENCE_NATIVE_GPU_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.to_string())?;
    if state.presence_startup.ready() && !state.presence.is_repositioning() {
        show_presence_tracking_window(window.app_handle())?;
    } else {
        // A monitor transition stops and recreates the capture source. Showing the transparent
        // tracking WebView during that handoff exposes a white titled rectangle on some WebView2
        // builds, so keep it hidden until the native renderer has resumed or drag has ended.
        window.hide().map_err(|error| error.to_string())?;
    }
    Ok(status)
}

fn deserialize_core_result<T: serde::de::DeserializeOwned>(response: Value) -> Result<T, String> {
    if let Some(result) = response.get("result") {
        return serde_json::from_value(result.clone())
            .map_err(|_| "CORE_PROTOCOL_ERROR".to_owned());
    }
    Err(response
        .pointer("/error/data/error_code")
        .and_then(Value::as_str)
        .unwrap_or("CORE_PROTOCOL_ERROR")
        .to_owned())
}

#[tauri::command]
async fn voice_worker_health(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<Value, String> {
    authorize_voice_health_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let voice = Arc::clone(&state.voice);
    tauri::async_runtime::spawn_blocking(move || voice.status())
        .await
        .map_err(|_| "VOICE_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.public_code().to_owned())
}

#[tauri::command]
async fn voice_model_install(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<Value, String> {
    authorize_voice_settings_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let voice = Arc::clone(&state.voice);
    tauri::async_runtime::spawn_blocking(move || voice.install_model())
        .await
        .map_err(|_| "VOICE_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.public_code().to_owned())
}

#[tauri::command]
async fn voice_session_start(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: VoiceStreamInput,
    audio: Channel<Response>,
    events: Channel<VoiceStreamEvent>,
) -> Result<PreparedVoiceSession, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let response = call_core(
        &state,
        json!({
            "jsonrpc": "2.0",
            "id": -10_001,
            "method": "voice.sessions.start",
            "params": input,
        }),
    )
    .await;
    let session: PreparedVoiceSession = deserialize_core_result(response)?;
    let worker_session = session.clone();
    let voice = Arc::clone(&state.voice);
    let failure_events = events.clone();
    tauri::async_runtime::spawn(async move {
        let failed_session_id = worker_session.id.clone();
        let result = tauri::async_runtime::spawn_blocking(move || {
            voice.stream(worker_session, audio, events)
        })
        .await;
        let error_code = match result {
            Ok(Ok(())) => return,
            Ok(Err(error)) => error.public_code().to_owned(),
            Err(_) => "VOICE_WORKER_INTERRUPTED".to_owned(),
        };
        let _ = failure_events.send(VoiceStreamEvent::Failed {
            session_id: failed_session_id,
            error_code,
            message: "Fairy voice playback could not start.".to_owned(),
        });
    });
    Ok(session)
}

#[tauri::command]
async fn voice_session_cancel(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    session_id: String,
) -> Result<PreparedVoiceSession, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let voice = Arc::clone(&state.voice);
    let worker_session_id = session_id.clone();
    let worker_result =
        tauri::async_runtime::spawn_blocking(move || voice.cancel(&worker_session_id))
            .await
            .map_err(|_| "VOICE_WORKER_INTERRUPTED".to_owned())?;
    let response = call_core(
        &state,
        json!({
            "jsonrpc": "2.0",
            "id": -10_002,
            "method": "voice.sessions.cancel",
            "params": { "session_id": session_id },
        }),
    )
    .await;
    let session = deserialize_core_result(response)?;
    worker_result.map_err(|error| error.public_code().to_owned())?;
    Ok(session)
}

#[tauri::command]
async fn voice_test_start(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    audio: Channel<Response>,
    events: Channel<VoiceStreamEvent>,
) -> Result<PreparedVoiceSession, String> {
    authorize_voice_settings_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let session = prepared_test_session().map_err(|error| error.public_code().to_owned())?;
    let worker_session = session.clone();
    let failed_session_id = session.id.clone();
    let voice = Arc::clone(&state.voice);
    let failure_events = events.clone();
    tauri::async_runtime::spawn(async move {
        let result = tauri::async_runtime::spawn_blocking(move || {
            voice.stream(worker_session, audio, events)
        })
        .await;
        let error_code = match result {
            Ok(Ok(())) => return,
            Ok(Err(error)) => error.public_code().to_owned(),
            Err(_) => "VOICE_WORKER_INTERRUPTED".to_owned(),
        };
        let _ = failure_events.send(VoiceStreamEvent::Failed {
            session_id: failed_session_id,
            error_code,
            message: "Fairy voice test could not start.".to_owned(),
        });
    });
    Ok(session)
}

#[tauri::command]
async fn voice_test_cancel(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    session_id: String,
) -> Result<(), String> {
    authorize_voice_settings_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let voice = Arc::clone(&state.voice);
    tauri::async_runtime::spawn_blocking(move || voice.cancel(&session_id))
        .await
        .map_err(|_| "VOICE_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.public_code().to_owned())
}

#[tauri::command]
async fn realtime_voice_start(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    text: String,
    audio: Channel<Response>,
    events: Channel<VoiceStreamEvent>,
) -> Result<PreparedVoiceSession, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let session =
        prepared_realtime_session(&text).map_err(|error| error.public_code().to_owned())?;
    let worker_session = session.clone();
    let failed_session_id = session.id.clone();
    let voice = Arc::clone(&state.voice);
    let failure_events = events.clone();
    tauri::async_runtime::spawn(async move {
        let result = tauri::async_runtime::spawn_blocking(move || {
            voice.stream(worker_session, audio, events)
        })
        .await;
        let error_code = match result {
            Ok(Ok(())) => return,
            Ok(Err(error)) => error.public_code().to_owned(),
            Err(_) => "VOICE_WORKER_INTERRUPTED".to_owned(),
        };
        let _ = failure_events.send(VoiceStreamEvent::Failed {
            session_id: failed_session_id,
            error_code,
            message: "Realtime Fairy voice playback could not start.".to_owned(),
        });
    });
    Ok(session)
}

#[tauri::command]
async fn realtime_voice_cancel(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    session_id: String,
) -> Result<(), String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let voice = Arc::clone(&state.voice);
    tauri::async_runtime::spawn_blocking(move || voice.cancel(&session_id))
        .await
        .map_err(|_| "VOICE_WORKER_INTERRUPTED".to_owned())?
        .map_err(|error| error.public_code().to_owned())
}

#[tauri::command]
async fn select_project_folder(
    window: WebviewWindow,
    app: tauri::AppHandle,
) -> Result<Option<String>, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("Import a project folder")
            .blocking_pick_folder()
            .map(|selected| match selected {
                FilePath::Path(path) => path.to_string_lossy().into_owned(),
                FilePath::Url(url) => url.to_string(),
            })
    })
    .await
    .map_err(|error| error.to_string())
}

#[tauri::command]
async fn select_obsidian_vault(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
) -> Result<Option<ObsidianVaultSelection>, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let registry_path = state.data_dir.join("obsidian-paths.json");
    tauri::async_runtime::spawn_blocking(move || {
        let selected = app
            .dialog()
            .file()
            .set_title("Select an Obsidian Vault")
            .blocking_pick_folder();
        let Some(FilePath::Path(path)) = selected else {
            return Ok(None);
        };
        ObsidianPathRegistry::new(registry_path)
            .register(&path)
            .map(Some)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn select_skill_source(
    window: WebviewWindow,
    app: tauri::AppHandle,
    source_kind: String,
) -> Result<Option<String>, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    tauri::async_runtime::spawn_blocking(move || {
        let dialog = app.dialog().file().set_title("Add an external Fairy Skill");
        let selected = match source_kind.as_str() {
            "folder" => dialog.blocking_pick_folder(),
            "zip" => dialog
                .add_filter("Fairy Skill archive", &["zip"])
                .blocking_pick_file(),
            _ => return Err("SKILL_SOURCE_KIND_UNSUPPORTED".to_owned()),
        };
        Ok(selected.map(|value| match value {
            FilePath::Path(path) => path.to_string_lossy().into_owned(),
            FilePath::Url(url) => url.to_string(),
        }))
    })
    .await
    .map_err(|error| error.to_string())?
}

fn development_core_root() -> PathBuf {
    env::var_os("FAIRY_CORE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core"))
}

pub fn bundled_core_path(desktop_executable: &std::path::Path) -> PathBuf {
    desktop_executable
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join(if cfg!(windows) {
            "fairy-core.exe"
        } else {
            "fairy-core"
        })
}

pub fn bundled_git_path(resource_dir: &std::path::Path) -> PathBuf {
    resource_dir.join("runtime/git/cmd/git.exe")
}

fn core_launch_spec(data_dir: &std::path::Path) -> Result<CoreLaunchSpec, std::io::Error> {
    if cfg!(debug_assertions) {
        return Ok(CoreLaunchSpec::development(
            development_core_root(),
            data_dir,
        ));
    }
    let executable = std::env::current_exe()?;
    let core_program = env::var_os("FAIRY_CORE_PROGRAM")
        .map(PathBuf::from)
        .unwrap_or_else(|| bundled_core_path(&executable));
    Ok(CoreLaunchSpec::bundled(core_program, data_dir))
}

fn configured_core_launch(
    data_dir: &Path,
    desktop_program: &Path,
    resource_dir: &Path,
) -> Result<CoreLaunchSpec, Box<dyn std::error::Error>> {
    let mut launch = core_launch_spec(data_dir)?;
    launch.env.insert(
        "FAIRY_LOCAL_WORKER_PROGRAM".to_owned(),
        desktop_program.to_string_lossy().into_owned(),
    );
    launch.env.insert(
        "FAIRY_LOCAL_WORKER_ARGS_JSON".to_owned(),
        "[\"--local-worker\"]".to_owned(),
    );
    if !cfg!(debug_assertions) {
        launch.env.insert(
            "FAIRY_GIT_PROGRAM".to_owned(),
            bundled_git_path(resource_dir)
                .to_string_lossy()
                .into_owned(),
        );
    }
    let configuration = ProviderConfigurationStore::new(data_dir).load_openrouter()?;
    let secret = match ProviderCredentialStore::new(data_dir).load_openrouter() {
        Ok(secret) => secret,
        Err(error) => {
            eprintln!(
                "OpenRouter credential is unavailable; starting Fairy without provider access: {error}"
            );
            None
        }
    };
    if let (Some(configuration), Some(secret)) = (configuration, secret) {
        launch.env.insert(
            "FAIRY_PROVIDER_PROFILES_JSON".to_owned(),
            openrouter_profiles_json(&configuration),
        );
        launch.env.insert(
            "FAIRY_PROVIDER_SECRET_REFS_JSON".to_owned(),
            "{\"openrouter\":\"FAIRY_PROVIDER_SECRET_OPENROUTER\"}".to_owned(),
        );
        launch
            .env
            .insert("FAIRY_PROVIDER_SECRET_OPENROUTER".to_owned(), secret);
    }
    Ok(launch)
}

fn restart_core(state: &DesktopState) -> Result<(), Box<dyn std::error::Error>> {
    let mut core = state
        .core
        .lock()
        .map_err(|_| CoreBridgeError::LockPoisoned)?;
    *core = None;
    let launch =
        configured_core_launch(&state.data_dir, &state.desktop_program, &state.resource_dir)?;
    *core = Some(CoreBridge::spawn_verified(launch)?);
    Ok(())
}

fn import_legacy_memory_settings(
    bridge: &CoreBridge,
    legacy: Option<LegacyMemorySettings>,
) -> Result<(), String> {
    let Some(legacy) = legacy else {
        return Ok(());
    };
    let current = bridge
        .call(json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "memory.settings.get",
            "params": {}
        }))
        .map_err(|error| error.to_string())?;
    let current = json_rpc_result(&current)?;
    let revision = current
        .get("revision")
        .and_then(Value::as_u64)
        .ok_or_else(|| "Core Memory Settings revision is missing".to_owned())?;
    if revision != 0 {
        return Ok(());
    }
    let updated = bridge
        .call(json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "memory.settings.update",
            "params": {
                "enabled": legacy.enabled,
                "retention_days": legacy.retention_days,
                "export_to_obsidian": false,
                "sync_normalized_content": false,
                "expected_revision": 0,
                "idempotency_key": "desktop-memory-settings-migration:v7"
            }
        }))
        .map_err(|error| error.to_string())?;
    json_rpc_result(&updated)?;
    Ok(())
}

fn json_rpc_result(response: &Value) -> Result<&Value, String> {
    if let Some(error) = response.get("error") {
        return Err(format!("Core request failed: {error}"));
    }
    response
        .get("result")
        .ok_or_else(|| "Core response result is missing".to_owned())
}

pub fn resolve_desktop_data_dir(
    default_dir: PathBuf,
    override_dir: Option<OsString>,
) -> Result<PathBuf, std::io::Error> {
    let Some(override_dir) = override_dir else {
        return Ok(default_dir);
    };
    let override_dir = PathBuf::from(override_dir);
    if !override_dir.is_absolute() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "FAIRY_DESKTOP_DATA_DIR must be an absolute path",
        ));
    }
    Ok(override_dir)
}

fn configured_desktop_data_dir(
    app: &tauri::AppHandle,
) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let default_dir = app.path().app_data_dir()?;
    Ok(resolve_desktop_data_dir(
        default_dir,
        env::var_os("FAIRY_DESKTOP_DATA_DIR"),
    )?)
}

fn reconcile_pet_window_placement(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app
        .try_state::<DesktopState>()
        .ok_or_else(|| "Desktop state is unavailable".to_owned())?;
    let data_dir = state.data_dir.clone();
    let preferences = DesktopPreferencesStore::new(&data_dir)
        .load()
        .map_err(|error| error.to_string())?;
    let placement = place_pet_windows(app, &preferences, Some(presence_native_windows(&state)?))?;
    set_presence_placement(&state, placement);
    state
        .pet_placement_reconciled
        .store(true, Ordering::Release);
    Ok(())
}

fn initialize_presence_after_main_load(app: tauri::AppHandle) {
    tauri::async_runtime::spawn_blocking(move || {
        let result = initialize_presence(&app);
        let Some(state) = app.try_state::<DesktopState>() else {
            return;
        };
        match result {
            Ok(()) => {
                state.presence_startup.succeeded();
                if let Ok(preferences) = DesktopPreferencesStore::new(&state.data_dir).load() {
                    if let Err(error) = apply_pet_window_preferences(&app, &preferences) {
                        eprintln!("failed to reveal initialized Fairy Presence: {error}");
                    }
                }
            }
            Err(error) => {
                eprintln!("failed to initialize Fairy Presence: {error}");
                if let Some(delay) = state.presence_startup.failed() {
                    let retry_app = app.clone();
                    tauri::async_runtime::spawn_blocking(move || {
                        std::thread::sleep(delay);
                        request_presence_initialization(retry_app);
                    });
                }
            }
        }
    });
}

fn initialize_presence(app: &tauri::AppHandle) -> Result<(), String> {
    let native_windows = create_presence_windows(app).map_err(|error| error.to_string())?;
    let state = app
        .try_state::<DesktopState>()
        .ok_or_else(|| "desktop state is unavailable".to_owned())?;
    let mut stored = state
        .presence_windows
        .lock()
        .map_err(|_| "presence window state is unavailable".to_owned())?;
    *stored = Some(native_windows);
    drop(stored);
    reconcile_pet_window_placement(app)?;
    allow_presence_windows_in_capture(native_windows)?;
    let preferences = DesktopPreferencesStore::new(&state.data_dir)
        .load()
        .map_err(|error| error.to_string())?;
    apply_pet_window_preferences(app, &preferences)?;
    state
        .presence
        .launch(app.clone())
        .map_err(|error| error.to_string())
}

fn request_presence_initialization(app: tauri::AppHandle) {
    let Some(state) = app.try_state::<DesktopState>() else {
        return;
    };
    if state.presence_startup.try_begin() {
        initialize_presence_after_main_load(app);
    }
}

fn create_presence_windows(
    app: &tauri::AppHandle,
) -> Result<PresenceNativeWindows, Box<dyn std::error::Error>> {
    #[cfg(target_os = "windows")]
    wait_native_presence_handle("Fairy", Duration::from_secs(20))?;

    let [render_spec, input_spec] = presence_window_creation_specs();
    let (_, render) = ensure_presence_window(app, render_spec.label, Duration::from_secs(12))?;
    let (_, input) = ensure_presence_window(app, input_spec.label, Duration::from_secs(12))?;
    Ok(PresenceNativeWindows { render, input })
}

fn ensure_presence_window(
    app: &tauri::AppHandle,
    label: &str,
    timeout: Duration,
) -> Result<(WebviewWindow, PresenceNativeHandle), Box<dyn std::error::Error>> {
    let window = create_presence_window_on_main_thread(app, label, timeout)?;
    let policy = auxiliary_window_policy(label)
        .ok_or_else(|| std::io::Error::other("Missing pet window policy"))?;

    #[cfg(target_os = "windows")]
    let handle = {
        let handle = window.hwnd()?.0 as isize;
        if handle == 0 {
            return Err(std::io::Error::other(format!(
                "Native window handle is unavailable: {label}"
            ))
            .into());
        }
        handle
    };
    #[cfg(not(target_os = "windows"))]
    let handle = {
        let _ = timeout;
    };

    window.set_ignore_cursor_events(policy.ignore_cursor_events)?;
    window.set_focusable(policy.focusable)?;
    #[cfg(target_os = "windows")]
    enforce_presence_window_shell_policy(handle, Some(policy.focusable))
        .map_err(std::io::Error::other)?;
    Ok((window, handle))
}

fn create_presence_window_on_main_thread(
    app: &tauri::AppHandle,
    label: &str,
    timeout: Duration,
) -> Result<WebviewWindow, Box<dyn std::error::Error>> {
    if let Some(window) = app.get_webview_window(label) {
        return Ok(window);
    }
    if !matches!(label, PET_RENDER_LABEL | PET_INPUT_LABEL) {
        return Err(
            std::io::Error::other(format!("Unsupported presence window label: {label}")).into(),
        );
    }

    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    let window_app = app.clone();
    let window_label = label.to_owned();
    app.run_on_main_thread(move || {
        let result = build_presence_window(&window_app, &window_label)
            .map(|_| ())
            .map_err(|error| error.to_string());
        let _ = sender.send(result);
    })?;
    receiver
        .recv_timeout(timeout)
        .map_err(|_| std::io::Error::other(format!("Timed out creating presence window: {label}")))?
        .map_err(std::io::Error::other)?;
    app.get_webview_window(label)
        .ok_or_else(|| {
            std::io::Error::other(format!("Presence window was not registered: {label}"))
        })
        .map_err(Into::into)
}

fn build_presence_window(
    app: &tauri::AppHandle,
    label: &str,
) -> Result<WebviewWindow, tauri::Error> {
    let spec = presence_window_creation_specs()
        .into_iter()
        .find(|spec| spec.label == label)
        .expect("presence label was validated before window creation");
    let builder = WebviewWindowBuilder::new(
        app,
        label,
        WebviewUrl::App(format!("index.html?surface={label}").into()),
    )
    .position(-32_000.0, -32_000.0)
    .resizable(false)
    .maximizable(false)
    .minimizable(false)
    .decorations(false)
    .transparent(true)
    .background_color(tauri::webview::Color(0, 0, 0, 0))
    .focused(false)
    .always_on_top(true)
    .skip_taskbar(true)
    .shadow(false)
    .visible(false)
    // Auxiliary surfaces are intentionally untitled. Their accessible names live in the DOM;
    // exposing an HWND title lets Windows/WebView2 paint it during focus and drag transitions.
    .title("")
    .inner_size(f64::from(spec.width), f64::from(spec.height))
    .min_inner_size(f64::from(spec.min_width), f64::from(spec.min_height))
    .max_inner_size(f64::from(spec.max_width), f64::from(spec.max_height));
    if spec.focusable {
        builder.build()
    } else {
        builder.focusable(false).build()
    }
}

#[cfg(target_os = "windows")]
fn wait_native_presence_handle(
    title: &str,
    timeout: Duration,
) -> Result<PresenceNativeHandle, Box<dyn std::error::Error>> {
    let started = Instant::now();
    loop {
        if let Some(handle) = native_presence_handle_for_title(title) {
            return Ok(handle);
        }
        if started.elapsed() >= timeout {
            return Err(
                std::io::Error::other(format!("Native window is unavailable: {title}")).into(),
            );
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

#[cfg(target_os = "windows")]
fn native_presence_handle_for_title(title: &str) -> Option<PresenceNativeHandle> {
    use windows_sys::core::BOOL;
    use windows_sys::Win32::Foundation::{HWND, LPARAM};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowTextW, GetWindowThreadProcessId,
    };

    struct NativeWindowSearch<'a> {
        process_id: u32,
        title: &'a str,
        handle: Option<PresenceNativeHandle>,
    }

    unsafe extern "system" fn find_window(window: HWND, context: LPARAM) -> BOOL {
        let search = unsafe { &mut *(context as *mut NativeWindowSearch<'_>) };
        let mut process_id = 0_u32;
        unsafe { GetWindowThreadProcessId(window, std::ptr::addr_of_mut!(process_id)) };
        if process_id != search.process_id {
            return 1;
        }
        let mut title = [0_u16; 128];
        let length = unsafe { GetWindowTextW(window, title.as_mut_ptr(), title.len() as i32) };
        if length > 0 && String::from_utf16_lossy(&title[..length as usize]) == search.title {
            search.handle = Some(window as isize);
            return 0;
        }
        1
    }

    let mut search = NativeWindowSearch {
        process_id: std::process::id(),
        title,
        handle: None,
    };
    unsafe {
        EnumWindows(Some(find_window), std::ptr::addr_of_mut!(search) as LPARAM);
    }
    search.handle
}

#[cfg(target_os = "windows")]
fn allow_presence_windows_in_capture(native_windows: PresenceNativeWindows) -> Result<(), String> {
    for handle in [native_windows.render, native_windows.input] {
        presence_backdrop::set_window_capture_excluded(handle, false)?;
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn allow_presence_windows_in_capture(_native_windows: PresenceNativeWindows) -> Result<(), String> {
    Ok(())
}

pub fn run() {
    if let Err(error) = process_lifetime::protect_process_tree() {
        eprintln!("failed to protect Fairy child-process lifetime: {error}");
    }
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .on_page_load(|webview, payload| {
            if webview.label() != "main"
                || payload.event() != tauri::webview::PageLoadEvent::Finished
            {
                return;
            }
            request_presence_initialization(webview.app_handle().clone());
        })
        .setup(|app| {
            let data_dir = configured_desktop_data_dir(app.handle())?;
            std::fs::create_dir_all(&data_dir)?;
            let desktop_program = std::env::current_exe()?;
            let resource_dir = app.path().resource_dir()?;
            let launch = configured_core_launch(&data_dir, &desktop_program, &resource_dir)
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            let voice_launch = if cfg!(debug_assertions) {
                development_voice_launch(&data_dir)
            } else {
                bundled_voice_launch(&data_dir, &resource_dir, &desktop_program)
            };
            let voice = Arc::new(VoiceWorkerManager::new(voice_launch));
            let realtime_launch = if cfg!(debug_assertions) {
                development_realtime_launch(&data_dir)
            } else {
                bundled_realtime_launch(&data_dir, &resource_dir)
            };
            let realtime = Arc::new(RealtimeWorkerManager::new(realtime_launch));
            let preferences_store = DesktopPreferencesStore::new(&data_dir);
            let startup_preferences = preferences_store.load_for_startup()?;
            let preferences = startup_preferences.preferences;
            let legacy_memory = startup_preferences.legacy_memory;
            let preference_migration_required = startup_preferences.migration_required;
            let migrated_preferences = preferences.clone();
            let presence = PresenceCoordinatorHandle::new(coordinator_config(&preferences));
            app.manage(DesktopState {
                core: Arc::new(Mutex::new(None)),
                voice,
                realtime,
                preferences: Mutex::new(()),
                provider_update_in_progress: AtomicBool::new(false),
                data_dir,
                desktop_program,
                resource_dir,
                presence,
                native_gpu: Arc::new(NativePresenceGpuManager::default()),
                native_gpu_reset_in_flight: AtomicBool::new(false),
                presence_windows: Mutex::new(None),
                pet_drag: Mutex::new(None),
                pet_input_geometry: Mutex::new(PetInputGeometry {
                    layout: PetInputLayout::Hidden,
                    compact_width_logical: PET_INPUT_COMPACT_WIDTH_LOGICAL,
                    compact_height_logical: 64.0,
                    expanded_content_height_logical: PET_INPUT_EXPANDED_CONTENT_HEIGHT_LOGICAL,
                }),
                pet_input_presentation: Mutex::new(PetInputPresentationFence::default()),
                renderer_supervisor: Mutex::new(PresenceRendererSupervisor::default()),
                renderer_health: Mutex::new(None),
                presence_startup: PresenceStartupGate::default(),
                pet_placement_reconciled: AtomicBool::new(false),
                started_at: Instant::now(),
            });
            let tray = build_fairy_tray(app, &preferences)?;
            app.manage(tray);
            sync_tray_preferences(app.handle(), &preferences);
            let core_app = app.handle().clone();
            tauri::async_runtime::spawn_blocking(move || {
                match CoreBridge::spawn_verified(launch) {
                    Ok(bridge) => {
                        let memory_migration =
                            import_legacy_memory_settings(&bridge, legacy_memory);
                        let Some(state) = core_app.try_state::<DesktopState>() else {
                            return;
                        };
                        if let Err(error) = &memory_migration {
                            eprintln!("deferred legacy Memory Settings migration: {error}");
                        } else if preference_migration_required {
                            if let Err(error) = DesktopPreferencesStore::new(&state.data_dir)
                                .complete_startup_migration(&migrated_preferences)
                            {
                                eprintln!(
                                    "failed to finalize desktop preference migration: {error}"
                                );
                            }
                        }
                        let Ok(mut core) = state.core.lock() else {
                            return;
                        };
                        *core = Some(bridge);
                        drop(core);
                        let _ = core_app.emit("fairy-core-ready", ());
                    }
                    Err(error) => eprintln!("failed to start Fairy Core: {error}"),
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            core_rpc,
            settings_rpc,
            provider_openrouter_status,
            provider_openrouter_configure,
            provider_openrouter_delete,
            provider_realtime_status,
            provider_realtime_configure,
            provider_realtime_delete,
            realtime_worker_status,
            realtime_worker_start,
            realtime_worker_stop,
            realtime_worker_tool_result,
            desktop_preferences_get,
            desktop_preferences_update,
            pet_preferences_update,
            pet_input_set_layout,
            pet_input_set_interactive,
            pet_input_request_focus,
            pet_input_presentation_begin,
            pet_input_presentation_apply,
            pet_interaction_snapshot_get,
            pet_window_group_begin_drag,
            pet_window_group_move,
            pet_window_group_end_drag,
            pet_window_group_reset_position,
            pet_renderer_report_health,
            pet_renderer_get_health,
            pet_render_settings_get,
            pet_native_gpu_start,
            pet_native_gpu_status,
            pet_native_gpu_update,
            pet_native_gpu_rebind,
            pet_native_gpu_prepare_visual_test,
            pet_native_gpu_stop,
            pet_exit,
            open_main_window,
            open_settings_window,
            voice_worker_health,
            voice_model_install,
            voice_session_start,
            voice_session_cancel,
            voice_test_start,
            voice_test_cancel,
            realtime_voice_start,
            realtime_voice_cancel,
            select_project_folder,
            select_obsidian_vault,
            select_skill_source,
            capture::list_capture_surfaces,
            capture::capture_surface,
            presence_backdrop::pet_backdrop_capture
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Fairy desktop");
    application.run(|app, event| match event {
        tauri::RunEvent::Resumed => reset_native_presence_after_resume(app),
        tauri::RunEvent::WindowEvent { label, event, .. } if label == PET_RENDER_LABEL => {
            match event {
                tauri::WindowEvent::Resized(_) | tauri::WindowEvent::ScaleFactorChanged { .. } => {
                    reset_native_presence(app, "surface_changed");
                }
                tauri::WindowEvent::Destroyed => stop_native_presence(app),
                _ => {}
            }
        }
        tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. } => {
            let _ = app.emit_to(
                PET_RENDER_LABEL,
                PRESENCE_NATIVE_RENDERER_LIFECYCLE_EVENT,
                NativeRendererLifecycleSignal {
                    schema_version: 1,
                    reason: "shutdown",
                },
            );
            stop_native_presence(app);
        }
        _ => {}
    });
}

#[cfg(test)]
mod pet_input_presentation_tests {
    use super::{
        pet_input_presentation_is_interactive, PetInputLayout, PetInputPresentationFence,
        PetInputPresentationRequest,
    };

    #[test]
    fn hidden_layout_never_reenables_the_webview_surface() {
        let request = PetInputPresentationRequest {
            session_id: 1,
            revision: 1,
            layout: PetInputLayout::Hidden,
            compact_width: None,
            compact_height: None,
            expanded_content_height: None,
            interactive: true,
            request_focus: false,
        };

        assert!(!pet_input_presentation_is_interactive(&request));
    }

    #[test]
    fn core_layout_keeps_the_circular_pointer_proxy_interactive() {
        let request = PetInputPresentationRequest {
            session_id: 1,
            revision: 1,
            layout: PetInputLayout::Core,
            compact_width: None,
            compact_height: None,
            expanded_content_height: None,
            interactive: true,
            request_focus: false,
        };

        assert!(pet_input_presentation_is_interactive(&request));
    }

    #[test]
    fn new_session_fences_every_revision_from_the_previous_webview() {
        let mut fence = PetInputPresentationFence::default();
        let first = fence.begin();
        fence.validate(first.session_id, 12).unwrap();
        fence.revision = 12;

        let second = fence.begin();
        assert!(second.session_id > first.session_id);
        assert_eq!(second.revision, 0);
        assert_eq!(
            fence.validate(first.session_id, 13),
            Err("PET_INPUT_PRESENTATION_STALE_SESSION")
        );
        assert!(fence.validate(second.session_id, 1).is_ok());
    }

    #[test]
    fn duplicate_and_out_of_order_revisions_are_rejected() {
        let mut fence = PetInputPresentationFence::default();
        let session = fence.begin();
        fence.validate(session.session_id, 3).unwrap();
        fence.revision = 3;

        assert_eq!(
            fence.validate(session.session_id, 3),
            Err("PET_INPUT_PRESENTATION_STALE_REVISION")
        );
        assert_eq!(
            fence.validate(session.session_id, 2),
            Err("PET_INPUT_PRESENTATION_STALE_REVISION")
        );
        assert!(fence.validate(session.session_id, 4).is_ok());
    }
}

#[cfg(all(test, target_os = "windows"))]
mod native_window_group_tests {
    use super::*;
    use windows_sys::Win32::Foundation::HWND;
    use windows_sys::Win32::Graphics::Gdi::{
        CreateRectRgn, DeleteObject, GetWindowRgn, PtInRegion,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DestroyWindow, GetWindow, GetWindowLongPtrW, GetWindowTextLengthW,
        SetWindowPos, GWL_EXSTYLE, GW_HWNDPREV, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
        WS_EX_APPWINDOW, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_POPUP,
    };

    struct TestWindow(HWND);

    impl Drop for TestWindow {
        fn drop(&mut self) {
            unsafe {
                DestroyWindow(self.0);
            }
        }
    }

    fn create_test_window(frame: PhysicalFrame) -> TestWindow {
        let _dpi_scope = PerMonitorDpiScope::enter();
        let class = "STATIC\0".encode_utf16().collect::<Vec<_>>();
        let title = "Fairy native group test\0"
            .encode_utf16()
            .collect::<Vec<_>>();
        let handle = unsafe {
            CreateWindowExW(
                0,
                class.as_ptr(),
                title.as_ptr(),
                WS_POPUP,
                frame.x,
                frame.y,
                frame.width as i32,
                frame.height as i32,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        };
        assert!(!handle.is_null(), "test HWND must be created");
        TestWindow(handle)
    }

    fn window_is_above(upper: HWND, lower: HWND) -> bool {
        let mut current = lower;
        for _ in 0..512 {
            current = unsafe { GetWindow(current, GW_HWNDPREV) };
            if current.is_null() {
                return false;
            }
            if current == upper {
                return true;
            }
        }
        false
    }

    #[test]
    fn render_shell_policy_clears_titles_and_never_creates_a_taskbar_window() {
        let render = create_test_window(PhysicalFrame {
            x: 100,
            y: 100,
            width: 640,
            height: 260,
        });

        enforce_presence_window_shell_policy(render.0 as isize, Some(false))
            .expect("render shell policy should apply");

        let style = unsafe { GetWindowLongPtrW(render.0, GWL_EXSTYLE) } as u32;
        assert_eq!(style & WS_EX_APPWINDOW, 0);
        assert_ne!(style & WS_EX_TOOLWINDOW, 0);
        assert_ne!(style & WS_EX_NOACTIVATE, 0);
        assert_eq!(unsafe { GetWindowTextLengthW(render.0) }, 0);
    }

    #[test]
    fn core_input_region_is_circular_instead_of_a_rectangular_hit_surface() {
        let _dpi_scope = PerMonitorDpiScope::enter();
        let input = create_test_window(PhysicalFrame {
            x: 0,
            y: 0,
            width: 616,
            height: 360,
        });
        apply_pet_input_window_region(
            input.0 as isize,
            PetInputWindowRegion {
                layout: PetInputLayout::Core,
                width: 616,
                height: 360,
                compact_content_width: 300,
                compact_content_height: 64,
                expanded_content_height: 72,
                scale_factor: 1.0,
                direction: ExpansionDirection::Right,
            },
        )
        .expect("core input region should apply");

        let region = unsafe { CreateRectRgn(0, 0, 0, 0) };
        assert!(!region.is_null());
        assert_ne!(unsafe { GetWindowRgn(input.0, region) }, 0);
        assert_ne!(unsafe { PtInRegion(region, 72, 188) }, 0);
        assert_eq!(unsafe { PtInRegion(region, 2, 2) }, 0);
        unsafe { DeleteObject(region) };
    }

    #[test]
    fn hidden_input_layout_applies_an_empty_native_hit_region() {
        let _dpi_scope = PerMonitorDpiScope::enter();
        let input = create_test_window(PhysicalFrame {
            x: 0,
            y: 0,
            width: 616,
            height: 360,
        });
        apply_pet_input_window_region(
            input.0 as isize,
            PetInputWindowRegion {
                layout: PetInputLayout::Hidden,
                width: 616,
                height: 360,
                compact_content_width: 0,
                compact_content_height: 64,
                expanded_content_height: 0,
                scale_factor: 1.0,
                direction: ExpansionDirection::Right,
            },
        )
        .expect("hidden input layout should apply an empty native region");

        let region = unsafe { CreateRectRgn(0, 0, 0, 0) };
        assert!(!region.is_null());
        assert_ne!(unsafe { GetWindowRgn(input.0, region) }, 0);
        assert_eq!(unsafe { PtInRegion(region, 72, 188) }, 0);
        assert_eq!(unsafe { PtInRegion(region, 2, 2) }, 0);
        unsafe { DeleteObject(region) };
    }

    #[test]
    fn compact_input_region_exposes_the_core_and_dynamic_capsule_only() {
        let _dpi_scope = PerMonitorDpiScope::enter();
        let parts = pet_input_region_parts(PetInputWindowRegion {
            layout: PetInputLayout::Compact,
            width: 616,
            height: 360,
            compact_content_width: 308,
            compact_content_height: 64,
            expanded_content_height: 72,
            scale_factor: 1.0,
            direction: ExpansionDirection::Right,
        });
        assert_eq!(
            parts,
            vec![
                PetInputRegionPart {
                    kind: PetInputRegionKind::Ellipse,
                    left: 0,
                    top: 116,
                    right: 144,
                    bottom: 260,
                    corner_diameter: 0,
                },
                PetInputRegionPart {
                    kind: PetInputRegionKind::RoundedRectangle,
                    left: 8,
                    top: 288,
                    right: 300,
                    bottom: 352,
                    corner_diameter: 64,
                },
            ]
        );

        let input = create_test_window(PhysicalFrame {
            x: 0,
            y: 0,
            width: 616,
            height: 360,
        });
        apply_pet_input_window_region(
            input.0 as isize,
            PetInputWindowRegion {
                layout: PetInputLayout::Compact,
                width: 616,
                height: 360,
                compact_content_width: 308,
                compact_content_height: 64,
                expanded_content_height: 72,
                scale_factor: 1.0,
                direction: ExpansionDirection::Right,
            },
        )
        .expect("compact input region should apply");
        let region = unsafe { CreateRectRgn(0, 0, 0, 0) };
        assert!(!region.is_null());
        assert_ne!(unsafe { GetWindowRgn(input.0, region) }, 0);
        assert_ne!(unsafe { PtInRegion(region, 72, 188) }, 0);
        assert_ne!(unsafe { PtInRegion(region, 154, 320) }, 0);
        assert_eq!(unsafe { PtInRegion(region, 220, 100) }, 0);
        unsafe { DeleteObject(region) };
    }

    #[test]
    fn compact_input_region_moves_only_the_core_with_expansion_direction() {
        let parts = pet_input_region_parts(PetInputWindowRegion {
            layout: PetInputLayout::Compact,
            width: 616,
            height: 360,
            compact_content_width: 308,
            compact_content_height: 64,
            expanded_content_height: 72,
            scale_factor: 1.0,
            direction: ExpansionDirection::Left,
        });
        assert_eq!(parts.len(), 2);
        assert_eq!(parts[0].kind, PetInputRegionKind::Ellipse);
        assert_eq!(parts[0].left, 472);
        assert_eq!(parts[0].right, 616);
        assert_eq!(parts[1].left, 316);
        assert_eq!(parts[1].right, 608);
    }

    #[test]
    fn compact_input_region_tracks_multiline_height_without_exposing_the_window() {
        let parts = pet_input_region_parts(PetInputWindowRegion {
            layout: PetInputLayout::Compact,
            width: 616,
            height: 360,
            compact_content_width: 360,
            compact_content_height: 104,
            expanded_content_height: 72,
            scale_factor: 1.0,
            direction: ExpansionDirection::Right,
        });
        assert_eq!(parts.len(), 2);
        assert_eq!(parts[1].kind, PetInputRegionKind::RoundedRectangle);
        assert_eq!(parts[1].left, 8);
        assert_eq!(parts[1].top, 248);
        assert_eq!(parts[1].right, 352);
        assert_eq!(parts[1].bottom, 352);
        assert_eq!(parts[1].corner_diameter, 104);
    }

    #[test]
    fn compact_input_region_can_be_reapplied_after_direction_changes() {
        let _dpi_scope = PerMonitorDpiScope::enter();
        let input = create_test_window(PhysicalFrame {
            x: 0,
            y: 0,
            width: 616,
            height: 360,
        });
        apply_pet_input_window_region(
            input.0 as isize,
            PetInputWindowRegion {
                layout: PetInputLayout::Compact,
                width: 616,
                height: 360,
                compact_content_width: 308,
                compact_content_height: 64,
                expanded_content_height: 72,
                scale_factor: 1.0,
                direction: ExpansionDirection::Right,
            },
        )
        .expect("right-facing input region should apply");
        apply_pet_input_window_region(
            input.0 as isize,
            PetInputWindowRegion {
                layout: PetInputLayout::Compact,
                width: 616,
                height: 360,
                compact_content_width: 308,
                compact_content_height: 64,
                expanded_content_height: 72,
                scale_factor: 1.0,
                direction: ExpansionDirection::Left,
            },
        )
        .expect("left-facing input region should replace the old region");

        let region = unsafe { CreateRectRgn(0, 0, 0, 0) };
        assert!(!region.is_null());
        assert_ne!(unsafe { GetWindowRgn(input.0, region) }, 0);
        assert_ne!(unsafe { PtInRegion(region, 544, 188) }, 0);
        assert_eq!(unsafe { PtInRegion(region, 72, 188) }, 0);
        assert_ne!(unsafe { PtInRegion(region, 462, 320) }, 0);
        unsafe { DeleteObject(region) };
    }

    #[test]
    fn expanded_input_region_keeps_the_core_and_menu_interactive() {
        let parts = pet_input_region_parts(PetInputWindowRegion {
            layout: PetInputLayout::Expanded,
            width: 616,
            height: 360,
            compact_content_width: 300,
            compact_content_height: 64,
            expanded_content_height: 72,
            scale_factor: 1.0,
            direction: ExpansionDirection::Right,
        });
        assert_eq!(parts.len(), 2);
        assert_eq!(
            parts[0],
            PetInputRegionPart {
                kind: PetInputRegionKind::Ellipse,
                left: 0,
                top: 116,
                right: 144,
                bottom: 260,
                corner_diameter: 0,
            }
        );
        assert_eq!(parts[1].kind, PetInputRegionKind::RoundedRectangle);
        assert_eq!(parts[1].left, 156);
        assert_eq!(parts[1].top, 280);
        assert_eq!(parts[1].right, 608);
        assert_eq!(parts[1].bottom, 352);

        let _dpi_scope = PerMonitorDpiScope::enter();
        let input = create_test_window(PhysicalFrame {
            x: 0,
            y: 0,
            width: 616,
            height: 360,
        });
        apply_pet_input_window_region(
            input.0 as isize,
            PetInputWindowRegion {
                layout: PetInputLayout::Expanded,
                width: 616,
                height: 360,
                compact_content_width: 300,
                compact_content_height: 64,
                expanded_content_height: 72,
                scale_factor: 1.0,
                direction: ExpansionDirection::Right,
            },
        )
        .expect("expanded input region should apply");
        let region = unsafe { CreateRectRgn(0, 0, 0, 0) };
        assert!(!region.is_null());
        assert_ne!(unsafe { GetWindowRgn(input.0, region) }, 0);
        assert_ne!(unsafe { PtInRegion(region, 72, 188) }, 0);
        assert_ne!(unsafe { PtInRegion(region, 300, 320) }, 0);
        assert_eq!(unsafe { PtInRegion(region, 300, 100) }, 0);
        assert_eq!(unsafe { PtInRegion(region, 300, 20) }, 0);
        unsafe { DeleteObject(region) };
    }

    #[test]
    fn drag_session_ids_are_bounded_opaque_tokens() {
        assert!(validate_pet_drag_session_id("48ebc11a-a24c-48e5-bae1-97fabc81bc41").is_ok());
        assert!(validate_pet_drag_session_id("").is_err());
        assert!(validate_pet_drag_session_id("invalid session").is_err());
        assert!(validate_pet_drag_session_id(&"x".repeat(129)).is_err());
    }

    #[test]
    fn native_geometry_scope_uses_per_monitor_v2_and_restores_the_caller() {
        use windows_sys::Win32::UI::HiDpi::{
            AreDpiAwarenessContextsEqual, GetThreadDpiAwarenessContext,
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        };

        let before = unsafe { GetThreadDpiAwarenessContext() };
        {
            let _scope = PerMonitorDpiScope::enter();
            let current = unsafe { GetThreadDpiAwarenessContext() };
            assert_ne!(
                unsafe {
                    AreDpiAwarenessContextsEqual(
                        current,
                        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
                    )
                },
                0
            );
        }
        let restored = unsafe { GetThreadDpiAwarenessContext() };
        assert_ne!(unsafe { AreDpiAwarenessContextsEqual(before, restored) }, 0);
    }

    #[test]
    fn drag_geometry_preserves_core_layout_across_dpi_changes() {
        let source = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 900, y: 600 },
            render_size_for_scale(1.0),
            PhysicalFrame {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_040,
            },
            1.0,
            Some(ExpansionDirection::Left),
        );
        let target = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 3_200, y: 500 },
            render_size_for_scale(1.25),
            PhysicalFrame {
                x: 2_560,
                y: -114,
                width: 1_920,
                height: 1_032,
            },
            1.25,
            Some(ExpansionDirection::Left),
        );
        let geometry = pet_input_geometry_from_frame(source.core_frame(144), 1.0);

        assert_eq!(geometry.layout, PetInputLayout::Core);
        assert_eq!(pet_input_frame_for_geometry(target, geometry).width, 770);
        assert_eq!(pet_input_frame_for_geometry(target, geometry).height, 450);
    }

    #[test]
    fn first_drag_uses_the_visible_core_instead_of_a_stale_direction() {
        let monitor = PresenceMonitor {
            id: "primary".to_owned(),
            work_area: PhysicalFrame {
                x: 0,
                y: 0,
                width: 2_560,
                height: 1_400,
            },
            scale_factor: 1.0,
            is_primary: true,
        };
        let actual = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 1_300, y: 600 },
            render_size_for_scale(1.0),
            monitor.work_area,
            1.0,
            Some(ExpansionDirection::Left),
        );
        let stale = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 500, y: 300 },
            render_size_for_scale(1.0),
            monitor.work_area,
            1.0,
            Some(ExpansionDirection::Right),
        );

        let (resolved, geometry) = placement_from_actual_pet_windows(
            actual.render_frame,
            actual.core_frame(144),
            &[monitor],
            Some(stale),
            None,
        )
        .expect("visible windows should define the first drag anchor");

        assert_eq!(geometry.layout, PetInputLayout::Core);
        assert_eq!(resolved.anchor, actual.anchor);
        assert_eq!(resolved.expansion_direction, ExpansionDirection::Left);
        assert_eq!(resolved.render_frame, actual.render_frame);
    }

    #[test]
    fn first_drag_uses_the_monitor_containing_the_visible_input_surface() {
        let primary = PresenceMonitor {
            id: "primary".to_owned(),
            work_area: PhysicalFrame {
                x: 0,
                y: 0,
                width: 1_920,
                height: 1_040,
            },
            scale_factor: 1.0,
            is_primary: true,
        };
        let secondary = PresenceMonitor {
            id: "secondary".to_owned(),
            work_area: PhysicalFrame {
                x: 1_920,
                y: -120,
                width: 2_560,
                height: 1_440,
            },
            scale_factor: 1.25,
            is_primary: false,
        };
        let actual = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 3_200, y: 540 },
            render_size_for_scale(secondary.scale_factor),
            secondary.work_area,
            secondary.scale_factor,
            Some(ExpansionDirection::Right),
        );
        let stale = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 500, y: 300 },
            render_size_for_scale(primary.scale_factor),
            primary.work_area,
            primary.scale_factor,
            Some(ExpansionDirection::Left),
        );
        let core_extent = (PET_CORE_EXTENT_LOGICAL * secondary.scale_factor).round() as u32;

        let (resolved, _) = placement_from_actual_pet_windows(
            actual.render_frame,
            actual.core_frame(core_extent),
            &[primary, secondary.clone()],
            Some(stale),
            None,
        )
        .expect("visible input should select the secondary monitor");

        assert_eq!(resolved.monitor_work_area, secondary.work_area);
        assert_eq!(resolved.anchor, actual.anchor);
        assert_eq!(resolved.scale_factor, secondary.scale_factor);
    }

    #[test]
    fn native_core_position_overrides_a_stale_input_proxy() {
        let monitor = PresenceMonitor {
            id: "primary".to_owned(),
            work_area: PhysicalFrame {
                x: 0,
                y: 0,
                width: 2_560,
                height: 1_400,
            },
            scale_factor: 1.0,
            is_primary: true,
        };
        let visible = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 1_528, y: 932 },
            render_size_for_scale(1.0),
            monitor.work_area,
            1.0,
            Some(ExpansionDirection::Left),
        );
        let stale_input = PhysicalFrame {
            x: 1_904,
            y: 860,
            width: 144,
            height: 144,
        };
        let presentation = NativeGpuPresentation {
            expansion_direction: NativeGpuExpansionDirection::Left,
            core_x: 544.0,
            core_y: 88.0,
            ..NativeGpuPresentation::default()
        };

        let (resolved, geometry) = placement_from_actual_pet_windows(
            visible.render_frame,
            stale_input,
            &[monitor],
            None,
            Some(presentation),
        )
        .expect("native presentation should define the visible core anchor");

        assert_eq!(geometry.layout, PetInputLayout::Core);
        assert_eq!(resolved.anchor, visible.anchor);
        assert_eq!(resolved.expansion_direction, ExpansionDirection::Left);
        assert_eq!(resolved.render_frame, visible.render_frame);
        assert_ne!(physical_frame_center(stale_input), resolved.anchor);
    }

    #[test]
    fn native_presentation_preserves_the_visible_edge_frame() {
        let monitor = PresenceMonitor {
            id: "primary".to_owned(),
            work_area: PhysicalFrame {
                x: 0,
                y: 0,
                width: 1_280,
                height: 720,
            },
            scale_factor: 1.0,
            is_primary: true,
        };
        let visible_frame = PhysicalFrame {
            x: 720,
            y: 320,
            width: 640,
            height: 260,
        };
        let presentation = NativeGpuPresentation {
            expansion_direction: NativeGpuExpansionDirection::Right,
            core_x: 96.0,
            core_y: 88.0,
            ..NativeGpuPresentation::default()
        };

        let placement = placement_from_native_presentation(visible_frame, &[monitor], presentation)
            .expect("the visible native surface should define drag geometry");

        assert_eq!(placement.render_frame, visible_frame);
        assert_eq!(placement.anchor, PhysicalPoint { x: 816, y: 408 });
    }

    #[test]
    fn drag_geometry_preserves_compact_logical_width_across_dpi_changes() {
        let target = resolve_presence_placement_for_anchor(
            PhysicalPoint { x: 3_200, y: 500 },
            render_size_for_scale(1.25),
            PhysicalFrame {
                x: 2_560,
                y: -114,
                width: 1_920,
                height: 1_032,
            },
            1.25,
            Some(ExpansionDirection::Right),
        );
        let geometry = pet_input_geometry_from_frame(
            PhysicalFrame {
                x: 0,
                y: 0,
                width: 308,
                height: 260,
            },
            1.0,
        );
        let frame = pet_input_frame_for_geometry(target, geometry);

        assert_eq!(geometry.layout, PetInputLayout::Compact);
        assert_eq!(frame.width, 770);
        assert_eq!(frame.height, 450);
    }

    #[test]
    fn native_batch_moves_render_and_input_by_the_same_delta() {
        let render_start = PhysicalFrame {
            x: 100,
            y: 120,
            width: 240,
            height: 120,
        };
        let input_start = PhysicalFrame {
            x: 340,
            y: 150,
            width: 320,
            height: 72,
        };
        let render = create_test_window(render_start);
        let input = create_test_window(input_start);
        let render_target = PhysicalFrame {
            x: 148,
            y: 157,
            ..render_start
        };
        let input_target = PhysicalFrame {
            x: 388,
            y: 187,
            ..input_start
        };

        move_native_presence_window_positions(
            render.0 as isize,
            render_target,
            None,
            Some((input.0 as isize, input_target)),
        )
        .expect("native window group should move");

        let render_after = native_presence_window_frame(render.0 as isize).unwrap();
        let input_after = native_presence_window_frame(input.0 as isize).unwrap();
        assert_eq!(render_after, render_target);
        assert_eq!(input_after, input_target);
        assert_eq!(
            render_after.x - render_start.x,
            input_after.x - input_start.x
        );
        assert_eq!(
            render_after.y - render_start.y,
            input_after.y - input_start.y
        );
        assert!(window_is_above(input.0, render.0));
    }

    #[test]
    fn native_batch_resizes_both_surfaces_for_a_cross_dpi_move() {
        let render = create_test_window(PhysicalFrame {
            x: 1_200,
            y: 600,
            width: 640,
            height: 260,
        });
        let input = create_test_window(PhysicalFrame {
            x: 1_300,
            y: 620,
            width: 144,
            height: 144,
        });
        let render_target = PhysicalFrame {
            x: 2_700,
            y: -80,
            width: 800,
            height: 325,
        };
        let input_target = PhysicalFrame {
            x: 2_730,
            y: -55,
            width: 180,
            height: 180,
        };

        move_native_presence_window_positions(
            render.0 as isize,
            render_target,
            None,
            Some((input.0 as isize, input_target)),
        )
        .expect("cross-DPI window group move should resize atomically");

        assert_eq!(
            native_presence_window_frame(render.0 as isize).unwrap(),
            render_target
        );
        assert_eq!(
            native_presence_window_frame(input.0 as isize).unwrap(),
            input_target
        );
        assert!(window_is_above(input.0, render.0));
    }

    #[test]
    fn native_batch_restores_input_above_render_and_supports_negative_coordinates() {
        let render = create_test_window(PhysicalFrame {
            x: 20,
            y: 20,
            width: 640,
            height: 260,
        });
        let input = create_test_window(PhysicalFrame {
            x: 300,
            y: 100,
            width: 320,
            height: 72,
        });
        assert_ne!(
            unsafe {
                SetWindowPos(
                    render.0,
                    std::ptr::null_mut(),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE,
                )
            },
            0
        );

        let render_target = PhysicalFrame {
            x: -2_400,
            y: -160,
            width: 640,
            height: 260,
        };
        let input_target = PhysicalFrame {
            x: -2_120,
            y: -80,
            width: 320,
            height: 72,
        };
        move_native_presence_window_positions(
            render.0 as isize,
            render_target,
            None,
            Some((input.0 as isize, input_target)),
        )
        .expect("native window group should restore its z-order");

        assert_eq!(
            native_presence_window_frame(render.0 as isize).unwrap(),
            render_target
        );
        assert_eq!(
            native_presence_window_frame(input.0 as isize).unwrap(),
            input_target
        );
        assert!(window_is_above(input.0, render.0));
    }

    #[test]
    fn native_batch_moves_composition_surface_with_render_and_below_input() {
        let render = create_test_window(PhysicalFrame {
            x: 80,
            y: 90,
            width: 640,
            height: 260,
        });
        let native_surface = create_test_window(PhysicalFrame {
            x: 80,
            y: 90,
            width: 640,
            height: 260,
        });
        let input = create_test_window(PhysicalFrame {
            x: 360,
            y: 184,
            width: 320,
            height: 72,
        });
        let render_target = PhysicalFrame {
            x: -1_920,
            y: 240,
            width: 640,
            height: 260,
        };
        let input_target = PhysicalFrame {
            x: -1_640,
            y: 334,
            width: 320,
            height: 72,
        };

        move_native_presence_window_positions(
            render.0 as isize,
            render_target,
            Some(native_surface.0 as isize),
            Some((input.0 as isize, input_target)),
        )
        .expect("native surface, render shell, and input should move in one batch");

        assert_eq!(
            native_presence_window_frame(render.0 as isize).unwrap(),
            render_target
        );
        assert_eq!(
            native_presence_window_frame(native_surface.0 as isize).unwrap(),
            render_target
        );
        assert_eq!(
            native_presence_window_frame(input.0 as isize).unwrap(),
            input_target
        );
        assert!(window_is_above(input.0, native_surface.0));
        assert!(window_is_above(native_surface.0, render.0));
    }

    #[test]
    fn hidden_input_drag_moves_only_the_visible_native_surfaces() {
        let render_start = PhysicalFrame {
            x: 80,
            y: 90,
            width: 640,
            height: 260,
        };
        let input_start = PhysicalFrame {
            x: 360,
            y: 184,
            width: 144,
            height: 144,
        };
        let render = create_test_window(render_start);
        let native_surface = create_test_window(render_start);
        let input = create_test_window(input_start);
        let render_target = PhysicalFrame {
            x: 600,
            y: 310,
            ..render_start
        };

        move_native_presence_window_positions(
            render.0 as isize,
            render_target,
            Some(native_surface.0 as isize),
            None,
        )
        .expect("hidden input should not be relocated during native drag");

        assert_eq!(
            native_presence_window_frame(render.0 as isize).unwrap(),
            render_target
        );
        assert_eq!(
            native_presence_window_frame(native_surface.0 as isize).unwrap(),
            render_target
        );
        assert_eq!(
            native_presence_window_frame(input.0 as isize).unwrap(),
            input_start
        );
    }

    #[test]
    fn stale_native_surface_never_blocks_the_authoritative_window_pair() {
        let render = create_test_window(PhysicalFrame {
            x: 40,
            y: 60,
            width: 640,
            height: 260,
        });
        let input = create_test_window(PhysicalFrame {
            x: 320,
            y: 154,
            width: 320,
            height: 72,
        });
        let render_target = PhysicalFrame {
            x: 500,
            y: 280,
            width: 640,
            height: 260,
        };
        let input_target = PhysicalFrame {
            x: 780,
            y: 374,
            width: 320,
            height: 72,
        };

        move_native_presence_window_positions(
            render.0 as isize,
            render_target,
            Some(isize::MAX),
            Some((input.0 as isize, input_target)),
        )
        .expect("a destroyed optional surface must not block the Tauri window pair");

        assert_eq!(
            native_presence_window_frame(render.0 as isize).unwrap(),
            render_target
        );
        assert_eq!(
            native_presence_window_frame(input.0 as isize).unwrap(),
            input_target
        );
        assert!(window_is_above(input.0, render.0));
    }

    #[test]
    fn drag_delta_uses_one_calibrated_native_coordinate_space() {
        let start = PhysicalPoint { x: 500, y: 300 };

        assert_eq!(
            resolve_pet_drag_delta(start, Some(start), (-48, 12), 1.25),
            (0, 0)
        );
        assert_eq!(
            resolve_pet_drag_delta(
                start,
                Some(PhysicalPoint { x: 452, y: 312 }),
                (-48, 12),
                2.0,
            ),
            (-48, 12)
        );
        assert_eq!(
            resolve_pet_drag_delta(start, None, (-48, 12), 1.25),
            (-60, 15)
        );
    }

    #[test]
    fn async_drag_handoff_preserves_the_initial_pointer_delta() {
        let current = PhysicalPoint { x: 1_030, y: 515 };
        let start = calibrated_pet_drag_start_pointer(current, (24, 12), 1.25);

        assert_eq!(start, PhysicalPoint { x: 1_000, y: 500 });
        assert_eq!(
            resolve_pet_drag_delta(start, Some(current), (24, 12), 1.25),
            (30, 15)
        );
        assert_eq!(
            resolve_pet_drag_delta(
                start,
                Some(PhysicalPoint { x: 1_040, y: 520 }),
                (32, 16),
                1.25,
            ),
            (40, 20)
        );
    }
}
