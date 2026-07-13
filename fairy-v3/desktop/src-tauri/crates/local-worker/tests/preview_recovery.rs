use std::fs;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream};
use std::sync::{Arc, Barrier};
use std::thread;
use std::time::{Duration, Instant};

use fairy_local_worker::preview::{PreviewServerState, StaticPreviewManager, StaticPreviewRequest};
use fairy_local_worker::WorkerError;
use tempfile::tempdir;

fn prepare_version() -> (tempfile::TempDir, std::path::PathBuf) {
    let temp = tempdir().expect("tempdir");
    let managed = temp.path().join("managed");
    let root = managed.join("projects/project-1/versions/version-1");
    fs::create_dir_all(&root).expect("version root");
    fs::write(root.join("index.html"), "<!doctype html><h1>Preview</h1>").expect("index");
    (temp, managed)
}

fn preview_request() -> StaticPreviewRequest {
    StaticPreviewRequest {
        preview_id: "preview-1".to_owned(),
        workspace_id: "project-1".to_owned(),
        version_id: "version-1".to_owned(),
        entry_path: "index.html".to_owned(),
    }
}

fn raw_request(port: u16, target: &str) -> Vec<u8> {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect preview");
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .expect("read timeout");
    write!(
        stream,
        "GET {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )
    .expect("request");
    stream.shutdown(Shutdown::Write).expect("shutdown write");
    let mut response = Vec::new();
    stream.read_to_end(&mut response).expect("response");
    response
}

#[test]
fn concurrent_duplicate_starts_share_one_server_entity() {
    let (_temp, managed) = prepare_version();
    let manager = StaticPreviewManager::new(&managed);
    let barrier = Arc::new(Barrier::new(8));
    let mut threads = Vec::new();

    for _index in 0..8 {
        let worker = manager.clone();
        let start = Arc::clone(&barrier);
        threads.push(thread::spawn(move || {
            start.wait();
            worker.start_static(preview_request()).expect("start")
        }));
    }
    let results: Vec<_> = threads
        .into_iter()
        .map(|worker| worker.join().expect("worker"))
        .collect();

    assert!(results
        .iter()
        .all(|result| result == results.first().expect("first result")));
    assert_eq!(manager.status("preview-1").expect("status"), results[0]);
    assert!(manager.stop("preview-1").expect("stop"));
}

#[test]
fn worker_loss_is_unavailable_until_an_explicit_restart() {
    let (_temp, managed) = prepare_version();
    let old_port = {
        let manager = StaticPreviewManager::new(&managed);
        manager.start_static(preview_request()).expect("start").port
    };

    let deadline = Instant::now() + Duration::from_secs(2);
    while TcpStream::connect(("127.0.0.1", old_port)).is_ok() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(20));
    }
    assert!(TcpStream::connect(("127.0.0.1", old_port)).is_err());

    let restarted = StaticPreviewManager::new(&managed);
    let error = restarted
        .status("preview-1")
        .expect_err("in-memory runtime must not be forged after worker loss");
    assert!(matches!(error, WorkerError::PreviewUnavailable(_)));

    let resumed = restarted
        .start_static(preview_request())
        .expect("explicit restart");
    assert_eq!(resumed.state, PreviewServerState::Running);
    assert!(restarted.stop("preview-1").expect("stop resumed"));
}

#[test]
fn oversized_request_target_is_rejected_without_stopping_the_server() {
    let (_temp, managed) = prepare_version();
    let manager = StaticPreviewManager::new(&managed);
    let info = manager.start_static(preview_request()).expect("start");
    let oversized = format!("/{}", "a".repeat(20_000));

    let rejected = raw_request(info.port, &oversized);

    assert!(String::from_utf8_lossy(&rejected).starts_with("HTTP/1.1 414"));
    assert!(String::from_utf8_lossy(&raw_request(info.port, "/")).starts_with("HTTP/1.1 200"));
    assert!(manager.stop("preview-1").expect("stop"));
}
