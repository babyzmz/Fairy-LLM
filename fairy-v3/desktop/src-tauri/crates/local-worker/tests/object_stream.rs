use fairy_local_worker::{dispatch_worker_request, LocalWorker, WorkspaceManager};
use serde_json::json;
use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use tempfile::tempdir;
use url::Url;

#[test]
fn imports_content_addressed_asset_and_serves_range() {
    let temporary = tempdir().expect("temporary directory");
    let managed = temporary.path().join("managed");
    let manager = WorkspaceManager::new(&managed);
    manager
        .create_empty("workspace-1", "version-1")
        .expect("empty Workspace");
    let source = temporary.path().join("asset.bin");
    fs::write(&source, (0_u8..64).collect::<Vec<_>>()).expect("source asset");
    let worker = LocalWorker::new(manager);

    let imported = dispatch_worker_request(
        &worker,
        json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "workspace.import_asset",
            "params": {
                "project_id": "workspace-1",
                "version_id": "version-1",
                "operation": "create",
                "relative_path": "media/asset.bin",
                "source": source,
                "max_file_bytes": 1024,
                "max_workspace_bytes": 4096
            }
        }),
    );
    let object = &imported["result"];
    assert_eq!(object["byte_length"], 64);
    assert_eq!(object["content_hash"].as_str().unwrap().len(), 64);

    let duplicate_create = dispatch_worker_request(
        &worker,
        json!({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "workspace.import_asset",
            "params": {
                "project_id": "workspace-1",
                "version_id": "version-1",
                "operation": "create",
                "relative_path": "media/asset.bin",
                "source": source,
                "max_file_bytes": 1024,
                "max_workspace_bytes": 4096
            }
        }),
    );
    assert_eq!(
        duplicate_create["error"]["data"]["error_code"],
        "VERSION_CONFLICT"
    );

    let opened = dispatch_worker_request(
        &worker,
        json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "workspace.open_read_stream",
            "params": {
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "version_id": "version-1",
                "relative_path": "media/asset.bin",
                "content_hash": object["content_hash"],
                "byte_length": 64,
                "media_type": "application/octet-stream",
                "expires_seconds": 60
            }
        }),
    );
    let url = Url::parse(opened["result"]["url"].as_str().unwrap()).expect("read URL");
    let response = raw_request(&url, "Range: bytes=8-15\r\n");
    let header_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .expect("HTTP header terminator");
    let headers = String::from_utf8_lossy(&response[..header_end]);
    assert!(headers.starts_with("HTTP/1.1 206"), "{headers}");
    assert!(headers.contains("Content-Range: bytes 8-15/64"));
    assert_eq!(&response[header_end + 4..], &[8, 9, 10, 11, 12, 13, 14, 15]);

    let revoked = dispatch_worker_request(
        &worker,
        json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "workspace.revoke_read_stream",
            "params": {"session_id": "session-1"}
        }),
    );
    assert_eq!(revoked["result"]["revoked"], true);
    assert!(TcpStream::connect((url.host_str().unwrap(), url.port().unwrap())).is_err());
}

fn raw_request(url: &Url, extra_headers: &str) -> Vec<u8> {
    let host = url.host_str().expect("host");
    let port = url.port().expect("port");
    let mut stream = TcpStream::connect((host, port)).expect("connect read server");
    write!(
        stream,
        "GET {} HTTP/1.1\r\nHost: {host}:{port}\r\n{extra_headers}Connection: close\r\n\r\n",
        url.path()
    )
    .expect("request");
    let mut response = Vec::new();
    stream.read_to_end(&mut response).expect("response");
    response
}
