use std::collections::BTreeMap;
use std::env;

use fairy_core_bridge::{CoreBridge, CoreBridgeError, CoreLaunchSpec};
use serde_json::json;

fn python_program() -> String {
    env::var("PYTHON").unwrap_or_else(|_| "python".to_owned())
}

fn helper(script: &str) -> CoreLaunchSpec {
    CoreLaunchSpec {
        program: python_program(),
        args: vec!["-u".to_owned(), "-c".to_owned(), script.to_owned()],
        env: BTreeMap::new(),
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
