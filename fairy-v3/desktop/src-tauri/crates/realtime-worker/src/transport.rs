use std::net::TcpStream;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;
use thiserror::Error;
use tungstenite::client::IntoClientRequest;
use tungstenite::http::header::AUTHORIZATION;
use tungstenite::http::StatusCode;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message, WebSocket};
use url::Url;
use zeroize::Zeroizing;

use crate::backend::RealtimeCloudProviderKind;
use crate::provider::{
    GeminiProtocol, GlmProtocol, ProviderOutput, ProviderProtocolError, RealtimeProtocol,
};

const GEMINI_ENDPOINT: &str = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent";
const GLM_ENDPOINT: &str = "wss://open.bigmodel.cn/api/paas/v4/realtime";

#[derive(Debug, Error)]
pub enum ProviderTransportError {
    #[error("realtime provider authentication failed")]
    Authentication,
    #[error("realtime provider quota is unavailable")]
    Quota,
    #[error("realtime provider rate limited the session")]
    RateLimited,
    #[error("realtime provider is temporarily unavailable")]
    Unavailable,
    #[error("realtime provider rejected the session configuration")]
    Rejected { code: Option<String> },
    #[error("realtime provider connection failed")]
    Connection,
    #[error("realtime provider connection timed out")]
    TimedOut,
    #[error("realtime provider protocol failed: {0}")]
    Protocol(#[from] ProviderProtocolError),
    #[error("realtime provider returned invalid JSON")]
    InvalidJson,
    #[error("realtime provider closed the session")]
    Closed,
}

impl ProviderTransportError {
    pub const fn public_code(&self) -> &'static str {
        match self {
            Self::Authentication => "REALTIME_PROVIDER_AUTHENTICATION_FAILED",
            Self::Quota => "REALTIME_PROVIDER_QUOTA_EXHAUSTED",
            Self::RateLimited => "REALTIME_PROVIDER_RATE_LIMITED",
            Self::Unavailable => "REALTIME_PROVIDER_UNAVAILABLE",
            Self::Rejected { .. } => "REALTIME_PROVIDER_REQUEST_REJECTED",
            Self::Connection | Self::Closed => "REALTIME_PROVIDER_INTERRUPTED",
            Self::TimedOut => "REALTIME_PROVIDER_TIMEOUT",
            Self::Protocol(_) | Self::InvalidJson => "REALTIME_PROVIDER_PROTOCOL_ERROR",
        }
    }

    pub fn diagnostic_code(&self) -> Option<&str> {
        match self {
            Self::Rejected { code } => code.as_deref(),
            _ => None,
        }
    }
}

pub struct ProviderSocket {
    socket: WebSocket<MaybeTlsStream<TcpStream>>,
    protocol: Box<dyn RealtimeProtocol + Send>,
}

impl ProviderSocket {
    pub fn connect(
        provider: RealtimeCloudProviderKind,
        credential: Zeroizing<String>,
        system_instruction: String,
        video_enabled: bool,
        native_audio: bool,
    ) -> Result<Self, ProviderTransportError> {
        ensure_tls_provider()?;
        let (request, protocol): (_, Box<dyn RealtimeProtocol + Send>) = match provider {
            RealtimeCloudProviderKind::GeminiLive => {
                let mut url =
                    Url::parse(GEMINI_ENDPOINT).map_err(|_| ProviderTransportError::Connection)?;
                url.query_pairs_mut().append_pair("key", &credential);
                let request = url
                    .as_str()
                    .into_client_request()
                    .map_err(|_| ProviderTransportError::Connection)?;
                (
                    request,
                    Box::new(GeminiProtocol {
                        model: "gemini-3.1-flash-live-preview".to_owned(),
                        system_instruction,
                        native_audio,
                    }),
                )
            }
            RealtimeCloudProviderKind::GlmRealtimeFlash
            | RealtimeCloudProviderKind::GlmRealtimeAir => {
                let mut request = GLM_ENDPOINT
                    .into_client_request()
                    .map_err(|_| ProviderTransportError::Connection)?;
                request.headers_mut().insert(
                    AUTHORIZATION,
                    glm_authorization(&credential)
                        .parse()
                        .map_err(|_| ProviderTransportError::Connection)?,
                );
                let model = match provider {
                    RealtimeCloudProviderKind::GlmRealtimeFlash => "glm-realtime-flash",
                    RealtimeCloudProviderKind::GlmRealtimeAir => "glm-realtime-air",
                    RealtimeCloudProviderKind::GeminiLive => unreachable!(),
                };
                (
                    request,
                    Box::new(GlmProtocol {
                        model: model.to_owned(),
                        system_instruction,
                        video_enabled,
                    }),
                )
            }
        };
        let (mut socket, _) = connect(request).map_err(classify_connect_error)?;
        configure_read_timeout(socket.get_mut())?;
        send_json(&mut socket, &protocol.setup())?;
        let mut connected = Self { socket, protocol };
        connected.wait_until_ready()?;
        Ok(connected)
    }

    pub fn send_audio(&mut self, pcm16_le: &[u8]) -> Result<(), ProviderTransportError> {
        let message = self.protocol.audio(pcm16_le)?;
        send_json(&mut self.socket, &message)
    }

    pub fn send_text(&mut self, text: &str) -> Result<(), ProviderTransportError> {
        for message in self.protocol.text(text)? {
            send_json(&mut self.socket, &message)?;
        }
        Ok(())
    }

    pub fn send_video(&mut self, jpeg: &[u8]) -> Result<(), ProviderTransportError> {
        let message = self.protocol.video(jpeg)?;
        send_json(&mut self.socket, &message)
    }

    pub fn send_tool_result(
        &mut self,
        call_id: &str,
        public_output: &str,
    ) -> Result<(), ProviderTransportError> {
        let message = self.protocol.tool_result(call_id, public_output);
        send_json(&mut self.socket, &message)
    }

    pub fn receive(&mut self) -> Result<Vec<ProviderOutput>, ProviderTransportError> {
        loop {
            match self.socket.read() {
                Ok(Message::Text(text)) => {
                    let event: Value = serde_json::from_str(&text)
                        .map_err(|_| ProviderTransportError::InvalidJson)?;
                    if let Some(error) = classify_provider_error(&event) {
                        return Err(error);
                    }
                    return self.protocol.parse(&event).map_err(Into::into);
                }
                Ok(Message::Ping(payload)) => self
                    .socket
                    .send(Message::Pong(payload))
                    .map_err(|_| ProviderTransportError::Connection)?,
                Ok(Message::Close(_)) => return Err(ProviderTransportError::Closed),
                Ok(_) => {}
                Err(tungstenite::Error::Io(error))
                    if matches!(
                        error.kind(),
                        std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                    ) =>
                {
                    return Ok(Vec::new());
                }
                Err(_) => return Err(ProviderTransportError::Connection),
            }
        }
    }

    fn wait_until_ready(&mut self) -> Result<(), ProviderTransportError> {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if self
                .receive()?
                .iter()
                .any(|output| matches!(output, ProviderOutput::Ready))
            {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(5));
        }
        Err(ProviderTransportError::TimedOut)
    }
}

fn ensure_tls_provider() -> Result<(), ProviderTransportError> {
    if rustls::crypto::CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
    if rustls::crypto::CryptoProvider::get_default().is_some() {
        Ok(())
    } else {
        Err(ProviderTransportError::Connection)
    }
}

fn glm_authorization(credential: &str) -> String {
    if credential
        .trim_start()
        .to_ascii_lowercase()
        .starts_with("bearer ")
    {
        credential.trim().to_owned()
    } else {
        format!("Bearer {}", credential.trim())
    }
}

fn classify_connect_error(error: tungstenite::Error) -> ProviderTransportError {
    match error {
        tungstenite::Error::Http(response) => classify_http_status(response.status()),
        _ => ProviderTransportError::Connection,
    }
}

fn classify_http_status(status: StatusCode) -> ProviderTransportError {
    match status.as_u16() {
        401 | 403 => ProviderTransportError::Authentication,
        402 => ProviderTransportError::Quota,
        408 | 504 => ProviderTransportError::TimedOut,
        429 => ProviderTransportError::RateLimited,
        500..=599 => ProviderTransportError::Unavailable,
        400..=499 => ProviderTransportError::Rejected {
            code: Some(format!("HTTP_{}", status.as_u16())),
        },
        _ => ProviderTransportError::Connection,
    }
}

fn classify_provider_error(event: &Value) -> Option<ProviderTransportError> {
    if event
        .get("type")
        .and_then(Value::as_str)
        .is_some_and(|kind| kind != "error")
    {
        return None;
    }
    let error = event.get("error")?;
    let numeric_code = error.get("code").and_then(Value::as_u64);
    if let Some(code) = numeric_code.and_then(|value| u16::try_from(value).ok()) {
        if (400..=599).contains(&code) {
            return Some(classify_http_status(
                StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_REQUEST),
            ));
        }
        if let Some(error) = classify_business_code(&code.to_string()) {
            return Some(error);
        }
    }
    if let Some(error) = error
        .get("code")
        .and_then(Value::as_str)
        .and_then(classify_business_code)
    {
        return Some(error);
    }
    let code = [error.get("type"), error.get("code"), error.get("status")]
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase();
    if code.contains("auth") || code.contains("api_key") || code.contains("unauthorized") {
        Some(ProviderTransportError::Authentication)
    } else if code.contains("quota") || code.contains("balance") || code.contains("insufficient") {
        Some(ProviderTransportError::Quota)
    } else if code.contains("rate") || code.contains("limit") || code.contains("capacity") {
        Some(ProviderTransportError::RateLimited)
    } else if code.contains("unavailable") || code.contains("internal") || code.contains("server") {
        Some(ProviderTransportError::Unavailable)
    } else {
        Some(ProviderTransportError::Rejected {
            code: error
                .get("code")
                .and_then(Value::as_str)
                .and_then(safe_provider_code),
        })
    }
}

fn classify_business_code(value: &str) -> Option<ProviderTransportError> {
    match value.trim() {
        "1000" | "1001" | "1002" | "1003" | "1004" => Some(ProviderTransportError::Authentication),
        "1113" | "1304" | "1308" | "1309" | "1310" => Some(ProviderTransportError::Quota),
        "1302" | "1303" | "1305" | "1312" | "1313" => Some(ProviderTransportError::RateLimited),
        "500" | "1120" | "1234" => Some(ProviderTransportError::Unavailable),
        _ => None,
    }
}

fn safe_provider_code(value: &str) -> Option<String> {
    let code = value
        .chars()
        .take(64)
        .filter(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '_' | '-' | '.')
        })
        .collect::<String>();
    (!code.is_empty()).then_some(code)
}

fn configure_read_timeout(
    stream: &mut MaybeTlsStream<TcpStream>,
) -> Result<(), ProviderTransportError> {
    let timeout = Some(Duration::from_millis(12));
    match stream {
        MaybeTlsStream::Plain(stream) => stream.set_read_timeout(timeout),
        MaybeTlsStream::Rustls(stream) => stream.get_mut().set_read_timeout(timeout),
        _ => return Err(ProviderTransportError::Connection),
    }
    .map_err(|_| ProviderTransportError::Connection)
}

impl Drop for ProviderSocket {
    fn drop(&mut self) {
        let _ = self.socket.close(None);
    }
}

fn send_json(
    socket: &mut WebSocket<MaybeTlsStream<TcpStream>>,
    value: &Value,
) -> Result<(), ProviderTransportError> {
    socket
        .send(Message::Text(value.to_string().into()))
        .map_err(|_| ProviderTransportError::Connection)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endpoints_are_secure_websockets_and_do_not_contain_credentials() {
        assert!(GEMINI_ENDPOINT.starts_with("wss://"));
        assert!(GLM_ENDPOINT.starts_with("wss://"));
        assert!(!GEMINI_ENDPOINT.contains("key="));
        assert!(!GLM_ENDPOINT.contains("Authorization"));
    }

    #[test]
    fn tls_crypto_provider_is_installed_idempotently() {
        ensure_tls_provider().expect("first install");
        ensure_tls_provider().expect("second install");
        assert!(rustls::crypto::CryptoProvider::get_default().is_some());
    }

    #[test]
    fn glm_credentials_use_bearer_auth_without_double_prefixing() {
        assert_eq!(glm_authorization("secret"), "Bearer secret");
        assert_eq!(glm_authorization("Bearer token"), "Bearer token");
    }

    #[test]
    fn websocket_statuses_are_classified_without_exposing_response_bodies() {
        assert!(matches!(
            classify_http_status(StatusCode::UNAUTHORIZED),
            ProviderTransportError::Authentication
        ));
        assert!(matches!(
            classify_http_status(StatusCode::TOO_MANY_REQUESTS),
            ProviderTransportError::RateLimited
        ));
        assert!(matches!(
            classify_http_status(StatusCode::SERVICE_UNAVAILABLE),
            ProviderTransportError::Unavailable
        ));
        assert_eq!(
            classify_http_status(StatusCode::BAD_REQUEST).diagnostic_code(),
            Some("HTTP_400")
        );
    }

    #[test]
    fn provider_error_events_are_reduced_to_safe_categories() {
        assert!(matches!(
            classify_provider_error(&serde_json::json!({
                "type": "error",
                "error": {"code": 1113, "message": "sensitive provider detail"}
            })),
            Some(ProviderTransportError::Quota)
        ));
        assert!(matches!(
            classify_provider_error(&serde_json::json!({
                "type": "error",
                "error": {"code": "1113", "message": "account detail"}
            })),
            Some(ProviderTransportError::Quota)
        ));
        assert!(matches!(
            classify_provider_error(&serde_json::json!({
                "type": "error",
                "error": {"code": "1303", "message": "rate detail"}
            })),
            Some(ProviderTransportError::RateLimited)
        ));
        assert!(matches!(
            classify_provider_error(&serde_json::json!({
                "type": "error",
                "error": {"code": 1303, "message": "rate detail"}
            })),
            Some(ProviderTransportError::RateLimited)
        ));
        assert!(matches!(
            classify_provider_error(&serde_json::json!({
                "type": "error",
                "error": {"type": "invalid_api_key", "message": "do not surface this"}
            })),
            Some(ProviderTransportError::Authentication)
        ));
        assert!(classify_provider_error(&serde_json::json!({
            "type": "conversation.item.input_audio_transcription.failed",
            "error": {"type": "ASR_ERROR", "code": "asr_no_result"}
        }))
        .is_none());
        let rejected = classify_provider_error(&serde_json::json!({
            "type": "error",
            "error": {"type": "invalid_request_error", "code": "invalid-event<script>"}
        }))
        .expect("rejected event");
        assert_eq!(rejected.diagnostic_code(), Some("invalid-eventscript"));
    }
}
