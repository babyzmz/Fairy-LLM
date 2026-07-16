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
    PetPreferencesUpdate,
};
use presence_coordinator::{
    anchor_from_ratios, anchor_ratios, global_cursor_position, resolve_presence_placement,
    resolve_presence_placement_for_anchor, select_work_area, ExpansionDirection, PhysicalFrame,
    PhysicalPoint, PresenceCoordinatorConfig, PresenceCoordinatorHandle, PresenceWindowPlacement,
    PET_CORE_EXTENT_LOGICAL, PET_INPUT_COMPACT_HEIGHT_LOGICAL, PET_INPUT_COMPACT_WIDTH_LOGICAL,
    PET_INPUT_EXPANDED_HEIGHT_LOGICAL, PET_INPUT_EXPANDED_WIDTH_LOGICAL,
};
use presence_renderer_supervisor::{
    PresenceRendererDirective, PresenceRendererHealthReport, PresenceRendererStatus,
    PresenceRendererSupervisor,
};
use presence_startup::PresenceStartupGate;
use provider_configuration::{openrouter_profiles_json, ProviderConfigurationStore};
use provider_credentials::{CredentialReplacement, ProviderCredentialStore};
use voice_worker::{
    bundled_voice_launch, development_voice_launch, prepared_test_session, PreparedVoiceSession,
    VoiceStreamEvent, VoiceStreamInput, VoiceWorkerManager,
};

pub mod capture;
pub mod desktop_preferences;
pub mod presence_backdrop;
pub mod presence_coordinator;
pub mod presence_interaction;
pub mod presence_renderer_supervisor;
pub mod presence_runtime;
pub mod presence_startup;
pub mod presence_window_policy;
pub mod provider_configuration;
pub mod provider_credentials;
pub mod voice_worker;

pub const PET_RENDER_LABEL: &str = "pet-render";
pub const PET_INPUT_LABEL: &str = "pet-input";
const TRAY_ASK_ID: &str = "fairy.tray.ask";
const TRAY_NEW_CHAT_ID: &str = "fairy.tray.new_chat";
const TRAY_AUTO_PLAY_ID: &str = "fairy.tray.auto_play";
const TRAY_MUTED_ID: &str = "fairy.tray.muted";
const TRAY_ALWAYS_ON_TOP_ID: &str = "fairy.tray.always_on_top";
const TRAY_OPEN_ID: &str = "fairy.tray.open";
const TRAY_SETTINGS_ID: &str = "fairy.tray.settings";
const TRAY_RESET_ID: &str = "fairy.tray.reset";
const TRAY_EXIT_ID: &str = "fairy.tray.exit";

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
            | "skills.list"
            | "extensions.catalog.list"
            | "skills.install"
            | "skills.update"
            | "skills.set_enabled"
            | "skills.remove"
            | "tasks.list"
            | "mcp.servers.list"
            | "mcp.servers.configure"
            | "mcp.servers.discover"
            | "mcp.servers.accept"
            | "mcp.servers.set_enabled"
            | "mcp.servers.delete"
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
    let render_right = i64::from(render.x) + i64::from(render.width);
    let render_center_y = i64::from(render.y) + i64::from(render.height) / 2;
    let compact_bottom = render_center_y + i64::from(compact_height) / 2;
    let y = if target_height <= compact_height {
        render_center_y - i64::from(target_height) / 2
    } else {
        compact_bottom - i64::from(target_height)
    };
    PetWindowFrame {
        x: (render_right - i64::from(target_width)) as i32,
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
    let anchor_x_offset = (96.0 * scale).round() as i64;
    let anchor_y_offset = (130.0 * scale).round() as i64;
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
    preferences: Mutex<()>,
    provider_update_in_progress: AtomicBool,
    data_dir: PathBuf,
    desktop_program: PathBuf,
    resource_dir: PathBuf,
    presence: PresenceCoordinatorHandle,
    presence_windows: Mutex<Option<PresenceNativeWindows>>,
    pet_drag: Mutex<Option<PetGroupDragSession>>,
    renderer_supervisor: Mutex<PresenceRendererSupervisor>,
    presence_startup: PresenceStartupGate,
    pet_placement_reconciled: AtomicBool,
    started_at: Instant,
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
    start_pointer: PhysicalPoint,
    start_anchor: PhysicalPoint,
    current_placement: PresenceWindowPlacement,
    monitors: Vec<PresenceMonitor>,
    native_windows: PresenceNativeWindows,
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
    state.presence.set_preferences(coordinator_config(&next));
    apply_pet_window_preferences(&app, &next)?;
    app.emit("desktop-preferences-changed", &next)
        .map_err(|error| error.to_string())?;
    sync_tray_preferences(&app, &next);
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
        let placement = place_pet_windows(&app, &next, Some(presence_native_windows(&state)?))?;
        set_presence_placement(&state, placement);
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
    state.presence.set_preferences(coordinator_config(&next));
    apply_pet_window_preferences(app, &next)?;
    app.emit("desktop-preferences-changed", &next)
        .map_err(|error| error.to_string())?;
    sync_tray_preferences(app, &next);
    Ok(next)
}

#[tauri::command]
async fn pet_window_group_begin_drag(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let app = window.app_handle();
    let native_windows = presence_native_windows(&state)?;
    let placement =
        current_pet_placement(app, Some(native_windows), state.presence.latest_placement())?;
    let session = PetGroupDragSession {
        start_pointer: global_cursor_position()
            .ok_or_else(|| "Pet cursor position is unavailable".to_owned())?,
        start_anchor: placement.anchor,
        current_placement: placement,
        monitors: presence_monitors_for_app(app)?,
        native_windows,
    };
    let mut drag = state
        .pet_drag
        .lock()
        .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
    *drag = Some(session);
    state.presence.set_repositioning(true);
    Ok(())
}

#[tauri::command]
async fn pet_window_group_move(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    delta_x: i32,
    delta_y: i32,
) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let app = window.app_handle();
    let mut drag = state
        .pet_drag
        .lock()
        .map_err(|_| "Pet drag lock is unavailable".to_owned())?;
    let session = drag
        .as_mut()
        .ok_or_else(|| "Pet drag has not started".to_owned())?;
    let (pointer_delta_x, pointer_delta_y) = global_cursor_position()
        .map(|pointer| {
            (
                pointer.x.saturating_sub(session.start_pointer.x),
                pointer.y.saturating_sub(session.start_pointer.y),
            )
        })
        .unwrap_or((delta_x, delta_y));
    let desired_anchor = PhysicalPoint {
        x: session.start_anchor.x.saturating_add(pointer_delta_x),
        y: session.start_anchor.y.saturating_add(pointer_delta_y),
    };
    let monitor = monitor_for_anchor(&session.monitors, desired_anchor)
        .ok_or_else(|| "No monitor is available".to_owned())?;
    let placement = resolve_presence_placement_for_anchor(
        desired_anchor,
        render_size_for_scale(monitor.scale_factor),
        monitor.work_area,
        monitor.scale_factor,
        Some(session.current_placement.expansion_direction),
    );
    move_pet_window_group(app, Some(session.native_windows), &placement)?;
    session.current_placement = placement;
    set_presence_placement(&state, placement);
    Ok(())
}

#[tauri::command]
async fn pet_window_group_end_drag(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
    expected_revision: u64,
) -> Result<DesktopPreferences, String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    let session = state
        .pet_drag
        .lock()
        .map_err(|_| "Pet drag lock is unavailable".to_owned())?
        .take();
    let result = (|| {
        let current = DesktopPreferencesStore::new(&state.data_dir)
            .load()
            .map_err(|error| error.to_string())?;
        let placement = match session {
            Some(session) => session.current_placement,
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
    result
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
    if let Ok(mut drag) = state.pet_drag.lock() {
        *drag = None;
    }
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
}

#[derive(Clone, Copy, Debug, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
enum PetInputLayout {
    Hidden,
    Core,
    Compact,
    Expanded,
}

#[tauri::command]
async fn pet_input_set_layout(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    layout: PetInputLayout,
) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    if matches!(layout, PetInputLayout::Hidden) {
        window
            .set_ignore_cursor_events(true)
            .map_err(|error| error.to_string())?;
        window
            .set_focusable(false)
            .map_err(|error| error.to_string())?;
        return window.hide().map_err(|error| error.to_string());
    }

    let app = window.app_handle();
    let input = app
        .get_webview_window(PET_INPUT_LABEL)
        .ok_or_else(|| "Pet input window is unavailable".to_owned())?;
    let placement = state
        .presence
        .latest_placement()
        .ok_or_else(|| "Pet placement is unavailable".to_owned())?;
    let scale = placement.scale_factor.clamp(0.5, 4.0);
    let (logical_width, logical_height) = match layout {
        PetInputLayout::Core => (PET_CORE_EXTENT_LOGICAL, PET_CORE_EXTENT_LOGICAL),
        PetInputLayout::Compact => (
            PET_INPUT_COMPACT_WIDTH_LOGICAL,
            PET_INPUT_COMPACT_HEIGHT_LOGICAL,
        ),
        PetInputLayout::Expanded => (
            PET_INPUT_EXPANDED_WIDTH_LOGICAL,
            PET_INPUT_EXPANDED_HEIGHT_LOGICAL,
        ),
        PetInputLayout::Hidden => unreachable!(),
    };
    let target_width = (logical_width * scale).round() as u32;
    let target_height = (logical_height * scale).round() as u32;
    let compact_height = (PET_INPUT_COMPACT_HEIGHT_LOGICAL * scale).round() as u32;
    let anchored_frame = match layout {
        PetInputLayout::Core => placement.core_frame(target_width),
        PetInputLayout::Compact | PetInputLayout::Expanded => {
            placement.input_frame(target_width, target_height, compact_height)
        }
        PetInputLayout::Hidden => unreachable!(),
    };
    let frame = PhysicalFrame {
        x: anchored_frame.x,
        y: anchored_frame.y,
        width: anchored_frame.width,
        height: anchored_frame.height,
    };
    let input_handle = presence_native_windows(&state)?.handle_for(PET_INPUT_LABEL)?;
    set_presence_window_frame(&input, input_handle, frame)?;
    input.show().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
async fn pet_input_set_interactive(window: WebviewWindow, interactive: bool) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    if interactive {
        window
            .set_focusable(true)
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
    }
}

#[tauri::command]
async fn pet_input_request_focus(window: WebviewWindow) -> Result<(), String> {
    authorize_pet_input_window(window.label())
        .map_err(|_| "Window is not authorized".to_owned())?;
    window
        .set_focusable(true)
        .map_err(|error| error.to_string())?;
    window
        .set_ignore_cursor_events(false)
        .map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
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

fn hide_pet_windows(app: &tauri::AppHandle) {
    for label in [PET_RENDER_LABEL, PET_INPUT_LABEL] {
        if let Some(window) = app.get_webview_window(label) {
            let _ = window.hide();
        }
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
    let enabled = preferences.pet_enabled && !pet_session_disabled(app);
    if !enabled {
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
    render.show().map_err(|error| error.to_string())
}

fn coordinator_config(preferences: &DesktopPreferences) -> PresenceCoordinatorConfig {
    PresenceCoordinatorConfig {
        reduced_motion: preferences.reduced_motion || !preferences.pet_motion_enabled,
        hover_enabled: preferences.pet_hover_enabled,
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

fn current_pet_placement(
    app: &tauri::AppHandle,
    native_windows: Option<PresenceNativeWindows>,
    previous: Option<PresenceWindowPlacement>,
) -> Result<PresenceWindowPlacement, String> {
    let render = app
        .get_webview_window(PET_RENDER_LABEL)
        .ok_or_else(|| "Pet render window is unavailable".to_owned())?;
    let render_handle = native_windows
        .ok_or_else(|| "Pet native windows are unavailable".to_owned())?
        .handle_for(PET_RENDER_LABEL)?;
    let frame = presence_window_frame(&render, render_handle)?;
    let monitors = presence_monitors_for_app(app)?;
    let provisional = previous.map_or(
        PhysicalPoint {
            x: frame
                .x
                .saturating_add(i32::try_from(frame.width / 2).unwrap_or(i32::MAX)),
            y: frame
                .y
                .saturating_add(i32::try_from(frame.height / 2).unwrap_or(i32::MAX)),
        },
        |placement| placement.anchor,
    );
    let monitor = monitor_for_anchor(&monitors, provisional)
        .ok_or_else(|| "No monitor is available".to_owned())?;
    Ok(resolve_presence_placement(
        frame,
        monitor.work_area,
        monitor.scale_factor,
        previous.map(|placement| placement.expansion_direction),
    ))
}

fn move_pet_window_group(
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
    let Some(input) = app.get_webview_window(PET_INPUT_LABEL) else {
        return move_presence_window_positions(
            &render,
            render_handle,
            placement.render_frame,
            None,
        );
    };
    let input_handle = native_windows.handle_for(PET_INPUT_LABEL)?;
    let input_size = presence_window_frame(&input, input_handle)?;
    let scale = placement.scale_factor.clamp(0.5, 4.0);
    let core_extent = (PET_CORE_EXTENT_LOGICAL * scale).round() as u32;
    let compact_width = (PET_INPUT_COMPACT_WIDTH_LOGICAL * scale).round() as u32;
    let compact_height = (PET_INPUT_COMPACT_HEIGHT_LOGICAL * scale).round() as u32;
    let expanded_width = (PET_INPUT_EXPANDED_WIDTH_LOGICAL * scale).round() as u32;
    let expanded_height = (PET_INPUT_EXPANDED_HEIGHT_LOGICAL * scale).round() as u32;
    let frame = if input_size.width == core_extent && input_size.height == core_extent {
        placement.core_frame(core_extent)
    } else if input_size.height > compact_height {
        placement.input_frame(expanded_width, expanded_height, compact_height)
    } else {
        placement.input_frame(compact_width, compact_height, compact_height)
    };
    move_presence_window_positions(
        &render,
        render_handle,
        placement.render_frame,
        Some((&input, input_handle, frame)),
    )
}

#[cfg(target_os = "windows")]
fn native_presence_hwnd(handle: PresenceNativeHandle) -> windows_sys::Win32::Foundation::HWND {
    handle as windows_sys::Win32::Foundation::HWND
}

#[cfg(target_os = "windows")]
fn native_presence_window_frame(handle: PresenceNativeHandle) -> Result<PhysicalFrame, String> {
    use windows_sys::Win32::Foundation::RECT;
    use windows_sys::Win32::UI::WindowsAndMessaging::GetWindowRect;

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

#[cfg(target_os = "windows")]
fn set_presence_window_frame(
    _window: &WebviewWindow,
    handle: PresenceNativeHandle,
    frame: PhysicalFrame,
) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{SetWindowPos, SWP_NOACTIVATE, SWP_NOZORDER};

    let width = i32::try_from(frame.width).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?;
    let height = i32::try_from(frame.height).map_err(|_| "PRESENCE_FRAME_INVALID".to_owned())?;
    if unsafe {
        SetWindowPos(
            native_presence_hwnd(handle),
            std::ptr::null_mut(),
            frame.x,
            frame.y,
            width,
            height,
            SWP_NOACTIVATE | SWP_NOZORDER,
        )
    } == 0
    {
        return Err("PRESENCE_FRAME_UPDATE_FAILED".to_owned());
    }
    Ok(())
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

#[cfg(target_os = "windows")]
fn move_presence_window_positions(
    _render: &WebviewWindow,
    render_handle: PresenceNativeHandle,
    render_frame: PhysicalFrame,
    input: Option<(&WebviewWindow, PresenceNativeHandle, PhysicalFrame)>,
) -> Result<(), String> {
    move_native_presence_window_positions(
        render_handle,
        render_frame,
        input.map(|(_window, handle, frame)| (handle, frame)),
    )
}

#[cfg(target_os = "windows")]
fn move_native_presence_window_positions(
    render_handle: PresenceNativeHandle,
    render_frame: PhysicalFrame,
    input: Option<(PresenceNativeHandle, PhysicalFrame)>,
) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        BeginDeferWindowPos, DeferWindowPos, EndDeferWindowPos, SWP_NOACTIVATE, SWP_NOSIZE,
        SWP_NOZORDER,
    };

    let render_hwnd = native_presence_hwnd(render_handle);
    let render_current = native_presence_window_frame(render_handle)?;
    let render_flags = SWP_NOACTIVATE
        | SWP_NOZORDER
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
                | SWP_NOZORDER
                | if current.width == frame.width && current.height == frame.height {
                    SWP_NOSIZE
                } else {
                    0
                };
            Ok::<_, String>((native_presence_hwnd(handle), frame, flags))
        })
        .transpose()?;
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
    let count = if input.is_some() { 2 } else { 1 };
    let mut batch = unsafe { BeginDeferWindowPos(count) };
    if batch.is_null() {
        return Err("PRESENCE_GROUP_MOVE_UNAVAILABLE".to_owned());
    }
    batch = unsafe {
        DeferWindowPos(
            batch,
            render_hwnd,
            std::ptr::null_mut(),
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
                    .emit_to(PET_INPUT_LABEL, "presence-input-requested", ())
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
    tauri::async_runtime::spawn_blocking(move || voice.health())
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
            Ok(()) => state.presence_startup.succeeded(),
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
    exclude_presence_windows_from_capture(native_windows)?;
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

    let (_, render) = ensure_presence_window(
        app,
        PET_RENDER_LABEL,
        "Fairy Presence Renderer",
        Duration::from_secs(12),
    )?;
    let (_, input) = ensure_presence_window(
        app,
        PET_INPUT_LABEL,
        "Fairy Presence Input",
        Duration::from_secs(12),
    )?;
    Ok(PresenceNativeWindows { render, input })
}

fn ensure_presence_window(
    app: &tauri::AppHandle,
    label: &str,
    title: &str,
    timeout: Duration,
) -> Result<(WebviewWindow, PresenceNativeHandle), Box<dyn std::error::Error>> {
    let window = app.get_webview_window(label).ok_or_else(|| {
        std::io::Error::other(format!(
            "Startup-created pet window is unavailable: {label}"
        ))
    })?;
    let policy = auxiliary_window_policy(label)
        .ok_or_else(|| std::io::Error::other("Missing pet window policy"))?;

    #[cfg(target_os = "windows")]
    let handle = wait_native_presence_handle(title, timeout)?;
    #[cfg(not(target_os = "windows"))]
    let handle = {
        let _ = (title, timeout);
    };

    window.set_ignore_cursor_events(policy.ignore_cursor_events)?;
    window.set_focusable(policy.focusable)?;
    Ok((window, handle))
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
fn exclude_presence_windows_from_capture(
    native_windows: PresenceNativeWindows,
) -> Result<(), String> {
    for handle in [native_windows.render, native_windows.input] {
        presence_backdrop::exclude_window_from_capture(handle)?;
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn exclude_presence_windows_from_capture(
    _native_windows: PresenceNativeWindows,
) -> Result<(), String> {
    Ok(())
}

pub fn run() {
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
            let warming_voice = Arc::clone(&voice);
            tauri::async_runtime::spawn_blocking(move || {
                let _ = warming_voice.health();
            });
            let preferences = DesktopPreferencesStore::new(&data_dir).load()?;
            let presence = PresenceCoordinatorHandle::new(coordinator_config(&preferences));
            app.manage(DesktopState {
                core: Arc::new(Mutex::new(None)),
                voice,
                preferences: Mutex::new(()),
                provider_update_in_progress: AtomicBool::new(false),
                data_dir,
                desktop_program,
                resource_dir,
                presence,
                presence_windows: Mutex::new(None),
                pet_drag: Mutex::new(None),
                renderer_supervisor: Mutex::new(PresenceRendererSupervisor::default()),
                presence_startup: PresenceStartupGate::default(),
                pet_placement_reconciled: AtomicBool::new(false),
                started_at: Instant::now(),
            });
            request_presence_initialization(app.handle().clone());
            let tray = build_fairy_tray(app, &preferences)?;
            app.manage(tray);
            sync_tray_preferences(app.handle(), &preferences);
            let core_app = app.handle().clone();
            tauri::async_runtime::spawn_blocking(move || {
                match CoreBridge::spawn_verified(launch) {
                    Ok(bridge) => {
                        let Some(state) = core_app.try_state::<DesktopState>() else {
                            return;
                        };
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
            desktop_preferences_get,
            desktop_preferences_update,
            pet_preferences_update,
            pet_input_set_layout,
            pet_input_set_interactive,
            pet_input_request_focus,
            pet_window_group_begin_drag,
            pet_window_group_move,
            pet_window_group_end_drag,
            pet_window_group_reset_position,
            pet_renderer_report_health,
            pet_exit,
            open_main_window,
            open_settings_window,
            voice_worker_health,
            voice_model_install,
            voice_session_start,
            voice_session_cancel,
            voice_test_start,
            voice_test_cancel,
            select_project_folder,
            capture::list_capture_surfaces,
            capture::capture_surface,
            presence_backdrop::pet_backdrop_capture
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Fairy desktop");
    application.run(|_, _| {});
}

#[cfg(all(test, target_os = "windows"))]
mod native_window_group_tests {
    use super::*;
    use windows_sys::Win32::Foundation::HWND;
    use windows_sys::Win32::UI::WindowsAndMessaging::{CreateWindowExW, DestroyWindow, WS_POPUP};

    struct TestWindow(HWND);

    impl Drop for TestWindow {
        fn drop(&mut self) {
            unsafe {
                DestroyWindow(self.0);
            }
        }
    }

    fn create_test_window(frame: PhysicalFrame) -> TestWindow {
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
    }
}
