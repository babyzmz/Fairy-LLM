use fairy_core_bridge::CoreBridgeError;
use fairy_desktop_v3::{
    authorize_core_rpc_window, auxiliary_window_policy, bridge_failure_response,
};
use serde_json::json;

#[test]
fn only_the_main_window_can_call_core_rpc() {
    assert!(authorize_core_rpc_window("main").is_ok());
    assert!(authorize_core_rpc_window("pet").is_err());
    assert!(authorize_core_rpc_window("guide").is_err());
    assert!(authorize_core_rpc_window("preview").is_err());
}

#[test]
fn guide_is_click_through_and_auxiliary_windows_are_not_core_clients() {
    let pet = auxiliary_window_policy("pet").expect("pet policy");
    assert!(!pet.ignore_cursor_events);
    assert!(pet.focusable);

    let guide = auxiliary_window_policy("guide").expect("guide policy");
    assert!(guide.ignore_cursor_events);
    assert!(!guide.focusable);
    assert!(auxiliary_window_policy("main").is_none());
}

#[test]
fn capability_files_keep_pet_local_and_guide_invoke_free() {
    let pet: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/pet.json")).expect("pet capability");
    let guide: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/guide.json")).expect("guide capability");

    let pet_permissions = pet["permissions"].as_array().expect("pet permissions");
    assert!(!pet_permissions.iter().any(|permission| {
        permission.as_str().is_some_and(|value| {
            value == "core:default" || value.contains("core_rpc") || value.contains("capture")
        })
    }));
    assert_eq!(guide["permissions"], json!([]));
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
