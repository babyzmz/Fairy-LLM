#[test]
fn crate_root_does_not_mix_workspace_and_protocol_implementations() {
    let crate_root = include_str!("../src/lib.rs");

    assert!(!crate_root.contains("struct WorkspaceManager"));
    assert!(!crate_root.contains("struct WorkerRequest"));
    assert!(!crate_root.contains("fn execute_method"));
    assert!(!crate_root.contains("fn run_git"));
}
