use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

#[derive(Deserialize)]
struct RuntimeManifest {
    schema_version: u32,
    build_profile: String,
    runtime_compatibility: String,
    upstream_revision: String,
    patch_set_digest: String,
    components: Vec<RuntimeComponent>,
}

#[derive(Deserialize)]
struct RuntimeComponent {
    name: String,
    package: String,
    version: String,
    license: String,
    bytes: u64,
    sha256: String,
}

fn sha256(path: &Path) -> String {
    let bytes = std::fs::read(path).expect("read staged Omni runtime component");
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn verify_production_omni_stage(root: &Path) {
    let manifest_path = root.join("runtime-components.json");
    let manifest_bytes =
        std::fs::read(&manifest_path).expect("production build requires Omni component manifest");
    let manifest: RuntimeManifest =
        serde_json::from_slice(&manifest_bytes).expect("parse Omni component manifest");
    assert_eq!(
        manifest.schema_version, 1,
        "unsupported Omni component schema"
    );
    assert_eq!(
        manifest.build_profile, "production-cuda",
        "production build requires a production-cuda Omni manifest"
    );
    assert_eq!(
        manifest.runtime_compatibility, "fairy-omni-runtime-v1",
        "Omni runtime compatibility is unsupported"
    );
    assert!(
        is_lower_hex(&manifest.upstream_revision, 40)
            && is_lower_hex(&manifest.patch_set_digest, 64),
        "Omni runtime source identity is malformed"
    );

    let expected: BTreeSet<&str> = [
        "build-profile.txt",
        "cublas64_13.dll",
        "cublasLt64_13.dll",
        "fairy-omni-runtime.exe",
    ]
    .into_iter()
    .collect();
    let actual: BTreeSet<&str> = manifest
        .components
        .iter()
        .map(|component| component.name.as_str())
        .collect();
    assert_eq!(
        actual, expected,
        "production Omni runtime component set is not exact"
    );
    assert_eq!(
        actual.len(),
        manifest.components.len(),
        "production Omni runtime component names are duplicated"
    );

    for component in &manifest.components {
        assert!(
            !component.name.contains('/')
                && !component.name.contains('\\')
                && !component.package.trim().is_empty()
                && !component.version.trim().is_empty()
                && !component.license.trim().is_empty()
                && component.bytes > 0
                && is_lower_hex(&component.sha256, 64),
            "production Omni runtime component metadata is invalid"
        );
        let path = root.join(&component.name);
        let metadata =
            std::fs::metadata(&path).expect("production Omni runtime component is missing");
        assert_eq!(
            metadata.len(),
            component.bytes,
            "production Omni runtime component size mismatch"
        );
        assert_eq!(
            sha256(&path),
            component.sha256,
            "production Omni runtime component digest mismatch"
        );
    }

    let allowed: BTreeSet<&str> = expected
        .iter()
        .copied()
        .chain([".gitkeep", "runtime-components.json"])
        .collect();
    for entry in std::fs::read_dir(root).expect("read production Omni stage") {
        let entry = entry.expect("read production Omni stage entry");
        assert!(
            entry.file_type().expect("read Omni entry type").is_file(),
            "production Omni stage contains a directory"
        );
        let name = entry
            .file_name()
            .into_string()
            .expect("production Omni stage file name must be Unicode");
        assert!(
            allowed.contains(name.as_str()),
            "production Omni stage contains an unknown file"
        );
    }
}

fn main() {
    println!("cargo:rerun-if-changed=runtime/omni/build-profile.txt");
    println!("cargo:rerun-if-changed=runtime/omni/fairy-omni-runtime.exe");
    println!("cargo:rerun-if-changed=runtime/omni/cublas64_13.dll");
    println!("cargo:rerun-if-changed=runtime/omni/cublasLt64_13.dll");
    println!("cargo:rerun-if-changed=runtime/omni/runtime-components.json");
    if std::env::var("PROFILE").as_deref() == Ok("debug") {
        let binary = PathBuf::from("runtime/voice-worker/fairy-voice-worker.exe");
        if !binary.exists() {
            std::fs::create_dir_all(binary.parent().expect("sidecar directory"))
                .expect("create debug sidecar directory");
            std::fs::write(&binary, b"debug-only-placeholder")
                .expect("create debug sidecar placeholder");
        }
    } else {
        let omni_root = PathBuf::from("runtime/omni");
        let runtime = omni_root.join("fairy-omni-runtime.exe");
        let profile = std::fs::read_to_string("runtime/omni/build-profile.txt")
            .expect("production build requires a staged Omni runtime profile");
        assert!(
            runtime.is_file() && profile.trim() == "production-cuda",
            "production build requires a verified production-cuda Omni runtime"
        );
        verify_production_omni_stage(&omni_root);
    }
    tauri_build::build()
}
