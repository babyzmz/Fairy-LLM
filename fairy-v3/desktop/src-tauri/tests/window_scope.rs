use fairy_core_bridge::CoreBridgeError;
use fairy_desktop_v3::{
    anchored_pet_frame, authorize_core_rpc_window, authorize_pet_window,
    authorize_preferences_reader, authorize_settings_window, authorize_voice_health_window,
    authorize_voice_settings_window, auxiliary_window_policy, bridge_failure_response,
    settings_method_allowed, PetWindowFrame,
};
use serde_json::json;

#[test]
fn only_the_main_window_can_call_core_rpc() {
    assert!(authorize_core_rpc_window("main").is_ok());
    assert!(authorize_core_rpc_window("pet").is_err());
    assert!(authorize_core_rpc_window("preview").is_err());
    assert!(authorize_core_rpc_window("settings").is_err());
}

#[test]
fn voice_host_commands_keep_session_and_model_access_out_of_auxiliary_windows() {
    assert!(authorize_voice_health_window("main").is_ok());
    assert!(authorize_voice_health_window("settings").is_ok());
    assert!(authorize_voice_health_window("pet").is_err());
    assert!(authorize_voice_settings_window("settings").is_ok());
    assert!(authorize_voice_settings_window("main").is_err());
    assert!(authorize_voice_settings_window("pet").is_err());
}

#[test]
fn settings_window_has_a_narrow_method_allow_list() {
    assert!(authorize_settings_window("settings").is_ok());
    assert!(authorize_settings_window("main").is_err());
    assert!(settings_method_allowed("permissions.update"));
    assert!(settings_method_allowed("mcp.servers.configure"));
    assert!(!settings_method_allowed("assistant.turns.start"));
    assert!(!settings_method_allowed("projects.list"));
    assert!(!settings_method_allowed("system.actions.execute"));
    assert!(authorize_preferences_reader("main").is_ok());
    assert!(authorize_preferences_reader("pet").is_ok());
    assert!(authorize_pet_window("pet").is_ok());
    assert!(authorize_pet_window("main").is_err());
    assert!(authorize_pet_window("settings").is_err());
}

#[test]
fn pet_is_interactive_and_is_not_a_core_client() {
    let pet = auxiliary_window_policy("pet").expect("pet policy");
    assert!(!pet.ignore_cursor_events);
    assert!(pet.focusable);

    assert!(auxiliary_window_policy("main").is_none());
    assert!(auxiliary_window_policy("guide").is_none());
}

#[test]
fn capability_files_keep_pet_local() {
    let pet: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/pet.json")).expect("pet capability");
    let settings: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/settings.json"))
            .expect("settings capability");

    let pet_permissions = pet["permissions"].as_array().expect("pet permissions");
    assert!(!pet_permissions.iter().any(|permission| {
        permission.as_str().is_some_and(|value| {
            value == "core:default" || value.contains("core_rpc") || value.contains("capture")
        })
    }));
    assert!(!settings["permissions"]
        .as_array()
        .expect("settings permissions")
        .iter()
        .any(|permission| permission == "core:default"));
}

#[test]
fn expanded_pet_preserves_its_bottom_right_anchor_and_clamps_to_monitor() {
    let monitor = PetWindowFrame {
        x: 0,
        y: 0,
        width: 1920,
        height: 1040,
    };
    let expanded = anchored_pet_frame(
        PetWindowFrame {
            x: 1700,
            y: 844,
            width: 176,
            height: 176,
        },
        420,
        360,
        monitor,
    );
    assert_eq!(expanded.x, 1456);
    assert_eq!(expanded.y, 660);
    assert_eq!(expanded.x + expanded.width as i32, 1876);
    assert_eq!(expanded.y + expanded.height as i32, 1020);

    let clamped = anchored_pet_frame(
        PetWindowFrame {
            x: -40,
            y: -30,
            width: 176,
            height: 176,
        },
        420,
        360,
        monitor,
    );
    assert_eq!((clamped.x, clamped.y), (0, 0));
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
