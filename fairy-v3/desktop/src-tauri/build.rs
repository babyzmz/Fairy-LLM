fn main() {
    if std::env::var("PROFILE").as_deref() == Ok("debug") {
        let binary = std::path::PathBuf::from("runtime/voice-worker/fairy-voice-worker.exe");
        if !binary.exists() {
            std::fs::create_dir_all(binary.parent().expect("sidecar directory"))
                .expect("create debug sidecar directory");
            std::fs::write(&binary, b"debug-only-placeholder")
                .expect("create debug sidecar placeholder");
        }
    }
    tauri_build::build()
}
