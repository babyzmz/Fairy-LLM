#[test]
fn crate_root_does_not_mix_workspace_and_protocol_implementations() {
    let crate_root = include_str!("../src/lib.rs");

    assert!(!crate_root.contains("struct WorkspaceManager"));
    assert!(!crate_root.contains("struct WorkerRequest"));
    assert!(!crate_root.contains("fn execute_method"));
    assert!(!crate_root.contains("fn run_git"));
}

#[test]
fn typed_system_actions_do_not_gain_process_or_arbitrary_input_apis() {
    let source = include_str!("../src/system_actions.rs");
    for forbidden in [
        "std::process",
        "process::Command",
        "Command::new",
        "CreateProcess",
        "powershell",
        "cmd.exe",
    ] {
        assert!(
            !source.contains(forbidden),
            "system action adapter contains forbidden process API: {forbidden}"
        );
    }

    let protocol = include_str!("../src/protocol.rs");
    for forbidden_field in [
        "program: String",
        "args: Vec",
        "command: String",
        "shell: String",
        "script: String",
        "registry: String",
        "keyboard: String",
        "pointer: String",
    ] {
        assert!(
            !protocol.contains(forbidden_field),
            "worker protocol exposes forbidden field: {forbidden_field}"
        );
    }
}
