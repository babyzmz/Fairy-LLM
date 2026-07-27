fn main() {
    println!("cargo:rerun-if-changed=runtime/omni/build-profile.txt");
    println!("cargo:rerun-if-changed=runtime/omni/fairy-omni-runtime.exe");
    if std::env::var("PROFILE").as_deref() == Ok("debug") {
        let binary = std::path::PathBuf::from("runtime/voice-worker/fairy-voice-worker.exe");
        if !binary.exists() {
            std::fs::create_dir_all(binary.parent().expect("sidecar directory"))
                .expect("create debug sidecar directory");
            std::fs::write(&binary, b"debug-only-placeholder")
                .expect("create debug sidecar placeholder");
        }
    } else {
        let runtime = std::path::PathBuf::from("runtime/omni/fairy-omni-runtime.exe");
        let profile = std::fs::read_to_string("runtime/omni/build-profile.txt")
            .expect("production build requires a staged Omni runtime profile");
        assert!(
            runtime.is_file() && profile.trim() == "production-cuda",
            "production build requires a verified production-cuda Omni runtime"
        );
    }
    tauri_build::build()
}
