use std::env;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use fairy_core_bridge::{CoreBridge, CoreBridgeError, CoreLaunchSpec};
use serde_json::{json, Value};
use tauri::ipc::{Channel, Response};
use tauri::{Emitter, Manager, State, WebviewWindow};
use tauri_plugin_dialog::{DialogExt, FilePath};

use desktop_preferences::{
    DesktopPreferences, DesktopPreferencesError, DesktopPreferencesStore, DesktopPreferencesUpdate,
};
use provider_configuration::{openrouter_profiles_json, ProviderConfigurationStore};
use provider_credentials::ProviderCredentialStore;
use voice_worker::{
    bundled_voice_launch, development_voice_launch, prepared_test_session, PreparedVoiceSession,
    VoiceStreamEvent, VoiceStreamInput, VoiceWorkerManager,
};

pub mod capture;
pub mod desktop_preferences;
pub mod provider_configuration;
pub mod provider_credentials;
pub mod voice_worker;

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
    if ["main", "settings", "pet"].contains(&label) {
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
            | "skills.list"
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
        "pet" => Some(AuxiliaryWindowPolicy {
            ignore_cursor_events: false,
            focusable: true,
        }),
        "guide" => Some(AuxiliaryWindowPolicy {
            ignore_cursor_events: true,
            focusable: false,
        }),
        _ => None,
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
    data_dir: PathBuf,
    desktop_program: PathBuf,
    resource_dir: PathBuf,
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
    model_id: String,
}

#[derive(serde::Serialize)]
struct OpenRouterStatus {
    configured: bool,
    model_id: Option<String>,
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
        model_id: configuration.map(|value| value.model_id),
    })
}

#[tauri::command]
async fn provider_openrouter_configure(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: OpenRouterConfigureInput,
) -> Result<OpenRouterStatus, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let credentials = ProviderCredentialStore::new(&state.data_dir);
    let configurations = ProviderConfigurationStore::new(&state.data_dir);
    let configuration = configurations
        .save_openrouter(&input.model_id)
        .map_err(|error| error.to_string())?;
    credentials
        .save_openrouter(&input.api_key)
        .map_err(|error| error.to_string())?;
    restart_core(&state).map_err(|error| error.to_string())?;
    Ok(OpenRouterStatus {
        configured: true,
        model_id: Some(configuration.model_id),
    })
}

#[tauri::command]
async fn provider_openrouter_delete(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<OpenRouterStatus, String> {
    authorize_settings_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    ProviderCredentialStore::new(&state.data_dir)
        .delete_openrouter()
        .map_err(|error| error.to_string())?;
    ProviderConfigurationStore::new(&state.data_dir)
        .delete_openrouter()
        .map_err(|error| error.to_string())?;
    restart_core(&state).map_err(|error| error.to_string())?;
    Ok(OpenRouterStatus {
        configured: false,
        model_id: None,
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
    app.emit("desktop-preferences-changed", &next)
        .map_err(|error| error.to_string())?;
    Ok(next)
}

#[tauri::command]
async fn open_settings_window(window: WebviewWindow) -> Result<(), String> {
    if !["main", "pet", "settings"].contains(&window.label()) {
        return Err("Window is not authorized".to_owned());
    }
    let settings = window
        .app_handle()
        .get_webview_window("settings")
        .ok_or_else(|| "Settings window is unavailable".to_owned())?;
    settings.show().map_err(|error| error.to_string())?;
    settings.unminimize().map_err(|error| error.to_string())?;
    settings.set_focus().map_err(|error| error.to_string())
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
            "id": "native-voice-start",
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
            "id": "native-voice-cancel",
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
    let secret = ProviderCredentialStore::new(data_dir).load_openrouter()?;
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

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let desktop_program = std::env::current_exe()?;
            let resource_dir = app.path().resource_dir()?;
            let launch = configured_core_launch(&data_dir, &desktop_program, &resource_dir)
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            let bridge = CoreBridge::spawn_verified(launch)?;
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
            app.manage(DesktopState {
                core: Arc::new(Mutex::new(Some(bridge))),
                voice,
                preferences: Mutex::new(()),
                data_dir,
                desktop_program,
                resource_dir,
            });
            for label in ["pet", "guide"] {
                let Some(policy) = auxiliary_window_policy(label) else {
                    continue;
                };
                let Some(window) = app.get_webview_window(label) else {
                    continue;
                };
                window.set_ignore_cursor_events(policy.ignore_cursor_events)?;
                window.set_focusable(policy.focusable)?;
                if label == "guide" {
                    window.show()?;
                }
            }
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
            open_settings_window,
            voice_worker_health,
            voice_model_install,
            voice_session_start,
            voice_session_cancel,
            voice_test_start,
            voice_test_cancel,
            select_project_folder,
            capture::list_capture_surfaces,
            capture::capture_surface
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Fairy desktop");
}
