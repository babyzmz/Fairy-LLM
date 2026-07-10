use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::ffi::OsStr;
use std::fs;
use std::io::{BufRead, Write};
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum WorkerError {
    #[error("I/O failure: {0}")]
    Io(#[from] std::io::Error),
    #[error("git command failed: {0}")]
    Git(String),
    #[error("path is outside the scoped version: {0}")]
    PathOutOfScope(String),
    #[error("identifier contains unsupported characters: {0}")]
    InvalidIdentifier(String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionWorkspace {
    pub project_id: String,
    pub version_id: String,
    pub root: PathBuf,
}

#[derive(Debug, Clone)]
pub struct WorkspaceManager {
    managed_root: PathBuf,
}

impl WorkspaceManager {
    pub fn new(managed_root: impl AsRef<Path>) -> Self {
        Self {
            managed_root: managed_root.as_ref().to_path_buf(),
        }
    }

    pub fn import_project(
        &self,
        source: impl AsRef<Path>,
        project_id: &str,
        version_id: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let source = source.as_ref().canonicalize()?;
        let project_root = self.project_root(project_id);
        let repository = project_root.join("repo.git");
        let seed = project_root.join("seed");
        let version_root = self.version_root(project_id, version_id);
        if repository.is_dir() && version_root.is_dir() {
            return Ok(VersionWorkspace {
                project_id: project_id.to_owned(),
                version_id: version_id.to_owned(),
                root: version_root.canonicalize()?,
            });
        }
        fs::create_dir_all(&seed)?;
        copy_tree(&source, &seed)?;
        self.initialize_seed(project_id, version_id, &seed, "Imported project")
    }

    pub fn create_empty(
        &self,
        project_id: &str,
        version_id: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let version_root = self.version_root(project_id, version_id);
        if version_root.is_dir() {
            return Ok(VersionWorkspace {
                project_id: project_id.to_owned(),
                version_id: version_id.to_owned(),
                root: version_root.canonicalize()?,
            });
        }
        let seed = self.project_root(project_id).join("seed");
        fs::create_dir_all(&seed)?;
        self.initialize_seed(project_id, version_id, &seed, "Created empty project")
    }

    fn initialize_seed(
        &self,
        project_id: &str,
        version_id: &str,
        seed: &Path,
        commit_message: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        let repository = self.repository_path(project_id);
        let version_root = self.version_root(project_id, version_id);
        run_git([OsStr::new("init"), seed.as_os_str()])?;
        run_git_in(seed, ["config", "user.name", "Fairy Core"])?;
        run_git_in(seed, ["config", "user.email", "fairy@localhost"])?;
        run_git_in(seed, ["config", "core.autocrlf", "false"])?;
        run_git_in(seed, ["add", "--all"])?;
        run_git_in(seed, ["commit", "--allow-empty", "-m", commit_message])?;

        run_git([
            OsStr::new("init"),
            OsStr::new("--bare"),
            repository.as_os_str(),
        ])?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("config"),
            OsStr::new("user.name"),
            OsStr::new("Fairy Core"),
        ])?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("config"),
            OsStr::new("user.email"),
            OsStr::new("fairy@localhost"),
        ])?;
        run_git_in(
            seed,
            [
                "remote",
                "add",
                "origin",
                repository.to_string_lossy().as_ref(),
            ],
        )?;
        let branch = branch_name(version_id);
        run_git_in(
            seed,
            ["push", "origin", &format!("HEAD:refs/heads/{branch}")],
        )?;
        fs::remove_dir_all(seed)?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("worktree"),
            OsStr::new("add"),
            version_root.as_os_str(),
            OsStr::new(&branch),
        ])?;
        Ok(VersionWorkspace {
            project_id: project_id.to_owned(),
            version_id: version_id.to_owned(),
            root: version_root.canonicalize()?,
        })
    }

    pub fn fork_version(
        &self,
        project_id: &str,
        parent_version_id: &str,
        version_id: &str,
    ) -> Result<VersionWorkspace, WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(parent_version_id)?;
        validate_identifier(version_id)?;
        let repository = self.repository_path(project_id);
        let version_root = self.version_root(project_id, version_id);
        if version_root.is_dir() {
            return Ok(VersionWorkspace {
                project_id: project_id.to_owned(),
                version_id: version_id.to_owned(),
                root: version_root.canonicalize()?,
            });
        }
        let branch = branch_name(version_id);
        let parent_branch = branch_name(parent_version_id);
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("worktree"),
            OsStr::new("add"),
            OsStr::new("-b"),
            OsStr::new(&branch),
            version_root.as_os_str(),
            OsStr::new(&parent_branch),
        ])?;
        Ok(VersionWorkspace {
            project_id: project_id.to_owned(),
            version_id: version_id.to_owned(),
            root: version_root.canonicalize()?,
        })
    }

    pub fn write_file(
        &self,
        project_id: &str,
        version_id: &str,
        relative_path: &str,
        content: &[u8],
    ) -> Result<PathBuf, WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let version_root = self.version_root(project_id, version_id).canonicalize()?;
        let relative = validate_relative_path(relative_path)?;
        let target = version_root.join(relative);
        let parent = target
            .parent()
            .ok_or_else(|| WorkerError::PathOutOfScope(relative_path.to_owned()))?;
        fs::create_dir_all(parent)?;
        let canonical_parent = parent.canonicalize()?;
        if !canonical_parent.starts_with(&version_root) {
            return Err(WorkerError::PathOutOfScope(relative_path.to_owned()));
        }
        fs::write(&target, content)?;
        Ok(target)
    }

    pub fn checkpoint(
        &self,
        project_id: &str,
        version_id: &str,
        message: &str,
    ) -> Result<String, WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let root = self.version_root(project_id, version_id).canonicalize()?;
        if run_git_in(&root, ["status", "--porcelain"])?
            .trim()
            .is_empty()
        {
            return Ok(run_git_in(&root, ["rev-parse", "HEAD"])?.trim().to_owned());
        }
        run_git_in(&root, ["add", "--all"])?;
        run_git_in(&root, ["commit", "-m", message])?;
        Ok(run_git_in(&root, ["rev-parse", "HEAD"])?.trim().to_owned())
    }

    pub fn create_scratch(
        &self,
        conversation_id: &str,
        task_id: &str,
    ) -> Result<PathBuf, WorkerError> {
        validate_identifier(conversation_id)?;
        validate_identifier(task_id)?;
        let root = self
            .managed_root
            .join("scratch")
            .join(conversation_id)
            .join(task_id);
        fs::create_dir_all(&root)?;
        Ok(root.canonicalize()?)
    }

    pub fn diff(&self, project_id: &str, version_id: &str) -> Result<String, WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let root = self.version_root(project_id, version_id).canonicalize()?;
        let status = run_git_in(&root, ["status", "--short"])?;
        let patch = run_git_in(&root, ["diff", "--no-ext-diff", "--binary", "HEAD"])?;
        Ok(format!("{status}{patch}"))
    }

    pub fn discard_version(&self, project_id: &str, version_id: &str) -> Result<(), WorkerError> {
        validate_identifier(project_id)?;
        validate_identifier(version_id)?;
        let repository = self.repository_path(project_id);
        let version_root = self.version_root(project_id, version_id);
        if !version_root.exists() {
            return Ok(());
        }
        let branch = branch_name(version_id);
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("worktree"),
            OsStr::new("remove"),
            OsStr::new("--force"),
            version_root.as_os_str(),
        ])?;
        run_git([
            OsStr::new("--git-dir"),
            repository.as_os_str(),
            OsStr::new("branch"),
            OsStr::new("-D"),
            OsStr::new(&branch),
        ])?;
        Ok(())
    }

    fn project_root(&self, project_id: &str) -> PathBuf {
        self.managed_root.join("projects").join(project_id)
    }

    fn repository_path(&self, project_id: &str) -> PathBuf {
        self.project_root(project_id).join("repo.git")
    }

    fn version_root(&self, project_id: &str, version_id: &str) -> PathBuf {
        self.project_root(project_id)
            .join("versions")
            .join(version_id)
    }
}

#[derive(Debug, Deserialize)]
struct WorkerRequest {
    jsonrpc: String,
    id: Value,
    method: String,
    #[serde(default)]
    params: Value,
}

#[derive(Debug, Deserialize)]
struct ImportParams {
    source: PathBuf,
    project_id: String,
    version_id: String,
}

#[derive(Debug, Deserialize)]
struct EmptyProjectParams {
    project_id: String,
    version_id: String,
}

#[derive(Debug, Deserialize)]
struct ScratchParams {
    conversation_id: String,
    task_id: String,
}

#[derive(Debug, Deserialize)]
struct VersionParams {
    project_id: String,
    version_id: String,
}

#[derive(Debug, Deserialize)]
struct ForkParams {
    project_id: String,
    parent_version_id: String,
    version_id: String,
}

#[derive(Debug, Deserialize)]
struct WriteTextParams {
    project_id: String,
    version_id: String,
    relative_path: String,
    content: String,
}

#[derive(Debug, Deserialize)]
struct CheckpointParams {
    project_id: String,
    version_id: String,
    message: String,
}

#[derive(Debug)]
struct ProtocolError {
    rpc_code: i32,
    error_code: &'static str,
    message: String,
}

impl From<WorkerError> for ProtocolError {
    fn from(error: WorkerError) -> Self {
        let error_code = match error {
            WorkerError::PathOutOfScope(_) => "PATH_OUT_OF_SCOPE",
            WorkerError::InvalidIdentifier(_) => "SCOPE_MISMATCH",
            WorkerError::Io(_) | WorkerError::Git(_) => "WORKER_INTERRUPTED",
        };
        Self {
            rpc_code: -32000,
            error_code,
            message: error.to_string(),
        }
    }
}

pub fn dispatch_request(manager: &WorkspaceManager, request: Value) -> Value {
    let parsed = serde_json::from_value::<WorkerRequest>(request);
    let request = match parsed {
        Ok(request) if request.jsonrpc == "2.0" => request,
        Ok(_) => {
            return protocol_error(
                Value::Null,
                ProtocolError {
                    rpc_code: -32600,
                    error_code: "INVALID_REQUEST",
                    message: "jsonrpc must be 2.0".to_owned(),
                },
            )
        }
        Err(error) => {
            return protocol_error(
                Value::Null,
                ProtocolError {
                    rpc_code: -32600,
                    error_code: "INVALID_REQUEST",
                    message: error.to_string(),
                },
            )
        }
    };
    let id = request.id.clone();
    let result = execute_method(manager, &request.method, request.params);
    match result {
        Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
        Err(error) => protocol_error(id, error),
    }
}

pub fn process_stream(
    manager: &WorkspaceManager,
    source: impl BufRead,
    mut destination: impl Write,
) -> Result<(), std::io::Error> {
    for line in source.lines() {
        let response = match serde_json::from_str::<Value>(&line?) {
            Ok(request) => dispatch_request(manager, request),
            Err(error) => protocol_error(
                Value::Null,
                ProtocolError {
                    rpc_code: -32700,
                    error_code: "PARSE_ERROR",
                    message: error.to_string(),
                },
            ),
        };
        serde_json::to_writer(&mut destination, &response)?;
        destination.write_all(b"\n")?;
        destination.flush()?;
    }
    Ok(())
}

pub fn run_stdio(managed_root: impl AsRef<Path>) -> Result<(), WorkerError> {
    let manager = WorkspaceManager::new(managed_root);
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    process_stream(&manager, stdin.lock(), stdout.lock())?;
    Ok(())
}

fn execute_method(
    manager: &WorkspaceManager,
    method: &str,
    params: Value,
) -> Result<Value, ProtocolError> {
    match method {
        "workspace.create_empty" => {
            let params: EmptyProjectParams = parse_params(params)?;
            let workspace = manager.create_empty(&params.project_id, &params.version_id)?;
            Ok(json!({"root": workspace.root}))
        }
        "workspace.import" => {
            let params: ImportParams = parse_params(params)?;
            let workspace =
                manager.import_project(params.source, &params.project_id, &params.version_id)?;
            Ok(json!({"root": workspace.root}))
        }
        "workspace.fork" => {
            let params: ForkParams = parse_params(params)?;
            let workspace = manager.fork_version(
                &params.project_id,
                &params.parent_version_id,
                &params.version_id,
            )?;
            Ok(json!({"root": workspace.root}))
        }
        "workspace.write_text" => {
            let params: WriteTextParams = parse_params(params)?;
            let path = manager.write_file(
                &params.project_id,
                &params.version_id,
                &params.relative_path,
                params.content.as_bytes(),
            )?;
            Ok(json!({"path": path}))
        }
        "workspace.checkpoint" => {
            let params: CheckpointParams = parse_params(params)?;
            let commit =
                manager.checkpoint(&params.project_id, &params.version_id, &params.message)?;
            Ok(json!({"commit": commit}))
        }
        "workspace.create_scratch" => {
            let params: ScratchParams = parse_params(params)?;
            let root = manager.create_scratch(&params.conversation_id, &params.task_id)?;
            Ok(json!({"root": root}))
        }
        "workspace.diff" => {
            let params: VersionParams = parse_params(params)?;
            let diff = manager.diff(&params.project_id, &params.version_id)?;
            Ok(json!({"diff": diff}))
        }
        "workspace.discard" => {
            let params: VersionParams = parse_params(params)?;
            manager.discard_version(&params.project_id, &params.version_id)?;
            Ok(json!({"discarded": true}))
        }
        _ => Err(ProtocolError {
            rpc_code: -32601,
            error_code: "METHOD_NOT_FOUND",
            message: format!("unknown worker method: {method}"),
        }),
    }
}

fn parse_params<T: DeserializeOwned>(params: Value) -> Result<T, ProtocolError> {
    serde_json::from_value(params).map_err(|error| ProtocolError {
        rpc_code: -32602,
        error_code: "INVALID_PARAMS",
        message: error.to_string(),
    })
}

fn protocol_error(id: Value, error: ProtocolError) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": error.rpc_code,
            "message": error.message,
            "data": {"error_code": error.error_code}
        }
    })
}

fn validate_identifier(value: &str) -> Result<(), WorkerError> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(WorkerError::InvalidIdentifier(value.to_owned()));
    }
    Ok(())
}

fn validate_relative_path(value: &str) -> Result<PathBuf, WorkerError> {
    if value.is_empty() || value.contains('\0') || value.contains(':') {
        return Err(WorkerError::PathOutOfScope(value.to_owned()));
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_) | Component::CurDir))
    {
        return Err(WorkerError::PathOutOfScope(value.to_owned()));
    }
    Ok(path.to_path_buf())
}

fn copy_tree(source: &Path, destination: &Path) -> Result<(), WorkerError> {
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let name = entry.file_name();
        if matches!(
            name.to_string_lossy().as_ref(),
            ".git" | ".venv" | "node_modules" | "target"
        ) {
            continue;
        }
        let file_type = entry.file_type()?;
        if file_type.is_symlink() {
            return Err(WorkerError::PathOutOfScope(
                entry.path().display().to_string(),
            ));
        }
        let target = destination.join(&name);
        if file_type.is_dir() {
            fs::create_dir_all(&target)?;
            copy_tree(&entry.path(), &target)?;
        } else if file_type.is_file() {
            fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

fn branch_name(version_id: &str) -> String {
    format!("version/{version_id}")
}

fn run_git<I, S>(arguments: I) -> Result<String, WorkerError>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let output = Command::new("git").args(arguments).output()?;
    if !output.status.success() {
        return Err(WorkerError::Git(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn run_git_in<const N: usize>(
    directory: &Path,
    arguments: [&str; N],
) -> Result<String, WorkerError> {
    let output = Command::new("git")
        .arg("-C")
        .arg(directory)
        .args(arguments)
        .output()?;
    if !output.status.success() {
        return Err(WorkerError::Git(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}
