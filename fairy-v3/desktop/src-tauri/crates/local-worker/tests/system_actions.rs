use std::sync::{Arc, Mutex};

use fairy_local_worker::system_actions::{
    ResolvedSystemAction, SystemActionBackend, SystemActionError, SystemActionManager,
};
use fairy_local_worker::{dispatch_worker_request, LocalWorker, WorkspaceManager};
use serde_json::{json, Value};
use tempfile::tempdir;

#[derive(Default)]
struct RecordingBackend {
    actions: Mutex<Vec<ResolvedSystemAction>>,
    fail_with: Mutex<Option<&'static str>>,
}

impl SystemActionBackend for RecordingBackend {
    fn execute(&self, action: &ResolvedSystemAction) -> Result<(), SystemActionError> {
        self.actions
            .lock()
            .expect("actions lock")
            .push(action.clone());
        if let Some(code) = *self.fail_with.lock().expect("failure lock") {
            return Err(SystemActionError::Native {
                error_code: code.to_owned(),
                message: "fixture failure".to_owned(),
            });
        }
        Ok(())
    }
}

fn fixture() -> (tempfile::TempDir, LocalWorker, Arc<RecordingBackend>) {
    let root = tempdir().expect("tempdir");
    let workspace = WorkspaceManager::new(root.path());
    let backend = Arc::new(RecordingBackend::default());
    let actions = SystemActionManager::with_backend(workspace.clone(), backend.clone());
    let worker = LocalWorker::with_system_actions(workspace, actions);
    (root, worker, backend)
}

fn call(worker: &LocalWorker, id: u64, params: Value) -> Value {
    dispatch_worker_request(
        worker,
        json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": "system.execute",
            "params": params,
        }),
    )
}

#[test]
fn protocol_executes_each_idempotency_key_once_and_replays_prior_result() {
    let (root, worker, backend) = fixture();
    let params = json!({
        "idempotency_key": "system:copy:one",
        "action": {"type": "copy_text", "text": "hello"},
    });

    let first = call(&worker, 1, params.clone());
    let restarted_workspace = WorkspaceManager::new(root.path());
    let restarted_actions =
        SystemActionManager::with_backend(restarted_workspace.clone(), backend.clone());
    let restarted = LocalWorker::with_system_actions(restarted_workspace, restarted_actions);
    let replay = call(&restarted, 2, params);

    assert_eq!(first["result"]["completed"], true);
    assert_eq!(first["result"]["replayed"], false);
    assert_eq!(replay["result"]["completed"], true);
    assert_eq!(replay["result"]["replayed"], true);
    assert_eq!(backend.actions.lock().expect("actions lock").len(), 1);
}

#[test]
fn same_key_with_different_action_is_rejected_without_a_second_effect() {
    let (_root, worker, backend) = fixture();
    let first = call(
        &worker,
        1,
        json!({
            "idempotency_key": "system:conflict",
            "action": {"type": "copy_text", "text": "first"},
        }),
    );
    assert_eq!(first["result"]["completed"], true);
    let conflict = call(
        &worker,
        2,
        json!({
            "idempotency_key": "system:conflict",
            "action": {"type": "copy_text", "text": "second"},
        }),
    );

    assert_eq!(
        conflict["error"]["data"]["error_code"],
        "IDEMPOTENCY_CONFLICT"
    );
    assert_eq!(backend.actions.lock().expect("actions lock").len(), 1);
}

#[test]
fn reveal_path_resolves_a_managed_identity_and_rejects_escape() {
    let (_root, worker, backend) = fixture();
    let created = dispatch_worker_request(
        &worker,
        json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "workspace.create_empty",
            "params": {"project_id": "project-1", "version_id": "version-1"},
        }),
    );
    assert!(created.get("result").is_some());
    let written = dispatch_worker_request(
        &worker,
        json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "workspace.write_text",
            "params": {
                "project_id": "project-1",
                "version_id": "version-1",
                "relative_path": "README.md",
                "content": "managed",
            },
        }),
    );
    assert!(written.get("result").is_some());

    let revealed = call(
        &worker,
        3,
        json!({
            "idempotency_key": "system:reveal:managed",
            "action": {
                "type": "reveal_path",
                "project_id": "project-1",
                "version_id": "version-1",
                "relative_path": "README.md",
            },
        }),
    );
    assert_eq!(revealed["result"]["completed"], true);
    let actions = backend.actions.lock().expect("actions lock");
    let ResolvedSystemAction::RevealPath { path } = &actions[0] else {
        panic!("expected reveal path")
    };
    assert!(path.ends_with("README.md"));
    assert!(path.is_absolute());
    drop(actions);

    let escaped = call(
        &worker,
        4,
        json!({
            "idempotency_key": "system:reveal:escape",
            "action": {
                "type": "reveal_path",
                "project_id": "project-1",
                "version_id": "version-1",
                "relative_path": "../outside.txt",
            },
        }),
    );
    assert_eq!(escaped["error"]["data"]["error_code"], "PATH_OUT_OF_SCOPE");
}

#[test]
fn protocol_rejects_non_https_oversized_unknown_and_shell_shaped_payloads() {
    let (_root, worker, backend) = fixture();
    let invalid = [
        json!({
            "idempotency_key": "system:http",
            "action": {"type": "open_url", "url": "http://example.com"},
        }),
        json!({
            "idempotency_key": "system:text",
            "action": {"type": "copy_text", "text": "x".repeat(32_769)},
        }),
        json!({
            "idempotency_key": "system:settings",
            "action": {"type": "open_settings", "page": "registry"},
        }),
        json!({
            "idempotency_key": "system:shell",
            "action": {
                "type": "copy_text",
                "text": "ok",
                "program": "cmd.exe",
                "args": ["/c", "whoami"],
            },
        }),
        json!({
            "idempotency_key": "system:outer-shell",
            "action": {"type": "copy_text", "text": "ok"},
            "command": "whoami",
            "shell": true,
            "script": "ignored",
        }),
    ];

    for (index, params) in invalid.into_iter().enumerate() {
        let response = call(&worker, index as u64 + 1, params);
        assert_eq!(response["error"]["data"]["error_code"], "INVALID_PARAMS");
    }
    assert!(backend.actions.lock().expect("actions lock").is_empty());
}

#[test]
fn notification_bounds_and_fixed_settings_enum_are_accepted() {
    let (_root, worker, backend) = fixture();
    let notification = call(
        &worker,
        1,
        json!({
            "idempotency_key": "system:notify",
            "action": {
                "type": "notify",
                "title": "Fairy",
                "body": "Preview ready",
                "level": "info",
            },
        }),
    );
    let settings = call(
        &worker,
        2,
        json!({
            "idempotency_key": "system:settings:display",
            "action": {"type": "open_settings", "page": "display"},
        }),
    );
    assert_eq!(notification["result"]["completed"], true);
    assert_eq!(settings["result"]["completed"], true);
    assert_eq!(backend.actions.lock().expect("actions lock").len(), 2);
}

#[test]
fn failed_effect_is_recorded_and_never_retried() {
    let (_root, worker, backend) = fixture();
    *backend.fail_with.lock().expect("failure lock") = Some("WORKER_INTERRUPTED");
    let params = json!({
        "idempotency_key": "system:failed",
        "action": {"type": "copy_text", "text": "once"},
    });
    let first = call(&worker, 1, params.clone());
    let replay = call(&worker, 2, params);
    assert_eq!(first["error"]["data"]["error_code"], "WORKER_INTERRUPTED");
    assert_eq!(replay["error"]["data"]["error_code"], "WORKER_INTERRUPTED");
    assert_eq!(backend.actions.lock().expect("actions lock").len(), 1);
}
