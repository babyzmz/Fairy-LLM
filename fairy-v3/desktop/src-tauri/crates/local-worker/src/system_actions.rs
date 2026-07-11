use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use url::Url;

use crate::{WorkerError, WorkspaceManager};

const MAX_CLIPBOARD_CHARACTERS: usize = 32_768;
const MAX_NOTIFICATION_TITLE_CHARACTERS: usize = 80;
const MAX_NOTIFICATION_BODY_CHARACTERS: usize = 240;
const MAX_URL_BYTES: usize = 2_048;
const MAX_IDEMPOTENCY_KEY_BYTES: usize = 255;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum NotificationLevel {
    Info,
    Warning,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SettingsPage {
    Display,
    Microphone,
    Notifications,
    Sound,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum SystemAction {
    OpenUrl {
        url: String,
    },
    RevealPath {
        project_id: String,
        version_id: String,
        relative_path: String,
    },
    CopyText {
        text: String,
    },
    Notify {
        title: String,
        body: String,
        #[serde(default = "default_notification_level")]
        level: NotificationLevel,
    },
    OpenSettings {
        page: SettingsPage,
    },
}

impl SystemAction {
    fn action_type(&self) -> &'static str {
        match self {
            Self::OpenUrl { .. } => "open_url",
            Self::RevealPath { .. } => "reveal_path",
            Self::CopyText { .. } => "copy_text",
            Self::Notify { .. } => "notify",
            Self::OpenSettings { .. } => "open_settings",
        }
    }
}

fn default_notification_level() -> NotificationLevel {
    NotificationLevel::Info
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SystemActionRequest {
    pub idempotency_key: String,
    pub action: SystemAction,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct SystemActionResult {
    pub action_type: String,
    pub completed: bool,
    pub replayed: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ResolvedSystemAction {
    OpenUrl {
        url: String,
    },
    RevealPath {
        path: PathBuf,
    },
    CopyText {
        text: String,
    },
    Notify {
        title: String,
        body: String,
        level: NotificationLevel,
    },
    OpenSettings {
        page: SettingsPage,
    },
}

pub trait SystemActionBackend: Send + Sync {
    fn execute(&self, action: &ResolvedSystemAction) -> Result<(), SystemActionError>;
}

#[derive(Debug, Error)]
pub enum SystemActionError {
    #[error("invalid system action: {0}")]
    InvalidParams(String),
    #[error("system action path is outside the managed Version: {0}")]
    PathOutOfScope(String),
    #[error("system action Scope identity is invalid: {0}")]
    ScopeMismatch(String),
    #[error("system action idempotency key was reused with different input")]
    IdempotencyConflict,
    #[error("system action has an uncertain prior outcome and will not be repeated")]
    Interrupted,
    #[error("system action journal failed: {0}")]
    Journal(String),
    #[error("prior system action failed ({error_code})")]
    PriorFailure { error_code: String },
    #[error("{message}")]
    Native { error_code: String, message: String },
}

impl SystemActionError {
    pub fn error_code(&self) -> &str {
        match self {
            Self::InvalidParams(_) => "INVALID_PARAMS",
            Self::PathOutOfScope(_) => "PATH_OUT_OF_SCOPE",
            Self::ScopeMismatch(_) => "SCOPE_MISMATCH",
            Self::IdempotencyConflict => "IDEMPOTENCY_CONFLICT",
            Self::Interrupted | Self::Journal(_) => "WORKER_INTERRUPTED",
            Self::PriorFailure { error_code } | Self::Native { error_code, .. } => error_code,
        }
    }

    fn from_workspace(error: WorkerError) -> Self {
        match error {
            WorkerError::PathOutOfScope(value) => Self::PathOutOfScope(value),
            WorkerError::InvalidIdentifier(value) => Self::ScopeMismatch(value),
            other => Self::Journal(other.to_string()),
        }
    }
}

#[derive(Clone)]
pub struct SystemActionManager {
    workspace: WorkspaceManager,
    backend: Arc<dyn SystemActionBackend>,
    execution_lock: Arc<Mutex<()>>,
}

impl SystemActionManager {
    pub fn new(workspace: WorkspaceManager) -> Self {
        Self::with_backend(workspace, Arc::new(NativeSystemActionBackend))
    }

    pub fn with_backend(
        workspace: WorkspaceManager,
        backend: Arc<dyn SystemActionBackend>,
    ) -> Self {
        Self {
            workspace,
            backend,
            execution_lock: Arc::new(Mutex::new(())),
        }
    }

    pub fn execute(
        &self,
        request: SystemActionRequest,
    ) -> Result<SystemActionResult, SystemActionError> {
        validate_idempotency_key(&request.idempotency_key)?;
        validate_action(&request.action)?;
        let action_type = request.action.action_type();
        let action_fingerprint = fingerprint(&request.action)?;
        let record_key = digest_hex(request.idempotency_key.as_bytes());
        let _execution = self
            .execution_lock
            .lock()
            .map_err(|_error| SystemActionError::Interrupted)?;
        let journal = self.journal_directory()?;
        let records = RecordPaths::new(journal, &record_key);

        if let Some(result) = replay_existing(&records, &action_fingerprint, action_type)? {
            return result;
        }
        let resolved = self.resolve_action(&request.action)?;
        let prepared = ActionRecord::new(&action_fingerprint, action_type, RecordPhase::Prepared);
        match write_new_record(&records.prepared, &prepared) {
            Ok(()) => {}
            Err(SystemActionError::Journal(message)) if message.starts_with("already exists:") => {
                return replay_existing(&records, &action_fingerprint, action_type)?
                    .unwrap_or(Err(SystemActionError::Interrupted));
            }
            Err(error) => return Err(error),
        }

        if let Err(error) = self.backend.execute(&resolved) {
            let mut failed =
                ActionRecord::new(&action_fingerprint, action_type, RecordPhase::Failed);
            failed.error_code = Some(error.error_code().to_owned());
            write_new_record(&records.failed, &failed)?;
            return Err(error);
        }

        let completed = ActionRecord::new(&action_fingerprint, action_type, RecordPhase::Completed);
        write_new_record(&records.completed, &completed)?;
        Ok(SystemActionResult {
            action_type: action_type.to_owned(),
            completed: true,
            replayed: false,
        })
    }

    fn resolve_action(
        &self,
        action: &SystemAction,
    ) -> Result<ResolvedSystemAction, SystemActionError> {
        match action {
            SystemAction::OpenUrl { url } => Ok(ResolvedSystemAction::OpenUrl {
                url: url.to_owned(),
            }),
            SystemAction::RevealPath {
                project_id,
                version_id,
                relative_path,
            } => self
                .workspace
                .resolve_existing_path(project_id, version_id, relative_path)
                .map(|path| ResolvedSystemAction::RevealPath { path })
                .map_err(SystemActionError::from_workspace),
            SystemAction::CopyText { text } => Ok(ResolvedSystemAction::CopyText {
                text: text.to_owned(),
            }),
            SystemAction::Notify { title, body, level } => Ok(ResolvedSystemAction::Notify {
                title: title.to_owned(),
                body: body.to_owned(),
                level: *level,
            }),
            SystemAction::OpenSettings { page } => {
                Ok(ResolvedSystemAction::OpenSettings { page: *page })
            }
        }
    }

    fn journal_directory(&self) -> Result<PathBuf, SystemActionError> {
        let directory = self.workspace.managed_root().join(".system-actions");
        if directory.exists() {
            let metadata = fs::symlink_metadata(&directory)
                .map_err(|error| SystemActionError::Journal(error.to_string()))?;
            if is_link_or_reparse_point(&metadata) || !metadata.is_dir() {
                return Err(SystemActionError::Journal(
                    "system action journal path is not a managed directory".to_owned(),
                ));
            }
        } else {
            fs::create_dir_all(&directory)
                .map_err(|error| SystemActionError::Journal(error.to_string()))?;
        }
        Ok(directory)
    }
}

#[derive(Debug, Serialize, Deserialize)]
struct ActionRecord {
    schema_version: u32,
    action_fingerprint: String,
    action_type: String,
    phase: RecordPhase,
    error_code: Option<String>,
}

impl ActionRecord {
    fn new(action_fingerprint: &str, action_type: &str, phase: RecordPhase) -> Self {
        Self {
            schema_version: 1,
            action_fingerprint: action_fingerprint.to_owned(),
            action_type: action_type.to_owned(),
            phase,
            error_code: None,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum RecordPhase {
    Prepared,
    Completed,
    Failed,
}

struct RecordPaths {
    prepared: PathBuf,
    completed: PathBuf,
    failed: PathBuf,
}

impl RecordPaths {
    fn new(root: PathBuf, key: &str) -> Self {
        Self {
            prepared: root.join(format!("{key}.prepared.json")),
            completed: root.join(format!("{key}.completed.json")),
            failed: root.join(format!("{key}.failed.json")),
        }
    }
}

fn replay_existing(
    paths: &RecordPaths,
    action_fingerprint: &str,
    action_type: &str,
) -> Result<Option<Result<SystemActionResult, SystemActionError>>, SystemActionError> {
    if paths.completed.exists() {
        let record = read_record(&paths.completed)?;
        validate_record(
            &record,
            action_fingerprint,
            action_type,
            RecordPhase::Completed,
        )?;
        return Ok(Some(Ok(SystemActionResult {
            action_type: action_type.to_owned(),
            completed: true,
            replayed: true,
        })));
    }
    if paths.failed.exists() {
        let record = read_record(&paths.failed)?;
        validate_record(
            &record,
            action_fingerprint,
            action_type,
            RecordPhase::Failed,
        )?;
        return Ok(Some(Err(SystemActionError::PriorFailure {
            error_code: record
                .error_code
                .unwrap_or_else(|| "WORKER_INTERRUPTED".to_owned()),
        })));
    }
    if paths.prepared.exists() {
        let record = read_record(&paths.prepared)?;
        validate_record(
            &record,
            action_fingerprint,
            action_type,
            RecordPhase::Prepared,
        )?;
        return Ok(Some(Err(SystemActionError::Interrupted)));
    }
    Ok(None)
}

fn validate_record(
    record: &ActionRecord,
    action_fingerprint: &str,
    action_type: &str,
    phase: RecordPhase,
) -> Result<(), SystemActionError> {
    if record.action_fingerprint != action_fingerprint || record.action_type != action_type {
        return Err(SystemActionError::IdempotencyConflict);
    }
    if record.schema_version != 1 || record.phase != phase {
        return Err(SystemActionError::Interrupted);
    }
    Ok(())
}

fn read_record(path: &PathBuf) -> Result<ActionRecord, SystemActionError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| SystemActionError::Journal(error.to_string()))?;
    if is_link_or_reparse_point(&metadata) || !metadata.is_file() {
        return Err(SystemActionError::Interrupted);
    }
    let content = fs::read(path).map_err(|error| SystemActionError::Journal(error.to_string()))?;
    serde_json::from_slice(&content).map_err(|_error| SystemActionError::Interrupted)
}

fn write_new_record(path: &PathBuf, record: &ActionRecord) -> Result<(), SystemActionError> {
    let content = serde_json::to_vec(record)
        .map_err(|error| SystemActionError::Journal(error.to_string()))?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| {
            if error.kind() == io::ErrorKind::AlreadyExists {
                SystemActionError::Journal(format!("already exists: {}", path.display()))
            } else {
                SystemActionError::Journal(error.to_string())
            }
        })?;
    file.write_all(&content)
        .and_then(|()| file.sync_all())
        .map_err(|error| SystemActionError::Journal(error.to_string()))
}

fn validate_idempotency_key(value: &str) -> Result<(), SystemActionError> {
    if value.is_empty()
        || value.len() > MAX_IDEMPOTENCY_KEY_BYTES
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b':' | b'.' | b'_' | b'-'))
    {
        return Err(SystemActionError::InvalidParams(
            "idempotency key is invalid".to_owned(),
        ));
    }
    Ok(())
}

fn validate_action(action: &SystemAction) -> Result<(), SystemActionError> {
    match action {
        SystemAction::OpenUrl { url } => {
            if url.len() > MAX_URL_BYTES || url.chars().any(char::is_control) {
                return Err(SystemActionError::InvalidParams(
                    "URL is invalid".to_owned(),
                ));
            }
            let parsed = Url::parse(url)
                .map_err(|_error| SystemActionError::InvalidParams("URL is invalid".to_owned()))?;
            if parsed.scheme() != "https"
                || parsed.host_str().is_none()
                || !parsed.username().is_empty()
                || parsed.password().is_some()
            {
                return Err(SystemActionError::InvalidParams(
                    "URL must use HTTPS without credentials".to_owned(),
                ));
            }
        }
        SystemAction::RevealPath { relative_path, .. } => {
            if relative_path.len() > 1_024 {
                return Err(SystemActionError::PathOutOfScope(relative_path.to_owned()));
            }
        }
        SystemAction::CopyText { text } => {
            if text.is_empty()
                || text.chars().count() > MAX_CLIPBOARD_CHARACTERS
                || text.contains('\0')
            {
                return Err(SystemActionError::InvalidParams(
                    "clipboard text is invalid".to_owned(),
                ));
            }
        }
        SystemAction::Notify { title, body, .. } => {
            if title.is_empty()
                || body.is_empty()
                || title.chars().count() > MAX_NOTIFICATION_TITLE_CHARACTERS
                || body.chars().count() > MAX_NOTIFICATION_BODY_CHARACTERS
                || title.contains('\0')
                || body.contains('\0')
            {
                return Err(SystemActionError::InvalidParams(
                    "notification text is invalid".to_owned(),
                ));
            }
        }
        SystemAction::OpenSettings { .. } => {}
    }
    Ok(())
}

fn fingerprint(action: &SystemAction) -> Result<String, SystemActionError> {
    let serialized = serde_json::to_vec(action)
        .map_err(|error| SystemActionError::Journal(error.to_string()))?;
    Ok(digest_hex(&serialized))
}

fn digest_hex(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

#[cfg(target_os = "windows")]
fn is_link_or_reparse_point(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    metadata.file_type().is_symlink()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

#[cfg(not(target_os = "windows"))]
fn is_link_or_reparse_point(metadata: &fs::Metadata) -> bool {
    metadata.file_type().is_symlink()
}

struct NativeSystemActionBackend;

#[cfg(not(target_os = "windows"))]
impl SystemActionBackend for NativeSystemActionBackend {
    fn execute(&self, _action: &ResolvedSystemAction) -> Result<(), SystemActionError> {
        Err(SystemActionError::Native {
            error_code: "CAPABILITY_NOT_AVAILABLE".to_owned(),
            message: "typed system actions are available only on Windows".to_owned(),
        })
    }
}

#[cfg(target_os = "windows")]
impl SystemActionBackend for NativeSystemActionBackend {
    fn execute(&self, action: &ResolvedSystemAction) -> Result<(), SystemActionError> {
        windows_native::execute(action)
    }
}

#[cfg(target_os = "windows")]
mod windows_native {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use std::ptr::{copy_nonoverlapping, null, null_mut};

    use windows_sys::Win32::Foundation::GlobalFree;
    use windows_sys::Win32::System::DataExchange::{
        CloseClipboard, EmptyClipboard, OpenClipboard, SetClipboardData,
    };
    use windows_sys::Win32::System::Memory::{
        GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE,
    };
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, MB_ICONINFORMATION, MB_ICONWARNING, MB_OK, MB_SETFOREGROUND, SW_SHOWNORMAL,
    };

    use super::{NotificationLevel, ResolvedSystemAction, SettingsPage, SystemActionError};

    pub(super) fn execute(action: &ResolvedSystemAction) -> Result<(), SystemActionError> {
        match action {
            ResolvedSystemAction::OpenUrl { url } => open_target(url, None),
            ResolvedSystemAction::RevealPath { path } if path.is_dir() => {
                open_target(&path.to_string_lossy(), None)
            }
            ResolvedSystemAction::RevealPath { path } => {
                let parameters = format!("/select,\"{}\"", path.to_string_lossy());
                open_target("explorer.exe", Some(&parameters))
            }
            ResolvedSystemAction::CopyText { text } => copy_text(text),
            ResolvedSystemAction::Notify { title, body, level } => notify(title, body, *level),
            ResolvedSystemAction::OpenSettings { page } => {
                let target = match page {
                    SettingsPage::Display => "ms-settings:display",
                    SettingsPage::Microphone => "ms-settings:privacy-microphone",
                    SettingsPage::Notifications => "ms-settings:notifications",
                    SettingsPage::Sound => "ms-settings:sound",
                };
                open_target(target, None)
            }
        }
    }

    fn open_target(target: &str, parameters: Option<&str>) -> Result<(), SystemActionError> {
        let operation = wide(OsStr::new("open"));
        let target = wide(OsStr::new(target));
        let parameters = parameters.map(|value| wide(OsStr::new(value)));
        let parameter_pointer = parameters.as_ref().map_or(null(), |value| value.as_ptr());
        // SAFETY: all pointers reference NUL-terminated buffers for the duration of the call.
        let result = unsafe {
            ShellExecuteW(
                null_mut(),
                operation.as_ptr(),
                target.as_ptr(),
                parameter_pointer,
                null(),
                SW_SHOWNORMAL,
            )
        } as isize;
        if result <= 32 {
            return Err(native_error(
                "WORKER_INTERRUPTED",
                "Windows could not open the target",
            ));
        }
        Ok(())
    }

    fn copy_text(text: &str) -> Result<(), SystemActionError> {
        let content: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
        // SAFETY: the clipboard is closed by ClipboardGuard on every return path.
        if unsafe { OpenClipboard(null_mut()) } == 0 {
            return Err(native_error(
                "WORKER_INTERRUPTED",
                "Windows clipboard is unavailable",
            ));
        }
        let _clipboard = ClipboardGuard;
        // SAFETY: OpenClipboard succeeded for this thread.
        if unsafe { EmptyClipboard() } == 0 {
            return Err(native_error(
                "WORKER_INTERRUPTED",
                "Windows clipboard could not be cleared",
            ));
        }
        let bytes = content.len() * std::mem::size_of::<u16>();
        // SAFETY: allocation size is bounded by the validated clipboard input.
        let allocation = unsafe { GlobalAlloc(GMEM_MOVEABLE, bytes) };
        if allocation.is_null() {
            return Err(native_error(
                "WORKER_INTERRUPTED",
                "clipboard allocation failed",
            ));
        }
        // SAFETY: allocation is valid and remains owned by this function until SetClipboardData.
        let destination = unsafe { GlobalLock(allocation) }.cast::<u16>();
        if destination.is_null() {
            // SAFETY: SetClipboardData has not taken ownership.
            unsafe { GlobalFree(allocation) };
            return Err(native_error(
                "WORKER_INTERRUPTED",
                "clipboard allocation could not be locked",
            ));
        }
        // SAFETY: destination has exactly content.len() u16 slots.
        unsafe {
            copy_nonoverlapping(content.as_ptr(), destination, content.len());
            GlobalUnlock(allocation);
        }
        const CF_UNICODE_TEXT: u32 = 13;
        // SAFETY: successful SetClipboardData transfers ownership of allocation to Windows.
        if unsafe { SetClipboardData(CF_UNICODE_TEXT, allocation) }.is_null() {
            // SAFETY: ownership was not transferred after a failed SetClipboardData call.
            unsafe { GlobalFree(allocation) };
            return Err(native_error(
                "WORKER_INTERRUPTED",
                "clipboard data could not be set",
            ));
        }
        Ok(())
    }

    fn notify(title: &str, body: &str, level: NotificationLevel) -> Result<(), SystemActionError> {
        let title = wide(OsStr::new(title));
        let body = wide(OsStr::new(body));
        let style = MB_OK
            | MB_SETFOREGROUND
            | match level {
                NotificationLevel::Info => MB_ICONINFORMATION,
                NotificationLevel::Warning => MB_ICONWARNING,
            };
        std::thread::Builder::new()
            .name("fairy-system-notification".to_owned())
            .spawn(move || {
                // SAFETY: buffers are owned by the thread until the modal notification closes.
                unsafe { MessageBoxW(null_mut(), body.as_ptr(), title.as_ptr(), style) };
            })
            .map_err(|error| native_error("WORKER_INTERRUPTED", &error.to_string()))?;
        Ok(())
    }

    fn wide(value: &OsStr) -> Vec<u16> {
        value.encode_wide().chain(std::iter::once(0)).collect()
    }

    fn native_error(error_code: &str, message: &str) -> SystemActionError {
        SystemActionError::Native {
            error_code: error_code.to_owned(),
            message: message.to_owned(),
        }
    }

    struct ClipboardGuard;

    impl Drop for ClipboardGuard {
        fn drop(&mut self) {
            // SAFETY: ClipboardGuard is created only after OpenClipboard succeeds.
            unsafe { CloseClipboard() };
        }
    }
}
