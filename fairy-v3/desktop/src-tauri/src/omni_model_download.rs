use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::Path;
use std::time::Duration;

use reqwest::blocking::{Client, Response};
use reqwest::header::{ACCEPT_ENCODING, CONTENT_RANGE, RANGE};
use reqwest::redirect::Policy;
use reqwest::{StatusCode, Url};
use thiserror::Error;

use crate::omni_model_manager::OmniCancellationToken;
use crate::omni_model_manifest::OmniModelFile;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(4 * 60 * 60);
const MAX_REDIRECTS: usize = 5;
const COPY_BUFFER_SIZE: usize = 1024 * 1024;
const PROGRESS_GRANULARITY: u64 = 8 * 1024 * 1024;

#[derive(Debug, Error)]
pub enum OmniModelDownloadError {
    #[error("the Omni model download URL is not allowed")]
    UrlNotAllowed,
    #[error("the Omni model download client failed: {0}")]
    Client(#[source] reqwest::Error),
    #[error("the Omni model server returned an invalid response")]
    InvalidResponse,
    #[error("the Omni model server returned an invalid range")]
    InvalidRange,
    #[error("the Omni model response exceeds the manifest size")]
    SizeExceeded,
    #[error("the Omni model download was cancelled")]
    Cancelled,
    #[error("the Omni model progress state could not be persisted")]
    ProgressState,
    #[error("the Omni model download filesystem failed: {0}")]
    Io(#[from] std::io::Error),
}

pub trait OmniArtifactTransfer {
    fn download(
        &self,
        file: &OmniModelFile,
        partial_path: &Path,
        cancellation: &OmniCancellationToken,
        progress: &mut dyn FnMut(u64) -> Result<(), OmniModelDownloadError>,
    ) -> Result<(), OmniModelDownloadError>;
}

pub struct OmniModelDownloader {
    client: Client,
    allow_http_loopback: bool,
}

impl OmniModelDownloader {
    pub fn new() -> Result<Self, OmniModelDownloadError> {
        Self::build(false)
    }

    fn build(allow_http_loopback: bool) -> Result<Self, OmniModelDownloadError> {
        let redirect_allow_http = allow_http_loopback;
        let redirect = Policy::custom(move |attempt| {
            if attempt.previous().len() > MAX_REDIRECTS {
                attempt.error("too many redirects")
            } else if allowed_url(attempt.url(), redirect_allow_http) {
                attempt.follow()
            } else {
                attempt.error("redirect URL is not allowed")
            }
        });
        let mut builder = Client::builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .timeout(REQUEST_TIMEOUT)
            .redirect(redirect);
        if allow_http_loopback {
            builder = builder.no_proxy();
        }
        let client = builder.build().map_err(OmniModelDownloadError::Client)?;
        Ok(Self {
            client,
            allow_http_loopback,
        })
    }

    #[cfg(test)]
    fn for_loopback_tests() -> Result<Self, OmniModelDownloadError> {
        Self::build(true)
    }
}

impl OmniArtifactTransfer for OmniModelDownloader {
    fn download(
        &self,
        file: &OmniModelFile,
        partial_path: &Path,
        cancellation: &OmniCancellationToken,
        progress: &mut dyn FnMut(u64) -> Result<(), OmniModelDownloadError>,
    ) -> Result<(), OmniModelDownloadError> {
        if cancellation.is_cancelled() {
            return Err(OmniModelDownloadError::Cancelled);
        }
        let url = file
            .urls
            .first()
            .ok_or(OmniModelDownloadError::UrlNotAllowed)
            .and_then(|value| {
                Url::parse(value).map_err(|_| OmniModelDownloadError::UrlNotAllowed)
            })?;
        if !allowed_url(&url, self.allow_http_loopback) {
            return Err(OmniModelDownloadError::UrlNotAllowed);
        }

        if let Some(parent) = partial_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut existing = partial_length(partial_path)?;
        if existing > file.size {
            OpenOptions::new()
                .write(true)
                .truncate(true)
                .open(partial_path)?;
            existing = 0;
        }
        if existing == file.size {
            progress(existing)?;
            return Ok(());
        }

        let mut request = self.client.get(url).header(ACCEPT_ENCODING, "identity");
        if existing > 0 {
            request = request.header(RANGE, format!("bytes={existing}-"));
        }
        let response = request.send().map_err(OmniModelDownloadError::Client)?;
        let (mut response, append) = validate_response(response, existing, file.size)?;
        if !append {
            existing = 0;
        }
        let mut output = OpenOptions::new()
            .create(true)
            .write(true)
            .append(append)
            .truncate(!append)
            .open(partial_path)?;
        let mut received = existing;
        let mut last_reported = existing;
        let mut buffer = vec![0_u8; COPY_BUFFER_SIZE];
        loop {
            if cancellation.is_cancelled() {
                output.sync_all()?;
                return Err(OmniModelDownloadError::Cancelled);
            }
            let read = response
                .read(&mut buffer)
                .map_err(OmniModelDownloadError::Io)?;
            if read == 0 {
                break;
            }
            received = received
                .checked_add(read as u64)
                .ok_or(OmniModelDownloadError::SizeExceeded)?;
            if received > file.size {
                output.sync_all()?;
                return Err(OmniModelDownloadError::SizeExceeded);
            }
            output.write_all(&buffer[..read])?;
            if received.saturating_sub(last_reported) >= PROGRESS_GRANULARITY {
                progress(received)?;
                last_reported = received;
            }
        }
        output.sync_all()?;
        if received != file.size {
            return Err(OmniModelDownloadError::InvalidResponse);
        }
        progress(received)?;
        Ok(())
    }
}

fn validate_response(
    response: Response,
    existing: u64,
    expected_size: u64,
) -> Result<(Response, bool), OmniModelDownloadError> {
    if !response.status().is_success() {
        return Err(OmniModelDownloadError::InvalidResponse);
    }
    if response.status() == StatusCode::PARTIAL_CONTENT {
        let range = response
            .headers()
            .get(CONTENT_RANGE)
            .and_then(|value| value.to_str().ok())
            .and_then(parse_content_range)
            .ok_or(OmniModelDownloadError::InvalidRange)?;
        if range.start != existing
            || range.total != expected_size
            || range.end < range.start
            || range.end >= range.total
        {
            return Err(OmniModelDownloadError::InvalidRange);
        }
        if let Some(length) = response.content_length() {
            let range_length = range
                .end
                .checked_sub(range.start)
                .and_then(|value| value.checked_add(1))
                .ok_or(OmniModelDownloadError::InvalidRange)?;
            if length != range_length {
                return Err(OmniModelDownloadError::InvalidRange);
            }
            if length > expected_size.saturating_sub(existing) {
                return Err(OmniModelDownloadError::SizeExceeded);
            }
        }
        return Ok((response, existing > 0));
    }
    if response.status() != StatusCode::OK {
        return Err(OmniModelDownloadError::InvalidResponse);
    }
    if response
        .content_length()
        .is_some_and(|length| length > expected_size)
    {
        return Err(OmniModelDownloadError::SizeExceeded);
    }
    Ok((response, false))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ContentRange {
    start: u64,
    end: u64,
    total: u64,
}

fn parse_content_range(value: &str) -> Option<ContentRange> {
    let value = value.strip_prefix("bytes ")?;
    let (range, total) = value.split_once('/')?;
    let (start, end) = range.split_once('-')?;
    Some(ContentRange {
        start: start.parse().ok()?,
        end: end.parse().ok()?,
        total: total.parse().ok()?,
    })
}

fn partial_length(path: &Path) -> Result<u64, OmniModelDownloadError> {
    if !path.exists() {
        return Ok(0);
    }
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(OmniModelDownloadError::InvalidResponse);
    }
    Ok(metadata.len())
}

fn allowed_url(url: &Url, allow_http_loopback: bool) -> bool {
    let no_credentials = url.username().is_empty() && url.password().is_none();
    if !no_credentials || url.host_str().is_none() {
        return false;
    }
    if url.scheme() == "https" {
        return true;
    }
    allow_http_loopback
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
}

#[cfg(test)]
mod tests {
    use std::sync::mpsc;
    use std::thread;

    use tiny_http::{Header, Response as TinyResponse, Server, StatusCode as TinyStatus};

    use super::*;

    fn file(url: String, size: u64) -> OmniModelFile {
        OmniModelFile {
            path: "MiniCPM-o-4_5-Q4_K_M.gguf".to_owned(),
            size,
            sha256: "a".repeat(64),
            urls: vec![url],
        }
    }

    fn server_url(server: &Server) -> String {
        format!("http://{}/model.gguf", server.server_addr())
    }

    fn header(name: &[u8], value: &[u8]) -> Header {
        Header::from_bytes(name, value).expect("header")
    }

    #[test]
    fn fresh_download_streams_to_a_partial_file() {
        let server = Server::http("127.0.0.1:0").expect("server");
        let url = server_url(&server);
        let worker = thread::spawn(move || {
            let request = server.recv().expect("request");
            request
                .respond(TinyResponse::from_data(b"model".to_vec()))
                .expect("respond");
        });
        let directory = tempfile::tempdir().expect("directory");
        let partial = directory.path().join("model.partial");
        let downloader = OmniModelDownloader::for_loopback_tests().expect("downloader");
        let cancellation = OmniCancellationToken::new();
        let mut progress = Vec::new();
        downloader
            .download(&file(url, 5), &partial, &cancellation, &mut |value| {
                progress.push(value);
                Ok(())
            })
            .expect("download");
        worker.join().expect("server thread");

        assert_eq!(fs::read(partial).expect("partial"), b"model");
        assert_eq!(progress, vec![5]);
    }

    #[test]
    fn honored_range_resumes_without_rewriting_the_prefix() {
        let server = Server::http("127.0.0.1:0").expect("server");
        let url = server_url(&server);
        let (header_tx, header_rx) = mpsc::channel();
        let worker = thread::spawn(move || {
            let request = server.recv().expect("request");
            let range = request
                .headers()
                .iter()
                .find(|header| header.field.equiv("Range"))
                .map(|header| header.value.as_str().to_owned());
            header_tx.send(range).expect("range");
            let response = TinyResponse::from_data(b"del".to_vec())
                .with_status_code(TinyStatus(206))
                .with_header(header(b"Content-Range", b"bytes 2-4/5"));
            request.respond(response).expect("respond");
        });
        let directory = tempfile::tempdir().expect("directory");
        let partial = directory.path().join("model.partial");
        fs::write(&partial, b"mo").expect("prefix");
        let downloader = OmniModelDownloader::for_loopback_tests().expect("downloader");
        downloader
            .download(
                &file(url, 5),
                &partial,
                &OmniCancellationToken::new(),
                &mut |_| Ok(()),
            )
            .expect("download");
        worker.join().expect("server thread");

        assert_eq!(
            header_rx.recv().expect("range"),
            Some("bytes=2-".to_owned())
        );
        assert_eq!(fs::read(partial).expect("partial"), b"model");
    }

    #[test]
    fn ignored_range_restarts_the_partial_safely() {
        let server = Server::http("127.0.0.1:0").expect("server");
        let url = server_url(&server);
        let worker = thread::spawn(move || {
            let request = server.recv().expect("request");
            request
                .respond(TinyResponse::from_data(b"model".to_vec()))
                .expect("respond");
        });
        let directory = tempfile::tempdir().expect("directory");
        let partial = directory.path().join("model.partial");
        fs::write(&partial, b"mo").expect("prefix");
        OmniModelDownloader::for_loopback_tests()
            .expect("downloader")
            .download(
                &file(url, 5),
                &partial,
                &OmniCancellationToken::new(),
                &mut |_| Ok(()),
            )
            .expect("download");
        worker.join().expect("server thread");
        assert_eq!(fs::read(partial).expect("partial"), b"model");
    }

    #[test]
    fn malformed_partial_content_is_rejected_before_writing() {
        let server = Server::http("127.0.0.1:0").expect("server");
        let url = server_url(&server);
        let worker = thread::spawn(move || {
            let request = server.recv().expect("request");
            let response = TinyResponse::from_data(b"de".to_vec())
                .with_status_code(TinyStatus(206))
                .with_header(header(b"Content-Range", b"bytes 2-4/5"));
            request.respond(response).expect("respond");
        });
        let directory = tempfile::tempdir().expect("directory");
        let partial = directory.path().join("model.partial");
        fs::write(&partial, b"mo").expect("prefix");
        assert!(matches!(
            OmniModelDownloader::for_loopback_tests()
                .expect("downloader")
                .download(
                    &file(url, 5),
                    &partial,
                    &OmniCancellationToken::new(),
                    &mut |_| Ok(())
                ),
            Err(OmniModelDownloadError::InvalidRange)
        ));
        worker.join().expect("server thread");
        assert_eq!(fs::read(partial).expect("partial"), b"mo");
    }

    #[test]
    fn oversized_partial_is_discarded_before_a_fresh_download() {
        let server = Server::http("127.0.0.1:0").expect("server");
        let url = server_url(&server);
        let (header_tx, header_rx) = mpsc::channel();
        let worker = thread::spawn(move || {
            let request = server.recv().expect("request");
            let range = request
                .headers()
                .iter()
                .find(|header| header.field.equiv("Range"))
                .map(|header| header.value.as_str().to_owned());
            header_tx.send(range).expect("range");
            request
                .respond(TinyResponse::from_data(b"model".to_vec()))
                .expect("respond");
        });
        let directory = tempfile::tempdir().expect("directory");
        let partial = directory.path().join("model.partial");
        fs::write(&partial, b"oversized").expect("oversized partial");
        OmniModelDownloader::for_loopback_tests()
            .expect("downloader")
            .download(
                &file(url, 5),
                &partial,
                &OmniCancellationToken::new(),
                &mut |_| Ok(()),
            )
            .expect("download");
        worker.join().expect("server thread");
        assert_eq!(header_rx.recv().expect("range"), None);
        assert_eq!(fs::read(partial).expect("partial"), b"model");
    }

    #[test]
    fn production_downloader_rejects_plain_http_and_credentials() {
        let directory = tempfile::tempdir().expect("directory");
        let downloader = OmniModelDownloader::new().expect("downloader");
        for url in [
            "http://127.0.0.1/model.gguf",
            "https://user@example.com/model.gguf",
        ] {
            assert!(matches!(
                downloader.download(
                    &file(url.to_owned(), 1),
                    &directory.path().join("model.partial"),
                    &OmniCancellationToken::new(),
                    &mut |_| Ok(())
                ),
                Err(OmniModelDownloadError::UrlNotAllowed)
            ));
        }
    }

    #[test]
    fn cancellation_and_size_ceiling_keep_the_transfer_bounded() {
        let directory = tempfile::tempdir().expect("directory");
        let cancellation = OmniCancellationToken::new();
        cancellation.cancel();
        assert!(matches!(
            OmniModelDownloader::for_loopback_tests()
                .expect("downloader")
                .download(
                    &file("http://127.0.0.1/model.gguf".to_owned(), 1),
                    &directory.path().join("model.partial"),
                    &cancellation,
                    &mut |_| Ok(())
                ),
            Err(OmniModelDownloadError::Cancelled)
        ));

        let server = Server::http("127.0.0.1:0").expect("server");
        let url = server_url(&server);
        let worker = thread::spawn(move || {
            let request = server.recv().expect("request");
            request
                .respond(TinyResponse::from_data(b"too large".to_vec()))
                .expect("respond");
        });
        assert!(matches!(
            OmniModelDownloader::for_loopback_tests()
                .expect("downloader")
                .download(
                    &file(url, 2),
                    &directory.path().join("oversized.partial"),
                    &OmniCancellationToken::new(),
                    &mut |_| Ok(())
                ),
            Err(OmniModelDownloadError::SizeExceeded)
        ));
        worker.join().expect("server thread");
    }

    #[test]
    fn content_range_parser_rejects_ambiguous_values() {
        assert_eq!(
            parse_content_range("bytes 2-4/5"),
            Some(ContentRange {
                start: 2,
                end: 4,
                total: 5
            })
        );
        assert_eq!(parse_content_range("bytes */5"), None);
        assert_eq!(parse_content_range("items 2-4/5"), None);
    }
}
