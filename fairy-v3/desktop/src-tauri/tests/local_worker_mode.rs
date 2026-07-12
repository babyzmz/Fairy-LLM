use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

use serde_json::{json, Value};
use tempfile::tempdir;

#[test]
fn desktop_binary_runs_local_worker_without_starting_tauri() {
    let managed = tempdir().expect("managed root");
    let mut child = Command::new(env!("CARGO_BIN_EXE_fairy"))
        .arg("--local-worker")
        .env("FAIRY_MANAGED_ROOT", managed.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .expect("start local worker mode");
    let mut stdin = child.stdin.take().expect("worker stdin");
    let mut stdout = BufReader::new(child.stdout.take().expect("worker stdout"));
    writeln!(
        stdin,
        "{}",
        json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "workspace.create_empty",
            "params": {"project_id": "project-1", "version_id": "version-base"}
        })
    )
    .expect("write request");
    stdin.flush().expect("flush request");
    let mut line = String::new();
    stdout.read_line(&mut line).expect("read response");
    drop(stdin);
    let status = child.wait().expect("worker exit");
    let response: Value = serde_json::from_str(&line).expect("JSON response");

    assert!(status.success());
    assert_eq!(response["id"], 1);
    assert!(managed
        .path()
        .join("projects/project-1/versions/version-base")
        .is_dir());
}

#[test]
fn bundled_core_path_is_adjacent_to_the_desktop_executable() {
    let executable = std::path::Path::new("C:/Program Files/Fairy/fairy.exe");
    let sidecar = fairy_desktop_v3::bundled_core_path(executable);
    assert_eq!(
        sidecar,
        std::path::Path::new("C:/Program Files/Fairy/fairy-core.exe")
    );
}

#[test]
fn bundled_git_path_stays_inside_the_resource_directory() {
    let resources = std::path::Path::new("C:/Program Files/Fairy/resources");
    let git = fairy_desktop_v3::bundled_git_path(resources);
    assert_eq!(
        git,
        std::path::Path::new("C:/Program Files/Fairy/resources/runtime/git/cmd/git.exe")
    );
}
