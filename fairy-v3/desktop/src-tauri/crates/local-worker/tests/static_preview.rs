use std::fs;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream};
use std::thread;
use std::time::{Duration, Instant};

use fairy_local_worker::preview::{PreviewServerState, StaticPreviewManager, StaticPreviewRequest};
use fairy_local_worker::WorkerError;
use tempfile::tempdir;

fn prepare_version() -> (tempfile::TempDir, std::path::PathBuf) {
    let temp = tempdir().expect("tempdir");
    let managed = temp.path().join("managed");
    let root = managed.join("projects/project-1/versions/version-1");
    fs::create_dir_all(root.join("assets")).expect("version root");
    fs::write(
        root.join("index.html"),
        "<!doctype html><script src=\"/assets/app.js\"></script><h1>Fairy Preview</h1>",
    )
    .expect("index");
    fs::write(root.join("assets/app.js"), "window.previewReady = true;").expect("asset");
    (temp, managed)
}

fn request(port: u16, method: &str, target: &str) -> Vec<u8> {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect preview");
    write!(
        stream,
        "{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )
    .expect("request");
    stream.shutdown(Shutdown::Write).expect("shutdown write");
    let mut response = Vec::new();
    stream.read_to_end(&mut response).expect("response");
    response
}

fn response_parts(response: &[u8]) -> (&[u8], &[u8]) {
    let boundary = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .expect("header boundary");
    (&response[..boundary], &response[boundary + 4..])
}

fn start(manager: &StaticPreviewManager) -> fairy_local_worker::preview::StaticPreviewInfo {
    manager
        .start_static(StaticPreviewRequest {
            preview_id: "preview-1".to_owned(),
            project_id: "project-1".to_owned(),
            version_id: "version-1".to_owned(),
            entry_path: "index.html".to_owned(),
        })
        .expect("start preview")
}

#[test]
fn static_preview_serves_get_and_head_with_browser_security_headers() {
    let (_temp, managed) = prepare_version();
    let manager = StaticPreviewManager::new(&managed);
    let info = start(&manager);

    assert_eq!(info.state, PreviewServerState::Running);
    assert_eq!(info.host, "127.0.0.1");
    assert!(info.port > 0);
    assert_eq!(
        info.url,
        format!("http://127.0.0.1:{}/preview-1/", info.port)
    );

    let get = request(info.port, "GET", "/");
    let (headers, body) = response_parts(&get);
    let headers = String::from_utf8_lossy(headers);
    assert!(headers.starts_with("HTTP/1.1 200"));
    assert!(headers.contains("Content-Type: text/html"));
    assert!(headers.contains("X-Content-Type-Options: nosniff"));
    assert!(!headers.contains("Cross-Origin-Resource-Policy: same-origin"));
    assert!(headers.contains("Cache-Control: no-store"));
    assert!(headers.contains("Content-Security-Policy:"));
    assert!(headers.contains("default-src 'self'"));
    assert!(headers.contains("connect-src 'self'"));
    assert!(!headers.contains("connect-src 'none'"));
    assert!(headers.contains(
        "frame-ancestors http://tauri.localhost tauri://localhost http://127.0.0.1:1430"
    ));
    assert!(!headers.contains("frame-ancestors 'none'"));
    assert!(String::from_utf8_lossy(body).contains("Fairy Preview"));
    assert!(
        String::from_utf8_lossy(&request(info.port, "GET", "/preview-1/"))
            .starts_with("HTTP/1.1 200")
    );

    let head = request(info.port, "HEAD", "/index.html");
    let (head_headers, head_body) = response_parts(&head);
    assert!(String::from_utf8_lossy(head_headers).starts_with("HTTP/1.1 200"));
    assert!(head_body.is_empty());

    assert!(
        String::from_utf8_lossy(&request(info.port, "GET", "/missing.txt"))
            .starts_with("HTTP/1.1 404")
    );
    let method = String::from_utf8_lossy(&request(info.port, "POST", "/")).into_owned();
    assert!(method.starts_with("HTTP/1.1 405"));
    assert!(method.contains("Allow: GET, HEAD"));

    let query = request(info.port, "GET", "/index.html?cache=1#ignored");
    assert!(String::from_utf8_lossy(&query).starts_with("HTTP/1.1 200"));
    let asset = String::from_utf8_lossy(&request(info.port, "GET", "/assets/app.js")).into_owned();
    assert!(asset.starts_with("HTTP/1.1 200"));
    assert!(asset.contains("Cache-Control: private, max-age=60"));
}

#[test]
fn static_preview_rejects_encoded_traversal_and_never_lists_directories() {
    let (temp, managed) = prepare_version();
    fs::write(temp.path().join("secret.txt"), "outside-secret").expect("outside secret");
    let manager = StaticPreviewManager::new(&managed);
    let info = start(&manager);

    for target in [
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/%2E%2E%5csecret.txt",
        "/assets/..%5c..%5csecret.txt",
        "/%00index.html",
        "/%FF",
        "/%ZZ",
    ] {
        let response = request(info.port, "GET", target);
        assert!(
            String::from_utf8_lossy(&response).starts_with("HTTP/1.1 400"),
            "target must be rejected: {target}"
        );
        assert!(!response
            .windows(b"outside-secret".len())
            .any(|window| window == b"outside-secret"));
    }
    assert!(
        String::from_utf8_lossy(&request(info.port, "GET", "/assets/")).starts_with("HTTP/1.1 404")
    );
}

#[test]
fn static_preview_rejects_symlinks_that_escape_the_version() {
    let (temp, managed) = prepare_version();
    let root = managed.join("projects/project-1/versions/version-1");
    let outside = temp.path().join("outside.txt");
    fs::write(&outside, "outside-secret").expect("outside");

    #[cfg(windows)]
    if std::os::windows::fs::symlink_file(&outside, root.join("escape.txt")).is_err() {
        return;
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, root.join("escape.txt")).expect("symlink");

    let manager = StaticPreviewManager::new(&managed);
    let info = start(&manager);
    let response = request(info.port, "GET", "/escape.txt");

    assert!(String::from_utf8_lossy(&response).starts_with("HTTP/1.1 400"));
    assert!(!response
        .windows(b"outside-secret".len())
        .any(|window| window == b"outside-secret"));
}

#[test]
fn static_preview_rejects_a_version_root_reparse_escape() {
    let (temp, managed) = prepare_version();
    let version_root = managed.join("projects/project-1/versions/version-1");
    let outside = temp.path().join("outside-version");
    fs::create_dir_all(&outside).expect("outside version");
    fs::write(outside.join("index.html"), "outside-secret").expect("outside index");
    fs::remove_dir_all(&version_root).expect("remove version root");

    #[cfg(windows)]
    if std::os::windows::fs::symlink_dir(&outside, &version_root).is_err() {
        return;
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, &version_root).expect("version symlink");

    let manager = StaticPreviewManager::new(&managed);
    let error = manager
        .start_static(StaticPreviewRequest {
            preview_id: "preview-1".to_owned(),
            project_id: "project-1".to_owned(),
            version_id: "version-1".to_owned(),
            entry_path: "index.html".to_owned(),
        })
        .expect_err("reparse root must fail closed");

    assert!(matches!(error, WorkerError::PathOutOfScope(_)));
}

#[test]
fn static_preview_lifecycle_is_idempotent_and_drop_stops_the_listener() {
    let (_temp, managed) = prepare_version();
    let port = {
        let manager = StaticPreviewManager::new(&managed);
        let first = start(&manager);
        let replay = start(&manager);
        assert_eq!(replay, first);
        assert_eq!(
            manager.status("preview-1").expect("status").state,
            PreviewServerState::Running
        );

        let conflict = manager
            .start_static(StaticPreviewRequest {
                preview_id: "preview-1".to_owned(),
                project_id: "project-1".to_owned(),
                version_id: "another-version".to_owned(),
                entry_path: "index.html".to_owned(),
            })
            .expect_err("binding conflict");
        assert!(matches!(conflict, WorkerError::PreviewScopeMismatch(_)));

        assert!(manager.stop("preview-1").expect("stop"));
        assert!(manager.stop("preview-1").expect("stop replay"));
        assert_eq!(
            manager.status("preview-1").expect("stopped status").state,
            PreviewServerState::Stopped
        );

        let second = manager
            .start_static(StaticPreviewRequest {
                preview_id: "preview-2".to_owned(),
                project_id: "project-1".to_owned(),
                version_id: "version-1".to_owned(),
                entry_path: "index.html".to_owned(),
            })
            .expect("second preview");
        assert!(TcpStream::connect(("127.0.0.1", second.port)).is_ok());
        second.port
    };

    let deadline = Instant::now() + Duration::from_secs(2);
    while TcpStream::connect(("127.0.0.1", port)).is_ok() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(20));
    }
    assert!(TcpStream::connect(("127.0.0.1", port)).is_err());
    assert!(!include_str!("../src/preview.rs").contains("Command::"));
}
