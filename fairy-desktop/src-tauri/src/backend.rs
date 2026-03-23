use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::SystemTime;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8000;
const DESKTOP_BRIDGE_HOST: &str = "127.0.0.1";
const DESKTOP_BRIDGE_PORT: u16 = 8527;
const READY_TIMEOUT: Duration = Duration::from_secs(15);
const POLL_INTERVAL: Duration = Duration::from_millis(250);
const HTTP_TIMEOUT: Duration = Duration::from_secs(3);

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct BackendLifecycleEvent {
    pub status: String,
    pub url: String,
    pub message: String,
    pub pid: Option<u32>,
    pub timestamp_ms: u64,
}

#[derive(Default)]
pub struct BackendProcess {
    pub child: Mutex<Option<Child>>,
    pub lifecycle: Mutex<BackendLifecycleEvent>,
    pub events: Mutex<Vec<BackendLifecycleEvent>>,
    pub bridge_started: Mutex<bool>,
}

#[derive(Clone, Serialize)]
pub struct DesktopSystemState {
    pub bridge_status: String,
    pub backend_url: String,
    pub bridge_message: String,
    pub pid: Option<u32>,
    pub bridge_events: Vec<BackendLifecycleEvent>,
    pub runtime_state: Value,
}

#[derive(Clone, Serialize)]
pub struct BackendControlResponse {
    pub action: String,
    pub ok: bool,
    pub message: String,
    pub detail: Value,
    pub system_state: DesktopSystemState,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct DesktopActionRequest {
    pub action: String,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct DesktopActionResponse {
    pub status: String,
    pub action: String,
    pub message: String,
    #[serde(default)]
    pub data: Value,
}

pub fn start_desktop_action_bridge(app: &AppHandle) {
    let state = app.state::<BackendProcess>();
    {
        let mut started = state.bridge_started.lock().expect("desktop bridge mutex poisoned");
        if *started {
            return;
        }
        *started = true;
    }

    let app_handle = app.clone();
    thread::spawn(move || {
        let listener = match TcpListener::bind((DESKTOP_BRIDGE_HOST, DESKTOP_BRIDGE_PORT)) {
            Ok(listener) => listener,
            Err(error) => {
                append_desktop_action_log(
                    "desktop_action://failed",
                    "bridge_bind",
                    "error",
                    &format!("Failed to bind desktop action bridge: {error}"),
                    &json!({}),
                );
                return;
            }
        };
        let _ = listener.set_nonblocking(true);

        loop {
            match listener.accept() {
                Ok((stream, _)) => handle_desktop_bridge_request(&app_handle, stream),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(100));
                }
                Err(error) => {
                    append_desktop_action_log(
                        "desktop_action://failed",
                        "bridge_accept",
                        "error",
                        &format!("Desktop action bridge accept failed: {error}"),
                        &json!({}),
                    );
                    thread::sleep(Duration::from_millis(250));
                }
            }
        }
    });
}

pub fn initialize_backend(app: &AppHandle) {
    let backend_test_mode = env::var("FAIRY_BACKEND_TEST_MODE").unwrap_or_default();
    if backend_test_mode.is_empty() && is_backend_ready() {
        println!("fairy-desktop backend already running");
        emit_event(
            app,
            "backend://reused",
            BackendLifecycleEvent {
                status: "reused".into(),
                url: backend_url(),
                message: "Detected an already-running backend.".into(),
                pid: None,
                timestamp_ms: now_ms(),
            },
        );
        emit_event(
            app,
            "backend://ready",
            BackendLifecycleEvent {
                status: "ready".into(),
                url: backend_url(),
                message: "Using existing backend process.".into(),
                pid: None,
                timestamp_ms: now_ms(),
            },
        );
        return;
    }

    let project_root = project_root();
    let python = resolve_python(&project_root);
    println!("fairy-desktop spawning backend via {:?}", python);
    emit_event(
        app,
        "backend://starting",
        BackendLifecycleEvent {
            status: "starting".into(),
            url: backend_url(),
            message: format!("Spawning backend via {:?}", python),
            pid: None,
            timestamp_ms: now_ms(),
        },
    );

    let mut command = if backend_test_mode == "spawn_fail" {
        Command::new("__fairy_missing_python__")
    } else {
        Command::new(&python)
    };
    if backend_test_mode == "timeout" {
        command.args(["-c", "import time; time.sleep(30)"]);
    } else if backend_test_mode != "spawn_fail" {
        command.args([
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            BACKEND_HOST,
            "--port",
            &BACKEND_PORT.to_string(),
        ]);
    }
    command
        .current_dir(&project_root)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    match command.spawn() {
        Ok(child) => {
            let pid = child.id();
            {
                let state = app.state::<BackendProcess>();
                *state.child.lock().expect("backend child mutex poisoned") = Some(child);
            }
            start_monitor(app.clone());
            if wait_for_backend_ready(app, pid) {
                emit_event(
                    app,
                    "backend://ready",
                    BackendLifecycleEvent {
                        status: "ready".into(),
                        url: backend_url(),
                        message: "Spawned local backend successfully.".into(),
                        pid: Some(pid),
                        timestamp_ms: now_ms(),
                    },
                );
            } else {
                emit_event(
                    app,
                    "backend://timeout",
                    BackendLifecycleEvent {
                        status: "timeout".into(),
                        url: backend_url(),
                        message: "Backend startup timed out before the health probe became ready.".into(),
                        pid: Some(pid),
                        timestamp_ms: now_ms(),
                    },
                );
                emit_event(
                    app,
                    "backend://spawn-failed",
                    BackendLifecycleEvent {
                        status: "spawn_failed".into(),
                        url: backend_url(),
                        message: "Backend did not become ready within the startup timeout.".into(),
                        pid: Some(pid),
                        timestamp_ms: now_ms(),
                    },
                );
                shutdown_backend(app);
            }
        }
        Err(error) => {
            emit_event(
                app,
                "backend://spawn-failed",
                BackendLifecycleEvent {
                    status: "spawn_failed".into(),
                    url: backend_url(),
                    message: format!("Failed to spawn backend: {error}"),
                    pid: None,
                    timestamp_ms: now_ms(),
                },
            );
        }
    }
}

pub fn shutdown_backend(app: &AppHandle) {
    let state = app.state::<BackendProcess>();
    let mut guard = state.child.lock().expect("backend child mutex poisoned");
    if let Some(mut child) = guard.take() {
        println!("fairy-desktop stopping backend child pid={}", child.id());
        let _ = child.kill();
        let _ = child.wait();
        emit_event(
            app,
            "backend://stopped",
            BackendLifecycleEvent {
                status: "stopped".into(),
                url: backend_url(),
                message: "Backend process terminated by desktop shell.".into(),
                pid: Some(child.id()),
                timestamp_ms: now_ms(),
            },
        );
    }
}

#[tauri::command]
pub fn system_action_execute(
    app: AppHandle,
    action: String,
    payload: Option<Value>,
) -> Result<DesktopActionResponse, String> {
    Ok(execute_desktop_action(&app, &action, payload.unwrap_or_else(|| json!({}))))
}

#[tauri::command]
pub fn system_state(app: AppHandle) -> Result<DesktopSystemState, String> {
    Ok(build_system_state(&app))
}

#[tauri::command]
pub fn backend_control(
    app: AppHandle,
    action: String,
    request_id: Option<String>,
) -> Result<BackendControlResponse, String> {
    let normalized = action.trim().to_string();
    if normalized.is_empty() {
        return Err("action is required".into());
    }

    let response = match normalized.as_str() {
        "restart_backend" => {
            shutdown_backend(&app);
            initialize_backend(&app);
            BackendControlResponse {
                action: normalized,
                ok: is_backend_ready(),
                message: "Restarted backend through desktop bridge.".into(),
                detail: json!({}),
                system_state: build_system_state(&app),
            }
        }
        "cancel_current_request" | "clear_asset_cache" | "refresh_capabilities" => {
            if !is_backend_ready() {
                return Ok(BackendControlResponse {
                    action: normalized,
                    ok: false,
                    message: "Backend is not ready.".into(),
                    detail: json!({}),
                    system_state: build_system_state(&app),
                });
            }
            let runtime_response = post_runtime_action(&normalized, request_id)?;
            let ok = runtime_response.get("ok").and_then(Value::as_bool).unwrap_or(false);
            let message = runtime_response
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let detail = runtime_response.get("detail").cloned().unwrap_or_else(|| json!({}));
            BackendControlResponse {
                action: normalized,
                ok,
                message,
                detail,
                system_state: build_system_state(&app),
            }
        }
        _ => BackendControlResponse {
            action: normalized,
            ok: false,
            message: "Unsupported backend control action.".into(),
            detail: json!({}),
            system_state: build_system_state(&app),
        },
    };

    Ok(response)
}

fn build_system_state(app: &AppHandle) -> DesktopSystemState {
    let state = app.state::<BackendProcess>();
    let lifecycle = state
        .lifecycle
        .lock()
        .expect("backend lifecycle mutex poisoned")
        .clone();
    let bridge_events = state
        .events
        .lock()
        .expect("backend events mutex poisoned")
        .clone();
    let runtime_state = if is_backend_ready() {
        fetch_runtime_state().unwrap_or_else(|error| {
            json!({
                "backend_status": "error",
                "active_session": null,
                "active_stream_request": null,
                "is_streaming": false,
                "last_error": error,
                "capabilities": {},
                "recent_events": [],
            })
        })
    } else {
        json!({
            "backend_status": lifecycle.status,
            "active_session": null,
            "active_stream_request": null,
            "is_streaming": false,
            "last_error": lifecycle.message,
            "capabilities": {},
            "recent_events": [],
        })
    };

    DesktopSystemState {
        bridge_status: if lifecycle.status.is_empty() { "unknown".into() } else { lifecycle.status },
        backend_url: if lifecycle.url.is_empty() { backend_url() } else { lifecycle.url },
        bridge_message: lifecycle.message,
        pid: lifecycle.pid,
        bridge_events,
        runtime_state,
    }
}

fn start_monitor(app: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(1));
        let maybe_status = {
            let state = app.state::<BackendProcess>();
            let mut guard = state.child.lock().expect("backend child mutex poisoned");
            if let Some(child) = guard.as_mut() {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        let pid = child.id();
                        *guard = None;
                        Some((pid, status.code()))
                    }
                    Ok(None) => None,
                    Err(_) => None,
                }
            } else {
                return;
            }
        };

        if let Some((pid, code)) = maybe_status {
            emit_event(
                &app,
                "backend://stopped",
                BackendLifecycleEvent {
                    status: "stopped".into(),
                    url: backend_url(),
                    message: format!("Backend process exited. code={:?}", code),
                    pid: Some(pid),
                    timestamp_ms: now_ms(),
                },
            );
            return;
        }
    });
}

fn wait_for_backend_ready(app: &AppHandle, pid: u32) -> bool {
    let started = Instant::now();
    while started.elapsed() < READY_TIMEOUT {
        if is_backend_ready() {
            println!("fairy-desktop backend ready pid={}", pid);
            return true;
        }
        let stopped = {
            let state = app.state::<BackendProcess>();
            let mut guard = state.child.lock().expect("backend child mutex poisoned");
            if let Some(child) = guard.as_mut() {
                matches!(child.try_wait(), Ok(Some(_)))
            } else {
                true
            }
        };
        if stopped {
            return false;
        }
        thread::sleep(POLL_INTERVAL);
    }
    false
}

fn handle_desktop_bridge_request(app: &AppHandle, stream: TcpStream) {
    let mut reader = BufReader::new(stream);
    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() {
        return;
    }
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or_default().to_string();
    let path = parts.next().unwrap_or_default().to_string();

    let mut content_length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).is_err() {
            return;
        }
        if line == "\r\n" || line == "\n" || line.is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            if name.eq_ignore_ascii_case("content-length") {
                content_length = value.trim().parse::<usize>().unwrap_or(0);
            }
        }
    }

    let mut body = vec![0u8; content_length];
    if content_length > 0 && reader.read_exact(&mut body).is_err() {
        return;
    }

    let response = if method.eq_ignore_ascii_case("POST") && path == "/bridge/system_action" {
        match serde_json::from_slice::<DesktopActionRequest>(&body) {
            Ok(request) => execute_desktop_action(app, &request.action, request.payload),
            Err(error) => DesktopActionResponse {
                status: "error".into(),
                action: "unknown".into(),
                message: format!("Invalid desktop action payload: {error}"),
                data: json!({}),
            },
        }
    } else {
        DesktopActionResponse {
            status: "error".into(),
            action: "unknown".into(),
            message: "Unsupported desktop bridge route.".into(),
            data: json!({}),
        }
    };

    let status_line = if response.status == "ok" {
        "HTTP/1.1 200 OK"
    } else {
        "HTTP/1.1 400 Bad Request"
    };
    let body_text = serde_json::to_string(&response).unwrap_or_else(|_| "{}".to_string());
    let writer = reader.get_mut();
    let reply = format!(
        "{status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body_text.len(),
        body_text,
    );
    let _ = writer.write_all(reply.as_bytes());
    let _ = writer.flush();
}

fn execute_desktop_action(app: &AppHandle, action: &str, payload: Value) -> DesktopActionResponse {
    append_desktop_action_log(
        "desktop_action://executing",
        action,
        "executing",
        "Executing desktop action.",
        &payload,
    );
    let response = execute_desktop_action_inner(app, action, payload);
    let event_name = if response.status == "ok" {
        "desktop_action://success"
    } else {
        "desktop_action://failed"
    };
    append_desktop_action_log(
        event_name,
        &response.action,
        &response.status,
        &response.message,
        &response.data,
    );
    let _ = app.emit(event_name, &response);
    response
}

fn execute_desktop_action_inner(app: &AppHandle, action: &str, payload: Value) -> DesktopActionResponse {
    let normalized = action.trim();
    let headless = env::var("FAIRY_HEADLESS_E2E")
        .map(|value| matches!(value.trim(), "1" | "true" | "TRUE" | "True"))
        .unwrap_or(false);

    match normalized {
        "show_notification" => {
            let message = payload
                .get("message")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .unwrap_or("Fairy notification")
                .to_string();
            let _ = app.emit(
                "desktop://notification",
                json!({"message": message, "timestamp_ms": now_ms()}),
            );
            DesktopActionResponse {
                status: "ok".into(),
                action: normalized.into(),
                message,
                data: json!({"headless": headless}),
            }
        }
        "focus_window" => {
            if headless {
                return DesktopActionResponse {
                    status: "ok".into(),
                    action: normalized.into(),
                    message: "Focused the main window (headless simulation).".into(),
                    data: json!({"headless": true}),
                };
            }
            if let Some(window) = resolve_main_window(app) {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
                DesktopActionResponse {
                    status: "ok".into(),
                    action: normalized.into(),
                    message: "Focused the main window.".into(),
                    data: json!({}),
                }
            } else {
                DesktopActionResponse {
                    status: "error".into(),
                    action: normalized.into(),
                    message: "No main window is available to focus.".into(),
                    data: json!({}),
                }
            }
        }
        "open_panel" => {
            let panel = payload
                .get("panel")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .unwrap_or("system")
                .to_string();
            if !headless {
                if let Some(window) = resolve_main_window(app) {
                    let _ = window.show();
                    let _ = window.unminimize();
                    let _ = window.set_focus();
                }
            }
            let _ = app.emit(
                "desktop://open-panel",
                json!({"panel": panel, "timestamp_ms": now_ms()}),
            );
            DesktopActionResponse {
                status: "ok".into(),
                action: normalized.into(),
                message: format!("Opened the {panel} panel."),
                data: json!({"panel": panel, "headless": headless}),
            }
        }
        "reveal_asset_folder" => {
            let folder = project_root().join("data").join("asset_cache");
            let _ = std::fs::create_dir_all(&folder);
            let spawn_result = Command::new("explorer.exe")
                .arg(folder.to_string_lossy().to_string())
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();
            match spawn_result {
                Ok(_) => DesktopActionResponse {
                    status: "ok".into(),
                    action: normalized.into(),
                    message: "Opened the asset cache folder.".into(),
                    data: json!({"path": folder}),
                },
                Err(error) => DesktopActionResponse {
                    status: "error".into(),
                    action: normalized.into(),
                    message: format!("Failed to open the asset cache folder: {error}"),
                    data: json!({"path": folder}),
                },
            }
        }
        "restart_backend" => {
            let app_handle = app.clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_millis(300));
                shutdown_backend(&app_handle);
                initialize_backend(&app_handle);
            });
            DesktopActionResponse {
                status: "ok".into(),
                action: normalized.into(),
                message: "Backend restart scheduled.".into(),
                data: json!({}),
            }
        }
        _ => DesktopActionResponse {
            status: "error".into(),
            action: normalized.into(),
            message: "Unsupported desktop action.".into(),
            data: json!({}),
        },
    }
}

fn resolve_main_window(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    app.get_webview_window("main")
        .or_else(|| app.webview_windows().values().next().cloned())
}

fn emit_event(app: &AppHandle, name: &str, payload: BackendLifecycleEvent) {
    {
        let state = app.state::<BackendProcess>();
        *state
            .lifecycle
            .lock()
            .expect("backend lifecycle mutex poisoned") = payload.clone();
        let mut events = state.events.lock().expect("backend events mutex poisoned");
        events.push(payload.clone());
        if events.len() > 40 {
            let overflow = events.len() - 40;
            events.drain(0..overflow);
        }
    }
    append_lifecycle_log(name, &payload);
    let _ = app.emit(name, payload);
}

fn is_backend_ready() -> bool {
    let address: SocketAddr = format!("{BACKEND_HOST}:{BACKEND_PORT}")
        .parse()
        .expect("invalid backend socket address");
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

fn fetch_runtime_state() -> Result<Value, String> {
    http_json_request("GET", "/system/state", None)
}

fn post_runtime_action(action: &str, request_id: Option<String>) -> Result<Value, String> {
    let payload = match request_id {
        Some(value) if !value.trim().is_empty() => json!({"action": action, "payload": {"request_id": value}}),
        _ => json!({"action": action, "payload": {}}),
    };
    http_json_request("POST", "/system/actions", Some(payload))
}

fn http_json_request(method: &str, path: &str, body: Option<Value>) -> Result<Value, String> {
    let address = format!("{BACKEND_HOST}:{BACKEND_PORT}");
    let mut stream = TcpStream::connect(&address).map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(HTTP_TIMEOUT))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(HTTP_TIMEOUT))
        .map_err(|error| error.to_string())?;

    let body_text = body.map(|value| value.to_string()).unwrap_or_default();
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {length}\r\n\r\n{body}",
        host = address,
        length = body_text.len(),
        body = body_text,
    );
    stream
        .write_all(request.as_bytes())
        .and_then(|_| stream.flush())
        .map_err(|error| error.to_string())?;

    let mut response = String::new();
    stream.read_to_string(&mut response).map_err(|error| error.to_string())?;
    let (status_code, payload) = parse_http_response(&response)?;
    if !(200..300).contains(&status_code) {
        return Err(
            payload
                .get("errors")
                .and_then(Value::as_array)
                .and_then(|items| items.first())
                .and_then(|item| item.get("message"))
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| format!("HTTP {status_code}")),
        );
    }
    Ok(payload)
}

fn parse_http_response(raw: &str) -> Result<(u16, Value), String> {
    let (head, body) = raw
        .split_once("\r\n\r\n")
        .ok_or_else(|| "Invalid HTTP response".to_string())?;
    let status_line = head.lines().next().ok_or_else(|| "Missing HTTP status line".to_string())?;
    let status_code = status_line
        .split_whitespace()
        .nth(1)
        .ok_or_else(|| "Missing HTTP status code".to_string())?
        .parse::<u16>()
        .map_err(|error| error.to_string())?;
    let payload = if body.trim().is_empty() {
        json!({})
    } else {
        serde_json::from_str::<Value>(body).map_err(|error| error.to_string())?
    };
    Ok((status_code, payload))
}

fn resolve_python(project_root: &Path) -> String {
    if let Ok(value) = env::var("FAIRY_PYTHON") {
        if !value.trim().is_empty() {
            return value;
        }
    }

    let venv_python = project_root.join("cosyvoice_env").join("Scripts").join("python.exe");
    if venv_python.exists() {
        return venv_python.to_string_lossy().to_string();
    }
    "python".to_string()
}

fn project_root() -> PathBuf {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    root.canonicalize().unwrap_or(root)
}

fn backend_url() -> String {
    format!("http://{BACKEND_HOST}:{BACKEND_PORT}")
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|value| value.as_millis() as u64)
        .unwrap_or(0)
}

fn append_lifecycle_log(event_name: &str, payload: &BackendLifecycleEvent) {
    let Ok(path_value) = env::var("FAIRY_LIFECYCLE_LOG_PATH") else {
        return;
    };
    let trimmed = path_value.trim();
    if trimmed.is_empty() {
        return;
    }
    let path = PathBuf::from(trimmed);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut entry = serde_json::to_value(payload).unwrap_or_else(|_| json!({}));
    if let Some(object) = entry.as_object_mut() {
        object.insert("event".into(), Value::String(event_name.to_string()));
    }
    if let Ok(mut handle) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(handle, "{}", entry);
    }
}

fn append_desktop_action_log(event_name: &str, action: &str, status: &str, message: &str, data: &Value) {
    let Ok(path_value) = env::var("FAIRY_LIFECYCLE_LOG_PATH") else {
        return;
    };
    let trimmed = path_value.trim();
    if trimmed.is_empty() {
        return;
    }
    let path = PathBuf::from(trimmed);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let entry = json!({
        "event": event_name,
        "action": action,
        "status": status,
        "message": message,
        "data": data,
        "timestamp_ms": now_ms(),
    });
    if let Ok(mut handle) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(handle, "{}", entry);
    }
}
