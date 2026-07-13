use fairy_core_bridge::CoreBridgeError;
use fairy_desktop_v3::{
    anchored_pet_frame, anchored_pet_input_frame, authorize_core_rpc_window,
    authorize_pet_input_window, authorize_preferences_reader, authorize_settings_window,
    authorize_voice_health_window, authorize_voice_settings_window, auxiliary_window_policy,
    bridge_failure_response, fairy_tray_action, settings_method_allowed, FairyTrayAction,
    PetWindowFrame,
};
use serde_json::json;

#[test]
fn only_the_main_window_can_call_core_rpc() {
    assert!(authorize_core_rpc_window("main").is_ok());
    assert!(authorize_core_rpc_window("pet-render").is_err());
    assert!(authorize_core_rpc_window("pet-input").is_err());
    assert!(authorize_core_rpc_window("preview").is_err());
    assert!(authorize_core_rpc_window("settings").is_err());
}

#[test]
fn voice_host_commands_keep_session_and_model_access_out_of_auxiliary_windows() {
    assert!(authorize_voice_health_window("main").is_ok());
    assert!(authorize_voice_health_window("settings").is_ok());
    assert!(authorize_voice_health_window("pet-render").is_err());
    assert!(authorize_voice_health_window("pet-input").is_err());
    assert!(authorize_voice_settings_window("settings").is_ok());
    assert!(authorize_voice_settings_window("main").is_err());
    assert!(authorize_voice_settings_window("pet-input").is_err());
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
    assert!(authorize_preferences_reader("pet-input").is_ok());
    assert!(authorize_preferences_reader("pet-render").is_err());
    assert!(authorize_pet_input_window("pet-input").is_ok());
    assert!(authorize_pet_input_window("pet-render").is_err());
    assert!(authorize_pet_input_window("main").is_err());
    assert!(authorize_pet_input_window("settings").is_err());
}

#[test]
fn pet_render_is_permanently_pass_through_and_input_starts_passive() {
    let render = auxiliary_window_policy("pet-render").expect("render policy");
    assert!(render.ignore_cursor_events);
    assert!(!render.focusable);

    let input = auxiliary_window_policy("pet-input").expect("input policy");
    assert!(input.ignore_cursor_events);
    assert!(!input.focusable);

    assert!(auxiliary_window_policy("main").is_none());
    assert!(auxiliary_window_policy("guide").is_none());
}

#[test]
fn capability_files_keep_pet_local() {
    let render: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/pet-render.json"))
            .expect("render capability");
    let input: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/pet-input.json"))
            .expect("input capability");
    let settings: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/settings.json"))
            .expect("settings capability");

    for capability in [&render, &input] {
        let permissions = capability["permissions"]
            .as_array()
            .expect("pet permissions");
        assert!(!permissions.iter().any(|permission| {
            permission.as_str().is_some_and(|value| {
                value == "core:default"
                    || value.contains("core_rpc")
                    || value.contains("capture")
                    || value.contains("voice")
                    || value.contains("window:allow-set")
            })
        }));
    }
    assert!(!settings["permissions"]
        .as_array()
        .expect("settings permissions")
        .iter()
        .any(|permission| permission == "core:default"));
}

#[test]
fn tauri_config_declares_separate_render_and_input_surfaces() {
    let config: serde_json::Value =
        serde_json::from_str(include_str!("../tauri.conf.json")).expect("Tauri config");
    let windows = config["app"]["windows"].as_array().expect("window list");
    let render = windows
        .iter()
        .find(|window| window["label"] == "pet-render")
        .expect("render window");
    let input = windows
        .iter()
        .find(|window| window["label"] == "pet-input")
        .expect("input window");

    assert_eq!(
        (render["width"].as_u64(), render["height"].as_u64()),
        (Some(640), Some(260))
    );
    assert_eq!(render["focusable"], false);
    assert_eq!(render["alwaysOnTop"], true);
    assert_eq!(
        (input["width"].as_u64(), input["height"].as_u64()),
        (Some(372), Some(72))
    );
    assert_eq!(input["visible"], false);
    assert_eq!(input["alwaysOnTop"], true);
}

#[test]
fn pet_input_overlays_the_render_surface_without_a_material_gap() {
    let render = PetWindowFrame {
        x: -1280,
        y: 240,
        width: 640,
        height: 260,
    };
    let compact = anchored_pet_input_frame(render, 372, 72, 72);
    assert_eq!(
        compact,
        PetWindowFrame {
            x: -1012,
            y: 334,
            width: 372,
            height: 72,
        }
    );

    let expanded = anchored_pet_input_frame(render, 420, 360, 72);
    assert_eq!(
        expanded,
        PetWindowFrame {
            x: -1060,
            y: 46,
            width: 420,
            height: 360,
        }
    );
    assert_eq!(
        compact.y + compact.height as i32,
        expanded.y + expanded.height as i32
    );
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

#[test]
fn tray_menu_routes_only_fixed_companion_actions() {
    assert_eq!(
        fairy_tray_action("fairy.tray.ask"),
        Some(FairyTrayAction::Ask)
    );
    assert_eq!(
        fairy_tray_action("fairy.tray.always_on_top"),
        Some(FairyTrayAction::ToggleAlwaysOnTop)
    );
    assert_eq!(fairy_tray_action("assistant.turns.start"), None);
    assert_eq!(fairy_tray_action("system.actions.execute"), None);
}
