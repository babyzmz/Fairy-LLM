#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if std::env::args().nth(1).as_deref() == Some("--local-worker") {
        let managed_root = std::env::var_os("FAIRY_MANAGED_ROOT")
            .map(std::path::PathBuf::from)
            .expect("FAIRY_MANAGED_ROOT is required for local worker mode");
        fairy_local_worker::run_stdio(managed_root).expect("Fairy Local Worker failed");
        return;
    }
    fairy_desktop_v3::run();
}
