use fairy_core_bridge::CoreBridgeError;
use fairy_desktop_v3::presence_coordinator::ExpansionDirection;
use fairy_desktop_v3::{
    anchored_pet_core_frame, anchored_pet_frame, anchored_pet_input_frame,
    authorize_core_rpc_window, authorize_pet_input_window, authorize_pet_render_window,
    authorize_preferences_reader, authorize_settings_window, authorize_voice_control_window,
    authorize_voice_health_window, authorize_voice_settings_window, auxiliary_window_policy,
    bridge_failure_response, fairy_tray_action, presence_window_creation_specs,
    resolve_desktop_data_dir, settings_method_allowed, FairyTrayAction, PetWindowFrame,
};
use serde_json::json;
use std::ffi::OsString;
use std::path::PathBuf;

#[test]
fn only_the_main_window_can_call_core_rpc() {
    assert!(authorize_core_rpc_window("main").is_ok());
    assert!(authorize_core_rpc_window("pet-render").is_err());
    assert!(authorize_core_rpc_window("pet-input").is_err());
    assert!(authorize_core_rpc_window("preview").is_err());
    assert!(authorize_core_rpc_window("settings").is_err());
}

#[test]
fn voice_host_commands_allow_bounded_pet_state_without_exposing_settings() {
    assert!(authorize_voice_health_window("main").is_ok());
    assert!(authorize_voice_health_window("settings").is_err());
    assert!(authorize_voice_health_window("pet-render").is_ok());
    assert!(authorize_voice_health_window("pet-input").is_ok());
    assert!(authorize_voice_health_window("companion").is_ok());
    assert!(authorize_voice_control_window("main").is_ok());
    assert!(authorize_voice_control_window("pet-render").is_ok());
    assert!(authorize_voice_control_window("pet-input").is_ok());
    assert!(authorize_voice_control_window("companion").is_ok());
    assert!(authorize_voice_control_window("settings").is_err());
    assert!(authorize_voice_settings_window("main").is_ok());
    assert!(authorize_voice_settings_window("settings").is_err());
    assert!(authorize_voice_settings_window("pet-input").is_err());
}

#[test]
fn main_window_settings_module_has_a_narrow_method_allow_list() {
    assert!(authorize_settings_window("main").is_ok());
    assert!(authorize_settings_window("settings").is_err());
    assert!(settings_method_allowed("permissions.update"));
    assert!(settings_method_allowed("mcp.servers.configure"));
    assert!(settings_method_allowed("memory.settings.get"));
    assert!(settings_method_allowed("memory.settings.update"));
    assert!(settings_method_allowed("memory.proposals.list"));
    assert!(settings_method_allowed("memory.proposals.accept"));
    assert!(settings_method_allowed("memory.proposals.reject"));
    assert!(settings_method_allowed("knowledge.sources.list"));
    assert!(settings_method_allowed("obsidian.health.get"));
    assert!(settings_method_allowed("projects.list"));
    assert!(settings_method_allowed("projects.archived.list"));
    assert!(settings_method_allowed("projects.archived.restore"));
    assert!(settings_method_allowed("projects.archived.delete"));
    assert!(settings_method_allowed("trash.items.list"));
    assert!(settings_method_allowed("trash.items.restore"));
    assert!(settings_method_allowed("trash.items.purge"));
    assert!(settings_method_allowed("trash.items.purge_all"));
    assert!(!settings_method_allowed("assistant.turns.start"));
    assert!(!settings_method_allowed("projects.delete"));
    assert!(!settings_method_allowed("conversations.delete"));
    assert!(!settings_method_allowed("system.actions.execute"));
    assert!(authorize_preferences_reader("main").is_ok());
    assert!(authorize_preferences_reader("pet-input").is_ok());
    assert!(authorize_preferences_reader("pet-render").is_err());
    assert!(authorize_pet_input_window("pet-input").is_ok());
    assert!(authorize_pet_input_window("pet-render").is_err());
    assert!(authorize_pet_input_window("main").is_err());
    assert!(authorize_pet_input_window("settings").is_err());
    assert!(authorize_pet_render_window("pet-render").is_ok());
    assert!(authorize_pet_render_window("pet-input").is_err());
    assert!(authorize_pet_render_window("main").is_err());
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
}

#[test]
fn presence_windows_are_created_serially_after_main_load() {
    let config: serde_json::Value =
        serde_json::from_str(include_str!("../tauri.conf.json")).expect("Tauri config");
    let windows = config["app"]["windows"].as_array().expect("window list");
    assert!(!windows
        .iter()
        .any(|window| { matches!(window["label"].as_str(), Some("pet-render" | "pet-input")) }));

    let [render, input] = presence_window_creation_specs();
    assert_eq!(render.label, "pet-render");
    assert_eq!((render.width, render.height), (640, 260));
    assert_eq!((render.min_width, render.max_width), (640, 640));
    assert!(!render.focusable);
    assert_eq!(input.label, "pet-input");
    assert_eq!((input.width, input.height), (616, 360));
    assert_eq!((input.min_width, input.min_height), (616, 360));
    assert_eq!((input.max_width, input.max_height), (616, 360));
    assert!(input.focusable);

    assert!(windows.iter().all(|window| window["label"] != "settings"));
}

#[test]
fn pet_input_sits_below_the_core_inside_the_unified_liquid_surface() {
    let render = PetWindowFrame {
        x: -1280,
        y: 240,
        width: 640,
        height: 260,
    };
    let core = anchored_pet_core_frame(render, 144, 1.0, ExpansionDirection::Right);
    assert_eq!(
        core,
        PetWindowFrame {
            x: -1256,
            y: 256,
            width: 144,
            height: 144,
        }
    );

    let compact = anchored_pet_input_frame(render, 300, 260, 260);
    assert_eq!(
        compact,
        PetWindowFrame {
            x: -1256,
            y: 240,
            width: 300,
            height: 260,
        }
    );

    let expanded = anchored_pet_input_frame(render, 616, 360, 260);
    assert_eq!(
        expanded,
        PetWindowFrame {
            x: -1256,
            y: 140,
            width: 616,
            height: 360,
        }
    );
    assert_eq!(
        compact.y + compact.height as i32,
        render.y + render.height as i32
    );
    assert_eq!(
        expanded.y + expanded.height as i32,
        render.y + render.height as i32
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

#[test]
fn desktop_data_override_requires_an_absolute_path() {
    let default = PathBuf::from(r"C:\Users\Fairy\AppData\Roaming\Fairy");
    assert_eq!(
        resolve_desktop_data_dir(default.clone(), None).expect("default data dir"),
        default
    );
    assert_eq!(
        resolve_desktop_data_dir(
            PathBuf::from(r"C:\ignored"),
            Some(OsString::from(r"D:\FairyTests\isolated")),
        )
        .expect("absolute override"),
        PathBuf::from(r"D:\FairyTests\isolated")
    );
    assert!(resolve_desktop_data_dir(
        PathBuf::from(r"C:\default"),
        Some(OsString::from(r"relative\data")),
    )
    .is_err());
}
