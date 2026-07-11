use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::preview::{StaticPreviewManager, StaticPreviewRequest};
use crate::system_actions::{SystemActionError, SystemActionManager, SystemActionRequest};
use crate::workspace::{FileMutationParams, WorkspaceManager};
use crate::WorkerError;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
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
struct ApplyChangesetParams {
    project_id: String,
    version_id: String,
    mutations: Vec<FileMutationParams>,
}

#[derive(Debug, Deserialize)]
struct CheckpointParams {
    project_id: String,
    version_id: String,
    message: String,
}

#[derive(Debug, Deserialize)]
struct PreviewIdParams {
    preview_id: String,
}

#[derive(Debug)]
struct ProtocolError {
    rpc_code: i32,
    error_code: String,
    message: String,
}

impl From<WorkerError> for ProtocolError {
    fn from(error: WorkerError) -> Self {
        let error_code = match error {
            WorkerError::PathOutOfScope(_) => "PATH_OUT_OF_SCOPE",
            WorkerError::InvalidIdentifier(_) | WorkerError::PreviewScopeMismatch(_) => {
                "SCOPE_MISMATCH"
            }
            WorkerError::Io(_)
            | WorkerError::Git(_)
            | WorkerError::ChangesetRollback { .. }
            | WorkerError::ChangesetJournal(_)
            | WorkerError::LockPoisoned
            | WorkerError::PreviewUnavailable(_)
            | WorkerError::PreviewServer(_) => "WORKER_INTERRUPTED",
        };
        Self {
            rpc_code: -32000,
            error_code: error_code.to_owned(),
            message: error.to_string(),
        }
    }
}

impl From<SystemActionError> for ProtocolError {
    fn from(error: SystemActionError) -> Self {
        Self {
            rpc_code: -32000,
            error_code: error.error_code().to_owned(),
            message: error.to_string(),
        }
    }
}

#[derive(Clone)]
pub struct LocalWorker {
    workspace: WorkspaceManager,
    previews: StaticPreviewManager,
    system_actions: SystemActionManager,
}

impl LocalWorker {
    pub fn new(workspace: WorkspaceManager) -> Self {
        let previews = StaticPreviewManager::new(workspace.managed_root());
        let system_actions = SystemActionManager::new(workspace.clone());
        Self {
            workspace,
            previews,
            system_actions,
        }
    }

    pub fn with_system_actions(
        workspace: WorkspaceManager,
        system_actions: SystemActionManager,
    ) -> Self {
        let previews = StaticPreviewManager::new(workspace.managed_root());
        Self {
            workspace,
            previews,
            system_actions,
        }
    }
}

pub fn dispatch_request(manager: &WorkspaceManager, request: Value) -> Value {
    dispatch_worker_request(&LocalWorker::new(manager.clone()), request)
}

pub fn dispatch_worker_request(worker: &LocalWorker, request: Value) -> Value {
    let parsed = serde_json::from_value::<WorkerRequest>(request);
    let request = match parsed {
        Ok(request) if request.jsonrpc == "2.0" => request,
        Ok(_) => {
            return protocol_error(
                Value::Null,
                ProtocolError {
                    rpc_code: -32600,
                    error_code: "INVALID_REQUEST".to_owned(),
                    message: "jsonrpc must be 2.0".to_owned(),
                },
            )
        }
        Err(error) => {
            return protocol_error(
                Value::Null,
                ProtocolError {
                    rpc_code: -32600,
                    error_code: "INVALID_REQUEST".to_owned(),
                    message: error.to_string(),
                },
            )
        }
    };
    let id = request.id.clone();
    let result = execute_method(worker, &request.method, request.params);
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
    let worker = LocalWorker::new(manager.clone());
    for line in source.lines() {
        let response = match serde_json::from_str::<Value>(&line?) {
            Ok(request) => dispatch_worker_request(&worker, request),
            Err(error) => protocol_error(
                Value::Null,
                ProtocolError {
                    rpc_code: -32700,
                    error_code: "PARSE_ERROR".to_owned(),
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
    worker: &LocalWorker,
    method: &str,
    params: Value,
) -> Result<Value, ProtocolError> {
    match method {
        "workspace.create_empty" => {
            let params: EmptyProjectParams = parse_params(params)?;
            let workspace = worker
                .workspace
                .create_empty(&params.project_id, &params.version_id)?;
            Ok(json!({"root": workspace.root}))
        }
        "workspace.import" => {
            let params: ImportParams = parse_params(params)?;
            let workspace = worker.workspace.import_project(
                params.source,
                &params.project_id,
                &params.version_id,
            )?;
            Ok(json!({"root": workspace.root}))
        }
        "workspace.fork" => {
            let params: ForkParams = parse_params(params)?;
            let workspace = worker.workspace.fork_version(
                &params.project_id,
                &params.parent_version_id,
                &params.version_id,
            )?;
            Ok(json!({"root": workspace.root}))
        }
        "workspace.write_text" => {
            let params: WriteTextParams = parse_params(params)?;
            let path = worker.workspace.write_file(
                &params.project_id,
                &params.version_id,
                &params.relative_path,
                params.content.as_bytes(),
            )?;
            Ok(json!({"path": path}))
        }
        "workspace.apply_changeset" => {
            let params: ApplyChangesetParams = parse_params(params)?;
            let paths = worker.workspace.apply_changeset(
                &params.project_id,
                &params.version_id,
                &params.mutations,
            )?;
            Ok(json!({"paths": paths}))
        }
        "workspace.checkpoint" => {
            let params: CheckpointParams = parse_params(params)?;
            let commit = worker.workspace.checkpoint(
                &params.project_id,
                &params.version_id,
                &params.message,
            )?;
            Ok(json!({"commit": commit}))
        }
        "workspace.create_scratch" => {
            let params: ScratchParams = parse_params(params)?;
            let root = worker
                .workspace
                .create_scratch(&params.conversation_id, &params.task_id)?;
            Ok(json!({"root": root}))
        }
        "workspace.diff" => {
            let params: VersionParams = parse_params(params)?;
            let diff = worker
                .workspace
                .diff(&params.project_id, &params.version_id)?;
            Ok(json!({"diff": diff}))
        }
        "workspace.discard" => {
            let params: VersionParams = parse_params(params)?;
            worker
                .workspace
                .discard_version(&params.project_id, &params.version_id)?;
            Ok(json!({"discarded": true}))
        }
        "preview.start_static" => {
            let params: StaticPreviewRequest = parse_params(params)?;
            let info = worker.previews.start_static(params)?;
            Ok(serde_json::to_value(info).expect("serializable Preview info"))
        }
        "preview.status" => {
            let params: PreviewIdParams = parse_params(params)?;
            let info = worker.previews.status(&params.preview_id)?;
            Ok(serde_json::to_value(info).expect("serializable Preview info"))
        }
        "preview.stop" => {
            let params: PreviewIdParams = parse_params(params)?;
            worker.previews.stop(&params.preview_id)?;
            Ok(json!({"stopped": true}))
        }
        "system.execute" => {
            let params: SystemActionRequest = parse_params(params)?;
            let result = worker.system_actions.execute(params)?;
            Ok(serde_json::to_value(result).expect("serializable system action result"))
        }
        _ => Err(ProtocolError {
            rpc_code: -32601,
            error_code: "METHOD_NOT_FOUND".to_owned(),
            message: format!("unknown worker method: {method}"),
        }),
    }
}

fn parse_params<T: DeserializeOwned>(params: Value) -> Result<T, ProtocolError> {
    serde_json::from_value(params).map_err(|error| ProtocolError {
        rpc_code: -32602,
        error_code: "INVALID_PARAMS".to_owned(),
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
