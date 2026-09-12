//! Explicit, opt-in credential validation using the production DPAPI store.
use super::*;
use base64::Engine;

struct TestCore(CoreBridge);
impl Drop for TestCore {
    fn drop(&mut self) {
        self.0.shutdown();
    }
}

#[test]
#[ignore = "explicit synthetic text provider and offline dictation acceptance"]
fn composer_live_text_and_local_dictation() {
    let directory =
        PathBuf::from(env::var_os("FAIRY_COMPOSER_DATA_DIR").expect("credential directory"));
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.tmp");
    std::fs::create_dir_all(&root).unwrap();
    let data = tempfile::Builder::new()
        .prefix("composer-live-")
        .tempdir_in(root)
        .unwrap();
    let desktop = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/debug/fairy.exe");
    assert!(
        desktop.is_file(),
        "Build the dev desktop/local worker before this test"
    );
    let mut launch = configured_core_launch(&directory, &desktop, &directory).unwrap();
    // Keep real secure provider configuration, but never read/send existing chats.
    launch.env.insert(
        "FAIRY_V3_DATA_DIR".to_owned(),
        data.path().to_string_lossy().into_owned(),
    );
    let core = TestCore(CoreBridge::spawn_verified(launch).expect("start isolated Core"));
    let call = |method: &str, params: Value| {
        let value = core
            .0
            .call(json!({"id": 1, "method": method, "params": params}))
            .expect("Core transport");
        if let Some(error) = value.get("error") {
            println!(
                "RPC failure codes: {}",
                json!({"rpc": error["code"], "domain": error.pointer("/data/error_code")})
            );
        }
        assert!(value.get("error").is_none(), "RPC failed: {method}");
        value["result"].clone()
    };
    let fixture =
        PathBuf::from(env::var_os("FAIRY_COMPOSER_AUDIO_FIXTURE").expect("synthetic WAV path"));
    let audio = base64::engine::general_purpose::STANDARD.encode(std::fs::read(fixture).unwrap());
    let mut conversations = Vec::new();
    for _ in 0..2 {
        let conversation = call(
            "conversations.create",
            json!({"project_id": null, "workspace_type": "chat_scratch"}),
        );
        let transcript = call(
            "voice.transcribe",
            json!({
                "conversation_id": conversation["id"], "profile_id": "local-whisper-small",
                "media_type": "audio/wav", "audio_base64": audio, "language": "en"
            }),
        );
        assert_eq!(transcript["conversation_id"], conversation["id"]);
        assert_eq!(transcript["profile_id"], "local-whisper-small");
        assert!(transcript["text"]
            .as_str()
            .unwrap()
            .contains("local transcription test"));
        conversations.push(conversation["id"].clone());
    }
    println!("Offline Core dictation passed in two separate conversations");
    let task = call(
        "tasks.create",
        json!({
            "conversation_id": conversations[0], "user_request": "Please only say hello. Do not call tools, read files, or change anything.",
            "operation_mode": "answer", "execution_target": "local", "idempotency_key": "composer-live-task"
        }),
    );
    let turn = call(
        "assistant.turns.create",
        json!({
            "task_id": task["task"]["id"], "profile_id": "openrouter-deepseek-v4-pro", "idempotency_key": "composer-live-turn"
        }),
    );
    call("assistant.turns.start", json!({"turn_id": turn["id"]}));
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(120);
    loop {
        let current = call("assistant.turns.get", json!({"turn_id": turn["id"]}));
        let status = current["status"].as_str().unwrap_or("unknown");
        if matches!(
            status,
            "completed"
                | "failed"
                | "cancelled"
                | "waiting_for_clarification"
                | "waiting_for_approval"
        ) {
            println!("Synthetic text turn status: {status}");
            assert_eq!(status, "completed", "Synthetic text turn did not complete");
            break;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "Synthetic text turn timed out"
        );
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

#[test]
#[ignore = "requires explicit key-file and data-directory authorization"]
fn validate_composer_openrouter_credentials() {
    let source = PathBuf::from(env::var_os("FAIRY_COMPOSER_KEY_FILE").expect("key file path"));
    let directory = PathBuf::from(env::var_os("FAIRY_COMPOSER_DATA_DIR").expect("data directory"));
    assert!(source.is_absolute() && directory.is_absolute());
    let secret = zeroize::Zeroizing::new(std::fs::read_to_string(source).expect("read key file"));
    assert!(
        secret.trim().starts_with("sk-or-"),
        "invalid credential format"
    );
    let store = ProviderCredentialStore::new(&directory);
    let configuration = ProviderConfigurationStore::new(&directory);
    let existed = configuration
        .load_openrouter()
        .expect("read configuration")
        .is_some();
    let replacement = store
        .begin_openrouter_replacement(secret.trim())
        .expect("protect candidate");
    configuration
        .save_openrouter()
        .expect("save public configuration");
    let result = (|| -> Result<bool, Box<dyn std::error::Error>> {
        let launch = configured_core_launch(&directory, &env::current_exe()?, &directory)?;
        let bridge = CoreBridge::spawn_verified(launch)?;
        let response = bridge.call(model_catalog_refresh_request());
        bridge.shutdown();
        if let Ok(value) = &response {
            println!(
                "catalog validation status: {}",
                json!({
                    "credential_status": value.pointer("/result/account/credential_status"),
                    "stale": value.pointer("/result/stale"),
                    "error_code": value.pointer("/result/last_error_code"),
                    "rpc_error_code": value.pointer("/error/code"),
                })
            );
        }
        Ok(catalog_refresh_is_configured(&response?))
    })();
    if !matches!(result, Ok(true)) {
        replacement
            .rollback()
            .expect("restore prior protected credential");
        if !existed {
            configuration
                .delete_openrouter()
                .expect("restore prior configuration");
        }
        panic!("OpenRouter validation did not succeed; prior credential restored");
    }
    replacement.commit();
    println!("OpenRouter credential securely stored and catalog validation passed");
}
