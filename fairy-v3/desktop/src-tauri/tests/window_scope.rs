use fairy_core_bridge::CoreBridgeError;
use fairy_desktop_v3::{authorize_core_rpc_window, bridge_failure_response};
use serde_json::json;

#[test]
fn only_the_main_window_can_call_core_rpc() {
    assert!(authorize_core_rpc_window("main").is_ok());
    assert!(authorize_core_rpc_window("pet").is_err());
    assert!(authorize_core_rpc_window("preview").is_err());
}

#[test]
fn interrupted_core_is_returned_as_a_typed_jsonrpc_error() {
    let response = bridge_failure_response(json!(12), &CoreBridgeError::WorkerInterrupted);

    assert_eq!(response["id"], 12);
    assert_eq!(
        response["error"]["data"]["error_code"],
        "WORKER_INTERRUPTED"
    );
}
