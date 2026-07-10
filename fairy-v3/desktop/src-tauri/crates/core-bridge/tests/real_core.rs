use std::path::PathBuf;
use std::time::{Duration, Instant};

use fairy_core_bridge::{CoreBridge, CoreLaunchSpec};
use serde_json::json;
use tempfile::tempdir;

#[test]
fn development_spec_starts_real_core_within_readiness_budget() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let core_root = manifest.join("../../../../core");
    let data = tempdir().expect("temporary data directory");
    let started_at = Instant::now();
    let bridge = CoreBridge::spawn(CoreLaunchSpec::development(&core_root, data.path()))
        .expect("launch real Fairy Core");

    let response = bridge
        .call(json!({"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}}))
        .expect("Core health response");

    assert_eq!(response["result"]["status"], "ok");
    assert!(started_at.elapsed() < Duration::from_secs(3));
}
