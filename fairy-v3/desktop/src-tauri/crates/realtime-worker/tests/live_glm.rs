use std::env;
use std::fs;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

use fairy_realtime_worker::{CaptionSpeaker, ProviderKind, ProviderOutput, ProviderSocket};
use zeroize::Zeroizing;

#[test]
#[ignore = "requires FAIRY_ZHIPU_API_KEY_FILE and live provider access"]
fn glm_realtime_flash_accepts_the_configured_credential() {
    let path = env::var_os("FAIRY_ZHIPU_API_KEY_FILE")
        .map(PathBuf::from)
        .expect("FAIRY_ZHIPU_API_KEY_FILE is required");
    let file = Zeroizing::new(fs::read_to_string(path).expect("credential file must be readable"));
    let credential = Zeroizing::new(file.trim().to_owned());
    assert!(!credential.is_empty(), "credential file must not be empty");

    let mut socket = ProviderSocket::connect(
        ProviderKind::GlmRealtimeFlash,
        credential,
        "This is a connection health check. Do not produce a response until client input arrives."
            .to_owned(),
        false,
        false,
    )
    .unwrap_or_else(|error| {
        panic!(
            "GLM Realtime smoke failed: {} ({})",
            error.public_code(),
            error.diagnostic_code().unwrap_or("no_provider_code")
        )
    });

    socket
        .send_text("Reply only with: connection successful")
        .unwrap_or_else(|error| {
            panic!(
                "GLM Realtime text send failed: {} ({})",
                error.public_code(),
                error.diagnostic_code().unwrap_or("no_provider_code")
            )
        });
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut received_assistant_text = false;
    while Instant::now() < deadline && !received_assistant_text {
        let outputs = socket.receive().unwrap_or_else(|error| {
            panic!(
                "GLM Realtime text receive failed: {} ({})",
                error.public_code(),
                error.diagnostic_code().unwrap_or("no_provider_code")
            )
        });
        received_assistant_text = outputs.iter().any(|output| {
            matches!(
                output,
                ProviderOutput::PublicCaption {
                    text,
                    speaker: CaptionSpeaker::Assistant,
                    ..
                } if !text.trim().is_empty()
            )
        });
        thread::sleep(Duration::from_millis(5));
    }
    assert!(
        received_assistant_text,
        "GLM Realtime did not return assistant text before the deadline"
    );

    drop(socket);
    println!("GLM_REALTIME_TEXT_SMOKE_OK");
}
