use std::net::TcpStream;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;
use thiserror::Error;
use tungstenite::client::IntoClientRequest;
use tungstenite::http::header::AUTHORIZATION;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message, WebSocket};
use url::Url;
use zeroize::Zeroizing;

use crate::protocol::ProviderKind;
use crate::provider::{
    GeminiProtocol, GlmProtocol, ProviderOutput, ProviderProtocolError, RealtimeProtocol,
};

const GEMINI_ENDPOINT: &str = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent";
const GLM_ENDPOINT: &str = "wss://open.bigmodel.cn/api/paas/v4/realtime";

#[derive(Debug, Error)]
pub enum ProviderTransportError {
    #[error("realtime provider connection failed")]
    Connection,
    #[error("realtime provider protocol failed: {0}")]
    Protocol(#[from] ProviderProtocolError),
    #[error("realtime provider returned invalid JSON")]
    InvalidJson,
    #[error("realtime provider closed the session")]
    Closed,
}

pub struct ProviderSocket {
    socket: WebSocket<MaybeTlsStream<TcpStream>>,
    protocol: Box<dyn RealtimeProtocol + Send>,
}

impl ProviderSocket {
    pub fn connect(
        provider: ProviderKind,
        credential: Zeroizing<String>,
        system_instruction: String,
        video_enabled: bool,
        native_audio: bool,
    ) -> Result<Self, ProviderTransportError> {
        let (request, protocol): (_, Box<dyn RealtimeProtocol + Send>) = match provider {
            ProviderKind::GeminiLive => {
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
            ProviderKind::GlmRealtimeFlash | ProviderKind::GlmRealtimeAir => {
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
                    ProviderKind::GlmRealtimeFlash => "glm-realtime-flash",
                    ProviderKind::GlmRealtimeAir => "glm-realtime-air",
                    ProviderKind::GeminiLive => unreachable!(),
                };
                (
                    request,
                    Box::new(GlmProtocol {
                        model: model.to_owned(),
                        system_instruction,
                        video_enabled,
                        native_audio,
                    }),
                )
            }
        };
        let (mut socket, _) = connect(request).map_err(|_| ProviderTransportError::Connection)?;
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
    fn glm_credentials_use_bearer_auth_without_double_prefixing() {
        assert_eq!(glm_authorization("secret"), "Bearer secret");
        assert_eq!(glm_authorization("Bearer token"), "Bearer token");
    }
}
