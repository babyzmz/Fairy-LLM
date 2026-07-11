use std::collections::HashMap;
use std::fs::{self, File, Metadata};
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Sender, TryRecvError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use percent_encoding::percent_decode_str;
use serde::{Deserialize, Serialize};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};

use crate::{validate_identifier, WorkerError};

const LOOPBACK_HOST: &str = "127.0.0.1";
const SERVER_POLL_INTERVAL: Duration = Duration::from_millis(25);
#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StaticPreviewRequest {
    pub preview_id: String,
    pub project_id: String,
    pub version_id: String,
    pub entry_path: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PreviewServerState {
    Running,
    Stopped,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StaticPreviewInfo {
    pub executor_handle: String,
    pub host: String,
    pub port: u16,
    pub url: String,
    pub state: PreviewServerState,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PreviewBinding {
    project_id: String,
    version_id: String,
    entry_path: String,
}

struct PreviewRecord {
    binding: PreviewBinding,
    info: StaticPreviewInfo,
    stop: Option<Sender<()>>,
    thread: Option<JoinHandle<()>>,
    alive: Arc<AtomicBool>,
}

struct PreviewRegistry {
    managed_root: PathBuf,
    records: Mutex<HashMap<String, PreviewRecord>>,
}

impl Drop for PreviewRegistry {
    fn drop(&mut self) {
        let records = self
            .records
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        for (_preview_id, mut record) in records.drain() {
            if let Some(stop) = record.stop.take() {
                let _ignored = stop.send(());
            }
            if let Some(thread) = record.thread.take() {
                let _ignored = thread.join();
            }
        }
    }
}

#[derive(Clone)]
pub struct StaticPreviewManager {
    registry: Arc<PreviewRegistry>,
}

impl StaticPreviewManager {
    pub fn new(managed_root: impl AsRef<Path>) -> Self {
        Self {
            registry: Arc::new(PreviewRegistry {
                managed_root: managed_root.as_ref().to_path_buf(),
                records: Mutex::new(HashMap::new()),
            }),
        }
    }

    pub fn start_static(
        &self,
        request: StaticPreviewRequest,
    ) -> Result<StaticPreviewInfo, WorkerError> {
        validate_identifier(&request.preview_id)?;
        validate_identifier(&request.project_id)?;
        validate_identifier(&request.version_id)?;
        if request.entry_path != "index.html" {
            return Err(WorkerError::PathOutOfScope(request.entry_path));
        }
        let binding = PreviewBinding {
            project_id: request.project_id.clone(),
            version_id: request.version_id.clone(),
            entry_path: request.entry_path.clone(),
        };
        let mut records = self.lock_records()?;
        if let Some(existing) = records.get(&request.preview_id) {
            if existing.binding != binding {
                return Err(WorkerError::PreviewScopeMismatch(request.preview_id));
            }
            if existing.info.state == PreviewServerState::Running {
                if existing.alive.load(Ordering::Acquire) {
                    return Ok(existing.info.clone());
                }
                return Err(WorkerError::PreviewUnavailable(request.preview_id));
            }
        }
        records.remove(&request.preview_id);

        let root = resolve_version_root(
            &self.registry.managed_root,
            &request.project_id,
            &request.version_id,
        )?;
        let entry = Path::new(&request.entry_path);
        if open_scoped_file(&root, entry)?.is_none() {
            return Err(WorkerError::PathOutOfScope(request.entry_path));
        }

        let server = Server::http((LOOPBACK_HOST, 0))
            .map_err(|error| WorkerError::PreviewServer(error.to_string()))?;
        let address = server
            .server_addr()
            .to_ip()
            .ok_or_else(|| WorkerError::PreviewServer("listener is not TCP".to_owned()))?;
        if !address.ip().is_loopback() || address.ip().to_string() != LOOPBACK_HOST {
            return Err(WorkerError::PreviewServer(
                "listener did not bind exact IPv4 loopback".to_owned(),
            ));
        }
        let port = address.port();
        let info = StaticPreviewInfo {
            executor_handle: format!("static:{}", request.preview_id),
            host: LOOPBACK_HOST.to_owned(),
            port,
            url: format!("http://{LOOPBACK_HOST}:{port}/{}/", request.preview_id),
            state: PreviewServerState::Running,
        };
        let (stop_sender, stop_receiver) = mpsc::channel();
        let alive = Arc::new(AtomicBool::new(true));
        let thread_alive = Arc::clone(&alive);
        let thread_root = root.clone();
        let thread_entry = request.entry_path.clone();
        let thread_preview_id = request.preview_id.clone();
        let server_thread = thread::Builder::new()
            .name(format!("fairy-preview-{}", request.preview_id))
            .spawn(move || {
                loop {
                    match stop_receiver.try_recv() {
                        Ok(()) | Err(TryRecvError::Disconnected) => break,
                        Err(TryRecvError::Empty) => {}
                    }
                    match server.recv_timeout(SERVER_POLL_INTERVAL) {
                        Ok(Some(request)) => {
                            serve_request(request, &thread_root, &thread_entry, &thread_preview_id);
                        }
                        Ok(None) => {}
                        Err(_error) => break,
                    }
                }
                thread_alive.store(false, Ordering::Release);
            })?;
        records.insert(
            request.preview_id,
            PreviewRecord {
                binding,
                info: info.clone(),
                stop: Some(stop_sender),
                thread: Some(server_thread),
                alive,
            },
        );
        Ok(info)
    }

    pub fn status(&self, preview_id: &str) -> Result<StaticPreviewInfo, WorkerError> {
        validate_identifier(preview_id)?;
        let records = self.lock_records()?;
        let record = records
            .get(preview_id)
            .ok_or_else(|| WorkerError::PreviewUnavailable(preview_id.to_owned()))?;
        if record.info.state == PreviewServerState::Running && !record.alive.load(Ordering::Acquire)
        {
            return Err(WorkerError::PreviewUnavailable(preview_id.to_owned()));
        }
        Ok(record.info.clone())
    }

    pub fn stop(&self, preview_id: &str) -> Result<bool, WorkerError> {
        validate_identifier(preview_id)?;
        let (stop, server_thread) = {
            let mut records = self.lock_records()?;
            let record = records
                .get_mut(preview_id)
                .ok_or_else(|| WorkerError::PreviewUnavailable(preview_id.to_owned()))?;
            if record.info.state == PreviewServerState::Stopped {
                return Ok(true);
            }
            (record.stop.take(), record.thread.take())
        };
        if let Some(stop) = stop {
            let _ignored = stop.send(());
        }
        if let Some(server_thread) = server_thread {
            server_thread.join().map_err(|_panic| {
                WorkerError::PreviewServer("Preview server thread panicked".to_owned())
            })?;
        }
        let mut records = self.lock_records()?;
        let record = records
            .get_mut(preview_id)
            .ok_or_else(|| WorkerError::PreviewUnavailable(preview_id.to_owned()))?;
        record.info.state = PreviewServerState::Stopped;
        record.alive.store(false, Ordering::Release);
        Ok(true)
    }

    fn lock_records(&self) -> Result<MutexGuard<'_, HashMap<String, PreviewRecord>>, WorkerError> {
        self.registry
            .records
            .lock()
            .map_err(|_error| WorkerError::LockPoisoned)
    }
}

fn serve_request(request: Request, root: &Path, entry_path: &str, preview_id: &str) {
    if !matches!(request.method(), Method::Get | Method::Head) {
        let mut response = Response::empty(StatusCode(405));
        response.add_header(header("Allow", "GET, HEAD"));
        add_security_headers(&mut response, false);
        let _ignored = request.respond(response);
        return;
    }
    let relative = match request_path(request.url(), entry_path, preview_id) {
        Ok(Some(relative)) => relative,
        Ok(None) => {
            respond_empty(request, StatusCode(404));
            return;
        }
        Err(_error) => {
            respond_empty(request, StatusCode(400));
            return;
        }
    };
    match open_scoped_file(root, &relative) {
        Ok(Some((file, canonical))) => {
            let is_html = canonical
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| extension.eq_ignore_ascii_case("html"));
            let media_type = mime_guess::from_path(&canonical).first_or_octet_stream();
            let mut response = Response::from_file(file);
            response.add_header(header("Content-Type", media_type.essence_str()));
            add_security_headers(&mut response, is_html);
            let _ignored = request.respond(response);
        }
        Ok(None) => respond_empty(request, StatusCode(404)),
        Err(WorkerError::PathOutOfScope(_)) => respond_empty(request, StatusCode(400)),
        Err(_error) => respond_empty(request, StatusCode(500)),
    }
}

fn respond_empty(request: Request, status: StatusCode) {
    let mut response = Response::empty(status);
    add_security_headers(&mut response, false);
    let _ignored = request.respond(response);
}

fn add_security_headers<R: Read>(response: &mut Response<R>, is_html: bool) {
    response.add_header(header("X-Content-Type-Options", "nosniff"));
    response.add_header(header("Referrer-Policy", "no-referrer"));
    response.add_header(header(
        "Content-Security-Policy",
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self'; \
         font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; \
         object-src 'none'; base-uri 'none'; form-action 'none'; \
         frame-ancestors http://tauri.localhost tauri://localhost http://127.0.0.1:1430; \
         navigate-to 'none'",
    ));
    response.add_header(header(
        "Cache-Control",
        if is_html {
            "no-store"
        } else {
            "private, max-age=60"
        },
    ));
}

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).expect("static ASCII header")
}

fn request_path(
    url: &str,
    entry_path: &str,
    preview_id: &str,
) -> Result<Option<PathBuf>, WorkerError> {
    let raw_path = url.split(['?', '#']).next().unwrap_or(url);
    validate_percent_encoding(raw_path)?;
    let decoded = percent_decode_str(raw_path)
        .decode_utf8()
        .map_err(|_error| WorkerError::PathOutOfScope(raw_path.to_owned()))?;
    if decoded.contains('\0') || decoded.contains('\\') || decoded.contains(':') {
        return Err(WorkerError::PathOutOfScope(decoded.into_owned()));
    }
    if decoded.starts_with("//") {
        return Err(WorkerError::PathOutOfScope(decoded.into_owned()));
    }
    let prefix = format!("/{preview_id}");
    let relative = if decoded == "/" || decoded == prefix || decoded == format!("{prefix}/") {
        entry_path
    } else if let Some(scoped) = decoded.strip_prefix(&format!("{prefix}/")) {
        scoped
    } else {
        decoded.strip_prefix('/').unwrap_or(&decoded)
    };
    if relative.is_empty() || relative.ends_with('/') {
        return Ok(None);
    }
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(WorkerError::PathOutOfScope(relative.to_owned()));
    }
    Ok(Some(path.to_path_buf()))
}

fn validate_percent_encoding(value: &str) -> Result<(), WorkerError> {
    let bytes = value.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' {
            if index + 2 >= bytes.len()
                || !bytes[index + 1].is_ascii_hexdigit()
                || !bytes[index + 2].is_ascii_hexdigit()
            {
                return Err(WorkerError::PathOutOfScope(value.to_owned()));
            }
            index += 3;
        } else {
            index += 1;
        }
    }
    Ok(())
}

fn resolve_version_root(
    managed_root: &Path,
    project_id: &str,
    version_id: &str,
) -> Result<PathBuf, WorkerError> {
    let managed_root = managed_root.canonicalize()?;
    let projects_root = managed_root.join("projects");
    let project_root = projects_root.join(project_id);
    let versions_root = project_root.join("versions");
    let requested_root = versions_root.join(version_id);
    for directory in [
        &projects_root,
        &project_root,
        &versions_root,
        &requested_root,
    ] {
        let metadata = fs::symlink_metadata(directory)?;
        reject_link_metadata(&metadata, directory)?;
        if !metadata.is_dir() {
            return Err(WorkerError::PathOutOfScope(directory.display().to_string()));
        }
    }
    let versions_root = versions_root.canonicalize()?;
    let requested_root = requested_root.canonicalize()?;
    if requested_root.parent() != Some(versions_root.as_path()) {
        return Err(WorkerError::PathOutOfScope(
            requested_root.display().to_string(),
        ));
    }
    Ok(requested_root)
}

fn open_scoped_file(root: &Path, relative: &Path) -> Result<Option<(File, PathBuf)>, WorkerError> {
    let mut target = root.to_path_buf();
    for component in relative.components() {
        if !matches!(component, Component::Normal(_)) {
            return Err(WorkerError::PathOutOfScope(relative.display().to_string()));
        }
        target.push(component.as_os_str());
        let metadata = match fs::symlink_metadata(&target) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        reject_link_metadata(&metadata, &target)?;
    }
    let metadata = fs::metadata(&target)?;
    if !metadata.is_file() {
        return Ok(None);
    }
    let canonical = target.canonicalize()?;
    if !canonical.starts_with(root) {
        return Err(WorkerError::PathOutOfScope(relative.display().to_string()));
    }
    let file = File::open(&canonical)?;
    let opened_metadata = file.metadata()?;
    let rechecked = target.canonicalize()?;
    let path_metadata = fs::metadata(&rechecked)?;
    if rechecked != canonical || !same_file_snapshot(&opened_metadata, &path_metadata) {
        return Err(WorkerError::PathOutOfScope(relative.display().to_string()));
    }
    Ok(Some((file, canonical)))
}

fn reject_link_metadata(metadata: &Metadata, path: &Path) -> Result<(), WorkerError> {
    if metadata.file_type().is_symlink() || is_windows_reparse_point(metadata) {
        return Err(WorkerError::PathOutOfScope(path.display().to_string()));
    }
    Ok(())
}

#[cfg(windows)]
fn is_windows_reparse_point(metadata: &Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;

    metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

#[cfg(not(windows))]
fn is_windows_reparse_point(_metadata: &Metadata) -> bool {
    false
}

#[cfg(windows)]
fn same_file_snapshot(left: &Metadata, right: &Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;

    left.file_size() == right.file_size()
        && left.creation_time() == right.creation_time()
        && left.last_write_time() == right.last_write_time()
}

#[cfg(unix)]
fn same_file_snapshot(left: &Metadata, right: &Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;

    left.dev() == right.dev() && left.ino() == right.ino()
}

#[cfg(not(any(windows, unix)))]
fn same_file_snapshot(left: &Metadata, right: &Metadata) -> bool {
    left.len() == right.len() && left.modified().ok() == right.modified().ok()
}
