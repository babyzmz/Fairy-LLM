use rand::RngCore;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;
use std::sync::mpsc::{self, Sender, TryRecvError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};

use crate::{validate_identifier, WorkerError};

const LOOPBACK_HOST: &str = "127.0.0.1";
const POLL_INTERVAL: Duration = Duration::from_millis(25);
const MAX_EXPIRY_SECONDS: u64 = 300;

#[derive(Debug, Clone, serde::Deserialize)]
pub struct FileReadRequest {
    pub session_id: String,
    pub workspace_id: String,
    pub version_id: String,
    pub relative_path: String,
    pub content_hash: String,
    pub byte_length: u64,
    pub media_type: String,
    pub expires_seconds: u64,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct FileReadInfo {
    pub session_id: String,
    pub workspace_id: String,
    pub version_id: String,
    pub path: String,
    pub content_hash: String,
    pub byte_length: u64,
    pub media_type: String,
    pub url: String,
    pub expires_unix_ms: u128,
}

struct ReadRecord {
    stop: Option<Sender<()>>,
    thread: Option<JoinHandle<()>>,
}

struct ReadRegistry {
    records: Mutex<HashMap<String, ReadRecord>>,
}

impl Drop for ReadRegistry {
    fn drop(&mut self) {
        let records = self
            .records
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        for (_id, mut record) in records.drain() {
            if let Some(stop) = record.stop.take() {
                let _ignored = stop.send(());
            }
            if let Some(thread) = record.thread.take() {
                let _ignored = thread.join();
            }
        }
    }
}

#[derive(Clone)]
pub struct FileReadManager {
    registry: Arc<ReadRegistry>,
}

impl FileReadManager {
    pub fn new() -> Self {
        Self {
            registry: Arc::new(ReadRegistry {
                records: Mutex::new(HashMap::new()),
            }),
        }
    }

    pub fn open(
        &self,
        request: FileReadRequest,
        source: PathBuf,
    ) -> Result<FileReadInfo, WorkerError> {
        validate_identifier(&request.session_id)?;
        validate_identifier(&request.workspace_id)?;
        validate_identifier(&request.version_id)?;
        if !(1..=MAX_EXPIRY_SECONDS).contains(&request.expires_seconds) {
            return Err(WorkerError::ReadStream(
                "expiry is outside policy".to_owned(),
            ));
        }
        if request.content_hash.len() != 64
            || !request
                .content_hash
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(WorkerError::ReadStream("invalid source digest".to_owned()));
        }
        let source = source.canonicalize()?;
        if source.metadata()?.len() != request.byte_length {
            return Err(WorkerError::ObjectDigestMismatch);
        }
        let mut source_file = File::open(&source)?;
        let mut source_digest = Sha256::new();
        let mut digest_buffer = vec![0_u8; 1024 * 1024];
        loop {
            let count = source_file.read(&mut digest_buffer)?;
            if count == 0 {
                break;
            }
            source_digest.update(&digest_buffer[..count]);
        }
        if format!("{:x}", source_digest.finalize()) != request.content_hash {
            return Err(WorkerError::ObjectDigestMismatch);
        }
        source_file.seek(SeekFrom::Start(0))?;
        let mut token_bytes = [0_u8; 32];
        rand::rngs::OsRng.fill_bytes(&mut token_bytes);
        let token = token_bytes
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let token_hash = Sha256::digest(token.as_bytes()).to_vec();
        let server = Server::http((LOOPBACK_HOST, 0))
            .map_err(|error| WorkerError::ReadStream(error.to_string()))?;
        let address = server
            .server_addr()
            .to_ip()
            .ok_or_else(|| WorkerError::ReadStream("listener is not TCP".to_owned()))?;
        if address.ip().to_string() != LOOPBACK_HOST {
            return Err(WorkerError::ReadStream(
                "listener is not exact loopback".to_owned(),
            ));
        }
        let deadline = Instant::now() + Duration::from_secs(request.expires_seconds);
        let expires_unix_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis()
            + u128::from(request.expires_seconds) * 1000;
        let path = format!("/read/{}/{}", request.session_id, token);
        let url = format!("http://{LOOPBACK_HOST}:{}{}", address.port(), path);
        let (stop_sender, stop_receiver) = mpsc::channel();
        let thread_path = path.clone();
        let thread_media_type = request.media_type.clone();
        let thread = thread::Builder::new()
            .name(format!("fairy-read-{}", request.session_id))
            .spawn(move || loop {
                if Instant::now() >= deadline {
                    break;
                }
                match stop_receiver.try_recv() {
                    Ok(()) | Err(TryRecvError::Disconnected) => break,
                    Err(TryRecvError::Empty) => {}
                }
                match server.recv_timeout(POLL_INTERVAL) {
                    Ok(Some(incoming)) => serve(
                        incoming,
                        &thread_path,
                        &token_hash,
                        &source_file,
                        request.byte_length,
                        &thread_media_type,
                    ),
                    Ok(None) => {}
                    Err(_error) => break,
                }
            })?;
        self.revoke(&request.session_id).ok();
        self.lock_records()?.insert(
            request.session_id.clone(),
            ReadRecord {
                stop: Some(stop_sender),
                thread: Some(thread),
            },
        );
        Ok(FileReadInfo {
            session_id: request.session_id,
            workspace_id: request.workspace_id,
            version_id: request.version_id,
            path: request.relative_path,
            content_hash: request.content_hash,
            byte_length: request.byte_length,
            media_type: request.media_type,
            url,
            expires_unix_ms,
        })
    }

    pub fn revoke(&self, session_id: &str) -> Result<(), WorkerError> {
        validate_identifier(session_id)?;
        let record = self.lock_records()?.remove(session_id);
        if let Some(mut record) = record {
            if let Some(stop) = record.stop.take() {
                let _ignored = stop.send(());
            }
            if let Some(thread) = record.thread.take() {
                thread
                    .join()
                    .map_err(|_| WorkerError::ReadStream("server thread panicked".to_owned()))?;
            }
        }
        Ok(())
    }

    fn lock_records(&self) -> Result<MutexGuard<'_, HashMap<String, ReadRecord>>, WorkerError> {
        self.registry
            .records
            .lock()
            .map_err(|_| WorkerError::LockPoisoned)
    }
}

fn serve(
    request: Request,
    expected_path: &str,
    expected_token_hash: &[u8],
    source: &File,
    byte_length: u64,
    media_type: &str,
) {
    if !matches!(request.method(), Method::Get | Method::Head) {
        let _ignored = request.respond(Response::empty(StatusCode(405)));
        return;
    }
    let path = request.url().split('?').next().unwrap_or(request.url());
    let supplied_token = path.rsplit('/').next().unwrap_or_default();
    let supplied_hash = Sha256::digest(supplied_token.as_bytes());
    if path != expected_path || !constant_time_eq(supplied_hash.as_slice(), expected_token_hash) {
        let _ignored = request.respond(Response::empty(StatusCode(404)));
        return;
    }
    let range_header = request
        .headers()
        .iter()
        .find(|header| header.field.equiv("Range"))
        .map(|header| header.value.as_str());
    if byte_length == 0 {
        let status = if range_header.is_some() {
            StatusCode(416)
        } else {
            StatusCode(200)
        };
        let _ignored = request.respond(Response::empty(status));
        return;
    }
    let (start, end) = match parse_range(range_header, byte_length) {
        Ok(range) => range,
        Err(()) => {
            let mut response = Response::empty(StatusCode(416));
            response.add_header(header("Content-Range", &format!("bytes */{byte_length}")));
            let _ignored = request.respond(response);
            return;
        }
    };
    let length = end.saturating_sub(start).saturating_add(1);
    let status = if range_header.is_some() {
        StatusCode(206)
    } else {
        StatusCode(200)
    };
    let mut headers = vec![
        header("Accept-Ranges", "bytes"),
        header("Content-Type", media_type),
        header("Cache-Control", "private, no-store"),
        header("X-Content-Type-Options", "nosniff"),
        header("Referrer-Policy", "no-referrer"),
    ];
    if status == StatusCode(206) {
        headers.push(header(
            "Content-Range",
            &format!("bytes {start}-{end}/{byte_length}"),
        ));
    }
    if request.method() == &Method::Head {
        let response = Response::new(
            status,
            headers,
            std::io::empty(),
            Some(length as usize),
            None,
        );
        let _ignored = request.respond(response);
        return;
    }
    match source.try_clone().and_then(|mut file| {
        file.seek(SeekFrom::Start(start))?;
        Ok(file)
    }) {
        Ok(file) => {
            let response = Response::new(
                status,
                headers,
                file.take(length),
                Some(length as usize),
                None,
            );
            let _ignored = request.respond(response);
        }
        Err(_error) => {
            let _ignored = request.respond(Response::empty(StatusCode(500)));
        }
    }
}

fn parse_range(value: Option<&str>, byte_length: u64) -> Result<(u64, u64), ()> {
    let Some(value) = value else {
        return Ok((0, byte_length - 1));
    };
    let value = value.strip_prefix("bytes=").ok_or(())?;
    if value.contains(',') {
        return Err(());
    }
    let (start, end) = value.split_once('-').ok_or(())?;
    let start = start.parse::<u64>().map_err(|_| ())?;
    let end = if end.is_empty() {
        byte_length - 1
    } else {
        end.parse::<u64>().map_err(|_| ())?
    };
    if start > end || end >= byte_length {
        return Err(());
    }
    Ok((start, end))
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0_u8, |difference, (left, right)| {
            difference | (left ^ right)
        })
        == 0
}

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).expect("static header")
}
