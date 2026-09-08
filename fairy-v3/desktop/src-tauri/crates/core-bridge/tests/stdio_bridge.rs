use std::collections::BTreeMap;
use std::env;
use std::path::PathBuf;
use std::sync::{mpsc, Arc};
use std::time::Duration;

use fairy_core_bridge::{CoreBridge, CoreBridgeError, CoreLaunchSpec};
use serde_json::json;

fn python_program() -> String {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let core_root = manifest.join("../../../../core");
    [
        core_root.join(".venv/Scripts/python.exe"),
        core_root.join(".venv/bin/python"),
    ]
    .into_iter()
    .find(|candidate| candidate.is_file())
    .map(|candidate| candidate.to_string_lossy().into_owned())
    .or_else(|| {
        env::var("PYTHON")
            .ok()
            .filter(|value| !value.trim().is_empty())
    })
    .unwrap_or_else(|| "python".to_owned())
}

fn helper(script: &str) -> CoreLaunchSpec {
    CoreLaunchSpec {
        program: python_program(),
        args: vec!["-u".to_owned(), "-c".to_owned(), script.to_owned()],
        env: BTreeMap::new(),
        clear_environment: true,
        current_dir: None,
    }
}

#[test]
fn sends_one_request_and_reads_the_matching_response() {
    let script = r#"
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    response = {"jsonrpc": "2.0", "id": request["id"], "result": {"method": request["method"]}}
    print(json.dumps(response), flush=True)
"#;
    let bridge = CoreBridge::spawn(helper(script)).expect("start helper");

    let response = bridge
        .call(json!({"jsonrpc": "2.0", "id": 7, "method": "health", "params": {}}))
        .expect("rpc response");

    assert_eq!(response["id"], 7);
    assert_eq!(response["result"]["method"], "health");
}

#[test]
fn concurrent_requests_with_same_client_id_receive_their_own_out_of_order_responses() {
    let script = r#"
import json, sys, threading, time
lock = threading.Lock()
def respond(request):
    if request['method'] == 'slow':
        time.sleep(1)
    with lock:
        print(json.dumps({'jsonrpc':'2.0', 'id':request['id'], 'result':request['method']}), flush=True)
for line in sys.stdin:
    threading.Thread(target=respond, args=(json.loads(line),)).start()
"#;
    let bridge = Arc::new(CoreBridge::spawn(helper(script)).expect("start helper"));
    let (sender, receiver) = mpsc::channel();
    let first = Arc::clone(&bridge);
    let slow = std::thread::spawn(move || first.call(json!({"id": 7, "method": "slow"})));
    // Give the first caller an opportunity to enter its response wait.
    std::thread::sleep(Duration::from_millis(100));
    let second = Arc::clone(&bridge);
    let fast = std::thread::spawn(move || {
        sender
            .send(second.call(json!({"id": 7, "method": "health"})))
            .unwrap();
    });
    let response = receiver.recv_timeout(Duration::from_millis(500));
    let slow_response = slow.join().unwrap().unwrap();
    fast.join().unwrap();
    assert_eq!(slow_response["id"], 7);
    assert_eq!(slow_response["result"], "slow");
    let response = response
        .expect("fast call must not wait for the slow response")
        .unwrap();
    assert_eq!(response["id"], 7);
    assert_eq!(response["result"], "health");
}

#[test]
fn rejects_a_response_with_the_wrong_request_id() {
    let script = r#"
import json, sys
for line in sys.stdin:
    json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}), flush=True)
"#;
    let bridge = CoreBridge::spawn(helper(script)).expect("start helper");

    let error = bridge
        .call(json!({"jsonrpc": "2.0", "id": 8, "method": "health", "params": {}}))
        .expect_err("mismatched response must fail");

    assert!(matches!(
        error,
        CoreBridgeError::ResponseIdMismatch {
            expected: 8,
            actual: 99
        }
    ));
}

#[test]
fn reports_worker_interrupted_when_core_exits() {
    let bridge =
        CoreBridge::spawn(helper("import sys; sys.exit(0)")).expect("start short-lived helper");

    let error = bridge
        .call(json!({"jsonrpc": "2.0", "id": 3, "method": "health", "params": {}}))
        .expect_err("closed stdout must fail");

    assert!(matches!(error, CoreBridgeError::WorkerInterrupted));
}

#[test]
fn late_response_after_timeout_cannot_complete_another_request() {
    let script = r#"
import json, sys, threading, time
lock = threading.Lock()
def respond(request):
    time.sleep(0.2 if request['method'] == 'slow' else 0.4)
    with lock:
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':request['method']}), flush=True)
for line in sys.stdin:
    threading.Thread(target=respond, args=(json.loads(line),)).start()
"#;
    let bridge = CoreBridge::spawn(helper(script)).unwrap();
    let error = bridge
        .call_with_timeout(
            json!({"id": 1, "method": "slow"}),
            Duration::from_millis(100),
        )
        .unwrap_err();
    assert!(matches!(error, CoreBridgeError::RequestTimedOut));
    let response = bridge.call(json!({"id": 1, "method": "next"})).unwrap();
    assert_eq!(response["id"], 1);
    assert_eq!(response["result"], "next");
}

#[test]
fn shutdown_interrupts_cloned_callers_and_does_not_affect_a_new_generation() {
    let old = CoreBridge::spawn(helper(
        "import sys, time\nfor line in sys.stdin: time.sleep(5)",
    ))
    .unwrap();
    let clone = old.clone();
    let caller = std::thread::spawn(move || clone.call(json!({"id": 1, "method": "slow"})));
    std::thread::sleep(Duration::from_millis(50));
    old.shutdown();
    assert!(matches!(
        caller.join().unwrap(),
        Err(CoreBridgeError::WorkerInterrupted)
    ));
    let new = CoreBridge::spawn(helper("import json, sys\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({'id':r['id'],'result':'new'}),flush=True)")).unwrap();
    assert_eq!(
        new.call(json!({"id": 1, "method": "health"})).unwrap()["result"],
        "new"
    );
}

#[test]
fn slow_event_subscriber_requests_resync_without_blocking_another_subscriber() {
    let script = r#"
import json, sys
count=0
for line in sys.stdin:
    r=json.loads(line)
    result={'status':'ok','service':'fairy-core','protocol':'core-service-v1','event_notifications':True}
    if r['method']=='events.watch':
        count+=1
        key=r['params']['subscription_id']
        result={'subscription_id':key, 'ledger_id':'ledger', 'latest_cursor':0, 'oldest_cursor':0}
    print(json.dumps({'id':r['id'], 'result':result}),flush=True)
    if r['method']=='events.watch':
        for cursor in range(12 if count==1 else 1):
            print(json.dumps({'jsonrpc':'2.0','method':'events.changed','params':{
                'subscription_id':key, 'cursor':cursor+1, 'items':[], 'resync_required':False
            }}),flush=True)
"#;
    let bridge = CoreBridge::spawn_verified(helper(script)).unwrap();
    let first = bridge.subscribe_events(0).unwrap();
    let second = bridge.subscribe_events(0).unwrap();
    assert_eq!(
        second
            .recv_timeout(Duration::from_secs(1))
            .unwrap()
            .unwrap()["cursor"],
        1
    );
    assert!(matches!(
        first.recv_timeout(Duration::from_secs(1)),
        Err(CoreBridgeError::EventResyncRequired)
    ));
    drop(first);
    drop(second);
    assert_eq!(
        bridge.call(json!({"id": 8, "method": "health"})).unwrap()["result"]["status"],
        "ok"
    );
}

#[test]
fn verifies_the_core_service_and_protocol_before_use() {
    let script = r#"
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "status": "ok",
            "service": "fairy-core",
            "protocol": "core-service-v1",
        },
    }), flush=True)
"#;

    CoreBridge::spawn_verified(helper(script)).expect("verified Core handshake");
}

#[test]
fn rejects_an_incompatible_core_protocol() {
    let script = r#"
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "status": "ok",
            "service": "fairy-core",
            "protocol": "core-service-v0",
        },
    }), flush=True)
"#;

    let error = match CoreBridge::spawn_verified(helper(script)) {
        Ok(_) => panic!("protocol mismatch must fail"),
        Err(error) => error,
    };
    assert!(matches!(error, CoreBridgeError::ProtocolMismatch { .. }));
}
