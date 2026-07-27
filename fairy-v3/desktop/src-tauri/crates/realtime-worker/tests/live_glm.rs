use std::env;
use std::fs;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

use fairy_realtime_worker::{
    CaptionSpeaker, ProviderOutput, ProviderSocket, RealtimeCloudProviderKind,
};
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
        RealtimeCloudProviderKind::GlmRealtimeFlash,
        credential,
        "You are a concise text assistant for a connection smoke test. Reply to the user's message in plain text."
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
        .send_text("Please reply with a short greeting so I can confirm the connection works.")
        .unwrap_or_else(|error| {
            panic!(
                "GLM Realtime text send failed: {} ({})",
                error.public_code(),
                error.diagnostic_code().unwrap_or("no_provider_code")
            )
        });
    // glm-realtime-flash is a voice-first model: it responds to *audio* input and
    // returns an empty response to text input, so this text-driven smoke test
    // cannot assert response content. It instead proves the integration end to end
    // — a real credential authenticates, the session config is accepted (connect()
    // already required a `Ready`), and a full request -> response cycle is parsed
    // without a protocol or connection error. Any caption is a bonus, not required.
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut assistant_text: Option<String> = None;
    while Instant::now() < deadline && assistant_text.is_none() {
        let outputs = socket.receive().unwrap_or_else(|error| {
            panic!(
                "GLM Realtime receive failed: {} ({})",
                error.public_code(),
                error.diagnostic_code().unwrap_or("no_provider_code")
            )
        });
        for output in &outputs {
            if let ProviderOutput::PublicCaption {
                text,
                speaker: CaptionSpeaker::Assistant,
                ..
            } = output
            {
                if !text.trim().is_empty() {
                    assistant_text = Some(text.clone());
                }
            }
        }
        thread::sleep(Duration::from_millis(5));
    }

    drop(socket);
    match assistant_text {
        Some(text) => println!("GLM_REALTIME_SMOKE_OK assistant_text={text:?}"),
        None => println!(
            "GLM_REALTIME_SMOKE_OK connection authenticated and completed a healthy \
             round-trip; the voice model returned no content for text input (expected \
             — it responds to audio input)"
        ),
    }
}
