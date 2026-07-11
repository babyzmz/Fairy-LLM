use std::collections::BTreeMap;
use std::fs;

use fairy_core_bridge::CoreLaunchSpec;
use tempfile::tempdir;

#[test]
fn development_spec_prefers_composed_runtime_and_filters_parent_environment() {
    let root = tempdir().expect("temporary product root");
    let core_root = root.path().join("core");
    let capabilities_root = root.path().join("capabilities");
    fs::create_dir_all(core_root.join("src")).expect("core source");
    fs::create_dir_all(capabilities_root.join("src/fairy_capabilities"))
        .expect("capabilities source");
    fs::write(
        capabilities_root.join("src/fairy_capabilities/stdio.py"),
        "",
    )
    .expect("stdio module");
    let windows_python = capabilities_root.join(".venv/Scripts/python.exe");
    let unix_python = capabilities_root.join(".venv/bin/python");
    fs::create_dir_all(windows_python.parent().expect("windows parent")).expect("windows venv");
    fs::create_dir_all(unix_python.parent().expect("unix parent")).expect("unix venv");
    fs::write(&windows_python, "").expect("windows python");
    fs::write(&unix_python, "").expect("unix python");

    let parent_environment = BTreeMap::from([
        ("PATH".to_owned(), "trusted-path".to_owned()),
        ("UNRELATED_SECRET".to_owned(), "must-not-pass".to_owned()),
        (
            "FAIRY_PROVIDER_SECRET_OPENROUTER".to_owned(),
            "provider-secret".to_owned(),
        ),
        ("FAIRY_PROVIDER_PROFILES_JSON".to_owned(), "[]".to_owned()),
        (
            "FAIRY_CORE_MODULE".to_owned(),
            "fairy_capabilities.stdio".to_owned(),
        ),
    ]);
    let spec = CoreLaunchSpec::development_with_environment(
        &core_root,
        root.path().join("data"),
        &parent_environment,
    );

    assert!(spec.clear_environment);
    assert_eq!(spec.args[2], "fairy_capabilities.stdio");
    assert_eq!(spec.env["PATH"], "trusted-path");
    assert_eq!(
        spec.env["FAIRY_PROVIDER_SECRET_OPENROUTER"],
        "provider-secret"
    );
    assert!(!spec.env.contains_key("UNRELATED_SECRET"));
    assert!(!spec.env.contains_key("FAIRY_CORE_MODULE"));
    let python_path = &spec.env["PYTHONPATH"];
    assert!(python_path.contains("capabilities"));
    assert!(python_path.contains("core"));
}
