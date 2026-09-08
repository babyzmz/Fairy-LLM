use std::path::PathBuf;
use std::time::{Duration, Instant};

use fairy_core_bridge::{CoreBridge, CoreLaunchSpec};
use serde_json::json;
use tempfile::tempdir;

#[test]
fn development_spec_starts_real_core_within_readiness_budget() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let core_root = manifest.join("../../../../core");
    let data = tempdir().expect("temporary data directory");
    let started_at = Instant::now();
    let bridge = CoreBridge::spawn(CoreLaunchSpec::development(&core_root, data.path()))
        .expect("launch real Fairy Core");

    let response = bridge
        .call(json!({"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}}))
        .expect("Core health response");

    assert_eq!(response["result"]["status"], "ok");
    assert!(started_at.elapsed() < Duration::from_secs(3));
}

#[test]
fn negotiated_real_core_health_bypasses_a_ten_second_domain_request() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let core_root = manifest.join("../../../../core");
    let data = tempdir().unwrap();
    let mut spec = CoreLaunchSpec::development(&core_root, data.path());
    // The delay is injected at the domain boundary. Dispatcher, stdio transport,
    // process pipes, ID routing and health handler are the real implementations.
    let script = r#"
import os, sys, time, threading
from pathlib import Path
from fairy_core.transports.stdio import build_local_dispatcher, process_stream
d = build_local_dispatcher(Path(os.environ['FAIRY_V3_DATA_DIR']))
started = threading.Event()
original = d.dispatch
def dispatch(request):
    if request['method'] == 'providers.health':
        started.set()
        time.sleep(10)
    result = original(request)
    if request['method'] == 'health':
        result['result']['slow_started'] = started.is_set()
    return result
d.dispatch = dispatch
try:
    process_stream(d, sys.stdin, sys.stdout)
finally:
    d.close()
"#;
    spec.args = vec!["-u".to_owned(), "-c".to_owned(), script.to_owned()];
    let bridge = CoreBridge::spawn_verified(spec).unwrap();
    let slow_bridge = bridge.clone();
    let slow = std::thread::spawn(move || {
        slow_bridge.call(json!({
            "id": 1, "method": "providers.health", "params": {}
        }))
    });
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let start = Instant::now();
        let result = bridge
            .call(json!({"id": 1, "method": "health", "params": {}}))
            .unwrap();
        assert!(
            start.elapsed() < Duration::from_secs(1),
            "health was blocked by slow domain work"
        );
        if result["result"]["slow_started"] == true {
            break;
        }
        assert!(Instant::now() < deadline);
        std::thread::yield_now();
    }
    assert!(slow.join().unwrap().is_ok());
}

#[test]
fn real_core_pushes_committed_events_to_two_subscribers_and_replays_after_unwatch() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let core_root = manifest.join("../../../../core");
    let data = tempdir().unwrap();
    let bridge =
        CoreBridge::spawn_verified(CoreLaunchSpec::development(&core_root, data.path())).unwrap();
    let state = bridge
        .call(json!({"id": 1, "method": "events.state", "params": {}}))
        .unwrap();
    let cursor = state["result"]["latest_cursor"].as_u64().unwrap();
    let first = bridge.subscribe_events(cursor).unwrap();
    let second = bridge.subscribe_events(cursor).unwrap();
    let created = bridge.call(json!({"id": 2, "method": "projects.create", "params": {"name": "Event test", "residency": "local_only"}})).unwrap();
    assert!(created.get("error").is_none());
    let mut observed = Vec::new();
    for subscription in [&first, &second] {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let batch = subscription
                .recv_timeout(Duration::from_millis(200))
                .unwrap();
            if let Some(batch) = batch {
                assert_eq!(batch["ledger_id"], state["result"]["ledger_id"]);
                if let Some(event) = batch["items"].as_array().unwrap().iter().find(|item| {
                    item["event_type"] == "command.output"
                        && item["payload"]["command_name"] == "workspace.create_empty"
                }) {
                    observed.push(event["id"].clone());
                    break;
                }
            }
            assert!(
                Instant::now() < deadline,
                "committed project event was not pushed"
            );
        }
    }
    assert_eq!(observed[0], observed[1]);
    drop(first);
    drop(second);
    let replay = bridge.subscribe_events(cursor).unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        if let Some(batch) = replay.recv_timeout(Duration::from_millis(200)).unwrap() {
            if batch["items"]
                .as_array()
                .unwrap()
                .iter()
                .any(|item| item["id"] == observed[0])
            {
                break;
            }
        }
        assert!(Instant::now() < deadline);
    }
}
