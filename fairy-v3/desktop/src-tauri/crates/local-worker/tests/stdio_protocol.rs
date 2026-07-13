use std::fs;
use std::io::Cursor;

use fairy_local_worker::{dispatch_request, process_stream, WorkspaceManager};
use serde_json::json;
use tempfile::tempdir;

#[test]
fn protocol_imports_forks_writes_and_checkpoints_a_scoped_version() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(temp.path().join("managed"));

    let imported = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "workspace.import",
            "params": {
                "source": source,
                "project_id": "project-1",
                "version_id": "version-base"
            }
        }),
    );
    let forked = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "workspace.fork",
            "params": {
                "project_id": "project-1",
                "parent_version_id": "version-base",
                "version_id": "version-draft"
            }
        }),
    );
    let written = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "workspace.write_text",
            "params": {
                "project_id": "project-1",
                "version_id": "version-draft",
                "relative_path": "README.md",
                "content": "draft"
            }
        }),
    );
    let diff = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "workspace.diff",
            "params": {"project_id": "project-1", "version_id": "version-draft"}
        }),
    );
    let checkpoint = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "workspace.checkpoint",
            "params": {
                "project_id": "project-1",
                "version_id": "version-draft",
                "message": "Task complete"
            }
        }),
    );
    assert!(imported["result"]["root"]
        .as_str()
        .unwrap()
        .ends_with("version-base"));
    assert!(forked["result"]["root"]
        .as_str()
        .unwrap()
        .ends_with("version-draft"));
    assert!(written["result"]["path"]
        .as_str()
        .unwrap()
        .ends_with("README.md"));
    assert_eq!(checkpoint["result"]["commit"].as_str().unwrap().len(), 40);
    assert!(diff["result"]["diff"]
        .as_str()
        .unwrap()
        .contains("README.md"));

    let discarded = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "workspace.discard",
            "params": {"project_id": "project-1", "version_id": "version-draft"}
        }),
    );
    assert_eq!(discarded["result"]["discarded"], true);
}

#[test]
fn protocol_maps_path_escape_to_standard_error_code() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(temp.path().join("managed"));
    manager
        .import_project(&source, "project-1", "version-base")
        .expect("import project");

    let response = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "workspace.write_text",
            "params": {
                "project_id": "project-1",
                "version_id": "version-base",
                "relative_path": "../secret.txt",
                "content": "blocked"
            }
        }),
    );

    assert_eq!(response["error"]["data"]["error_code"], "PATH_OUT_OF_SCOPE");
}

#[test]
fn changeset_validates_every_path_before_writing_any_file() {
    let temp = tempdir().expect("tempdir");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(temp.path().join("managed"));
    let workspace = manager
        .import_project(&source, "project-1", "version-base")
        .expect("import project");

    let response = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "workspace.apply_changeset",
            "params": {
                "project_id": "project-1",
                "version_id": "version-base",
                "mutations": [
                    {"relative_path": "README.md", "content": "draft"},
                    {"relative_path": "../secret.txt", "content": "blocked"}
                ]
            }
        }),
    );

    assert_eq!(response["error"]["data"]["error_code"], "PATH_OUT_OF_SCOPE");
    assert_eq!(
        fs::read_to_string(workspace.root.join("README.md")).expect("read file"),
        "base"
    );
}

#[test]
fn changeset_recovers_a_prepared_journal_before_the_next_request() {
    let temp = tempdir().expect("tempdir");
    let managed = temp.path().join("managed");
    let source = temp.path().join("source");
    fs::create_dir_all(&source).expect("source dir");
    fs::write(source.join("README.md"), "base").expect("source file");
    let manager = WorkspaceManager::new(&managed);
    let workspace = manager
        .import_project(&source, "project-1", "version-base")
        .expect("import project");
    let transaction = managed.join(".transactions/changesets/project-1/version-base/current");
    fs::create_dir_all(transaction.join("backups")).expect("backup dir");
    fs::write(transaction.join("backups/0.bin"), "base").expect("backup");
    fs::write(
        transaction.join("manifest.json"),
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "entries": [{"relative_path": "README.md", "existed": true}]
        }))
        .expect("manifest json"),
    )
    .expect("manifest");
    fs::write(workspace.root.join("README.md"), "partial").expect("partial write");

    let response = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 12,
            "method": "workspace.apply_changeset",
            "params": {
                "project_id": "project-1",
                "version_id": "version-base",
                "mutations": [
                    {"relative_path": "../secret.txt", "content": "blocked"}
                ]
            }
        }),
    );

    assert_eq!(response["error"]["data"]["error_code"], "PATH_OUT_OF_SCOPE");
    assert_eq!(
        fs::read_to_string(workspace.root.join("README.md")).expect("read file"),
        "base"
    );
    assert!(!transaction.exists());
}

#[test]
fn protocol_creates_empty_projects_and_conversation_scratch() {
    let temp = tempdir().expect("tempdir");
    let manager = WorkspaceManager::new(temp.path().join("managed"));

    let project = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "workspace.create_empty",
            "params": {"project_id": "project-1", "version_id": "version-base"}
        }),
    );
    let scratch = dispatch_request(
        &manager,
        json!({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "workspace.create_scratch",
            "params": {"conversation_id": "conversation-1", "task_id": "task-1"}
        }),
    );

    assert!(project["result"]["root"]
        .as_str()
        .unwrap()
        .ends_with("version-base"));
    assert!(scratch["result"]["root"]
        .as_str()
        .unwrap()
        .ends_with("task-1"));
    assert!(temp
        .path()
        .join("managed/scratch/conversation-1/task-1")
        .is_dir());
}

#[test]
fn preview_protocol_keeps_registry_for_the_entire_stdio_stream() {
    let temp = tempdir().expect("tempdir");
    let managed = temp.path().join("managed");
    let root = managed.join("projects/project-1/versions/version-1");
    fs::create_dir_all(&root).expect("version root");
    fs::write(root.join("index.html"), "<h1>Preview</h1>").expect("index");
    let manager = WorkspaceManager::new(&managed);
    let input = [
        json!({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "preview.start_static",
            "params": {
                "preview_id": "preview-1",
                "workspace_id": "project-1",
                "version_id": "version-1",
                "entry_path": "index.html"
            }
        }),
        json!({
            "jsonrpc": "2.0",
            "id": 21,
            "method": "preview.status",
            "params": {"preview_id": "preview-1"}
        }),
        json!({
            "jsonrpc": "2.0",
            "id": 22,
            "method": "preview.start_static",
            "params": {
                "preview_id": "preview-1",
                "workspace_id": "project-1",
                "version_id": "another-version",
                "entry_path": "index.html"
            }
        }),
        json!({
            "jsonrpc": "2.0",
            "id": 23,
            "method": "preview.status",
            "params": {"preview_id": "unknown-preview"}
        }),
        json!({
            "jsonrpc": "2.0",
            "id": 24,
            "method": "preview.stop",
            "params": {"preview_id": "preview-1"}
        }),
    ]
    .into_iter()
    .map(|request| serde_json::to_string(&request).expect("json"))
    .collect::<Vec<_>>()
    .join("\n");
    let mut output = Vec::new();

    process_stream(&manager, Cursor::new(input), &mut output).expect("process stream");

    let responses = String::from_utf8(output)
        .expect("utf8")
        .lines()
        .map(|line| serde_json::from_str::<serde_json::Value>(line).expect("response"))
        .collect::<Vec<_>>();
    assert_eq!(responses[0]["result"]["host"], "127.0.0.1");
    assert_eq!(responses[0]["result"]["state"], "running");
    assert_eq!(responses[1]["result"]["state"], "running");
    assert_eq!(
        responses[1]["result"]["executor_handle"],
        responses[0]["result"]["executor_handle"]
    );
    assert_eq!(
        responses[2]["error"]["data"]["error_code"],
        "SCOPE_MISMATCH"
    );
    assert_eq!(
        responses[3]["error"]["data"]["error_code"],
        "WORKER_INTERRUPTED"
    );
    assert_eq!(responses[4]["result"]["stopped"], true);
}
