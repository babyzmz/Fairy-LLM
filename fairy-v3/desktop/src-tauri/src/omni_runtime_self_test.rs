use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::omni_model_manifest::OmniModelManifest;
use crate::process_lifetime::configure_managed_child;

const SELF_TEST_TIMEOUT: Duration = Duration::from_secs(30);
const SELF_TEST_OUTPUT_LIMIT: usize = 64 * 1024;
const POLL_INTERVAL: Duration = Duration::from_millis(20);

#[derive(Clone, Debug)]
pub struct OmniRuntimeSelfTestRequest {
    pub runtime_path: PathBuf,
    pub manifest_path: PathBuf,
    pub model_root: PathBuf,
    pub expected_manifest_digest: String,
    pub expected_model_version: String,
    pub expected_runtime_compatibility: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OmniRuntimeSelfTestReport {
    pub schema_version: u16,
    pub runtime_compatibility: String,
    pub manifest_digest: String,
    pub model_version: String,
    pub predicted_model_peak_bytes: u64,
}

#[derive(Debug, Error)]
pub enum OmniRuntimeSelfTestError {
    #[error("the Omni runtime is missing")]
    RuntimeMissing,
    #[error("the Omni runtime path is unsafe")]
    RuntimeUnsafe,
    #[error("the Omni runtime self-test timed out")]
    Timeout,
    #[error("the Omni runtime self-test crashed")]
    Crash,
    #[error("the Omni runtime self-test output exceeded its limit")]
    OutputTooLarge,
    #[error("the Omni runtime self-test response is invalid")]
    InvalidResponse,
    #[error("the Omni runtime protocol does not match")]
    ProtocolMismatch,
    #[error("the Omni runtime manifest digest does not match")]
    DigestMismatch,
    #[error("the Omni runtime model version does not match")]
    ModelVersionMismatch,
    #[error("the Omni runtime self-test failed to start or communicate: {0}")]
    Io(#[from] std::io::Error),
}

pub trait OmniRuntimeSelfTestRunner: Send + Sync {
    fn run(
        &self,
        request: &OmniRuntimeSelfTestRequest,
    ) -> Result<OmniRuntimeSelfTestReport, OmniRuntimeSelfTestError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ProcessOmniRuntimeSelfTestRunner;

impl OmniRuntimeSelfTestRunner for ProcessOmniRuntimeSelfTestRunner {
    fn run(
        &self,
        request: &OmniRuntimeSelfTestRequest,
    ) -> Result<OmniRuntimeSelfTestReport, OmniRuntimeSelfTestError> {
        validate_runtime_path(&request.runtime_path)?;
        validate_readable_file(&request.manifest_path)?;
        validate_model_root(&request.model_root)?;

        let mut command = Command::new(&request.runtime_path);
        command
            .arg("--self-test")
            .arg("--manifest")
            .arg(&request.manifest_path)
            .arg("--model-root")
            .arg(&request.model_root)
            .current_dir(
                request
                    .runtime_path
                    .parent()
                    .ok_or(OmniRuntimeSelfTestError::RuntimeUnsafe)?,
            )
            .env_clear()
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        configure_managed_child(&mut command);

        let mut child = command.spawn()?;
        let stdout = child
            .stdout
            .take()
            .ok_or(OmniRuntimeSelfTestError::InvalidResponse)?;
        let stderr = child
            .stderr
            .take()
            .ok_or(OmniRuntimeSelfTestError::InvalidResponse)?;
        let stdout_reader = thread::spawn(move || read_bounded(stdout));
        let stderr_reader = thread::spawn(move || read_bounded(stderr));
        let deadline = Instant::now() + SELF_TEST_TIMEOUT;
        let status = loop {
            if let Some(status) = child.try_wait()? {
                break status;
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                join_bounded_reader(stdout_reader)?;
                join_bounded_reader(stderr_reader)?;
                return Err(OmniRuntimeSelfTestError::Timeout);
            }
            thread::sleep(POLL_INTERVAL);
        };
        let stdout = join_bounded_reader(stdout_reader)?;
        let stderr = join_bounded_reader(stderr_reader)?;
        validate_process_result(status, stdout, stderr, request)
    }
}

fn validate_process_result(
    status: ExitStatus,
    stdout: BoundedOutput,
    stderr: BoundedOutput,
    request: &OmniRuntimeSelfTestRequest,
) -> Result<OmniRuntimeSelfTestReport, OmniRuntimeSelfTestError> {
    if stdout.truncated || stderr.truncated {
        return Err(OmniRuntimeSelfTestError::OutputTooLarge);
    }
    if !status.success() {
        return Err(OmniRuntimeSelfTestError::Crash);
    }
    let report: OmniRuntimeSelfTestReport = serde_json::from_slice(&stdout.bytes)
        .map_err(|_| OmniRuntimeSelfTestError::InvalidResponse)?;
    if report.schema_version != 1 || report.predicted_model_peak_bytes == 0 {
        return Err(OmniRuntimeSelfTestError::InvalidResponse);
    }
    if report.runtime_compatibility != request.expected_runtime_compatibility {
        return Err(OmniRuntimeSelfTestError::ProtocolMismatch);
    }
    if report.manifest_digest != request.expected_manifest_digest {
        return Err(OmniRuntimeSelfTestError::DigestMismatch);
    }
    if report.model_version != request.expected_model_version {
        return Err(OmniRuntimeSelfTestError::ModelVersionMismatch);
    }
    Ok(report)
}

fn validate_runtime_path(path: &Path) -> Result<(), OmniRuntimeSelfTestError> {
    if !path.exists() {
        return Err(OmniRuntimeSelfTestError::RuntimeMissing);
    }
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || path.file_name().and_then(|name| name.to_str()) != Some("fairy-omni-runtime.exe")
    {
        return Err(OmniRuntimeSelfTestError::RuntimeUnsafe);
    }
    Ok(())
}

fn validate_readable_file(path: &Path) -> Result<(), OmniRuntimeSelfTestError> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(OmniRuntimeSelfTestError::RuntimeUnsafe);
    }
    Ok(())
}

fn validate_model_root(path: &Path) -> Result<(), OmniRuntimeSelfTestError> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(OmniRuntimeSelfTestError::RuntimeUnsafe);
    }
    Ok(())
}

#[derive(Debug)]
struct BoundedOutput {
    bytes: Vec<u8>,
    truncated: bool,
}

fn read_bounded(mut input: impl Read) -> Result<BoundedOutput, std::io::Error> {
    let mut bytes = Vec::with_capacity(SELF_TEST_OUTPUT_LIMIT.min(4096));
    let mut truncated = false;
    let mut buffer = [0_u8; 4096];
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        let remaining = SELF_TEST_OUTPUT_LIMIT.saturating_sub(bytes.len());
        let copied = remaining.min(read);
        bytes.extend_from_slice(&buffer[..copied]);
        if copied < read {
            truncated = true;
        }
    }
    Ok(BoundedOutput { bytes, truncated })
}

fn join_bounded_reader(
    reader: thread::JoinHandle<Result<BoundedOutput, std::io::Error>>,
) -> Result<BoundedOutput, OmniRuntimeSelfTestError> {
    reader
        .join()
        .map_err(|_| OmniRuntimeSelfTestError::InvalidResponse)?
        .map_err(OmniRuntimeSelfTestError::Io)
}

pub fn runtime_request(
    runtime_path: PathBuf,
    model_root: PathBuf,
    manifest: &OmniModelManifest,
) -> OmniRuntimeSelfTestRequest {
    OmniRuntimeSelfTestRequest {
        manifest_path: model_root.join("manifest.json"),
        runtime_path,
        model_root,
        expected_manifest_digest: manifest.manifest_digest.clone(),
        expected_model_version: manifest.version.clone(),
        expected_runtime_compatibility: manifest.runtime_compatibility.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> OmniRuntimeSelfTestRequest {
        OmniRuntimeSelfTestRequest {
            runtime_path: PathBuf::from("runtime/omni/fairy-omni-runtime.exe"),
            manifest_path: PathBuf::from("models/manifest.json"),
            model_root: PathBuf::from("models"),
            expected_manifest_digest: "a".repeat(64),
            expected_model_version: "4.5".to_owned(),
            expected_runtime_compatibility: "fairy-omni-runtime-v1".to_owned(),
        }
    }

    fn success_status() -> ExitStatus {
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::ExitStatusExt;
            ExitStatus::from_raw(0)
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            ExitStatus::from_raw(0)
        }
    }

    fn failure_status() -> ExitStatus {
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::ExitStatusExt;
            ExitStatus::from_raw(1)
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            ExitStatus::from_raw(1 << 8)
        }
    }

    fn valid_output(request: &OmniRuntimeSelfTestRequest) -> Vec<u8> {
        serde_json::to_vec(&OmniRuntimeSelfTestReport {
            schema_version: 1,
            runtime_compatibility: request.expected_runtime_compatibility.clone(),
            manifest_digest: request.expected_manifest_digest.clone(),
            model_version: request.expected_model_version.clone(),
            predicted_model_peak_bytes: 10,
        })
        .expect("response")
    }

    fn output(bytes: Vec<u8>) -> BoundedOutput {
        BoundedOutput {
            bytes,
            truncated: false,
        }
    }

    #[test]
    fn strict_response_is_accepted_only_when_every_identity_matches() {
        let request = request();
        let report = validate_process_result(
            success_status(),
            output(valid_output(&request)),
            output(Vec::new()),
            &request,
        )
        .expect("valid report");
        assert_eq!(report.predicted_model_peak_bytes, 10);

        let mut unknown = String::from_utf8(valid_output(&request)).expect("json");
        unknown.pop();
        unknown.push_str(",\"unknown\":true}");
        assert!(matches!(
            validate_process_result(
                success_status(),
                output(unknown.into_bytes()),
                output(Vec::new()),
                &request
            ),
            Err(OmniRuntimeSelfTestError::InvalidResponse)
        ));
    }

    #[test]
    fn protocol_digest_and_model_mismatches_are_distinct() {
        let request = request();
        let cases = [
            (
                OmniRuntimeSelfTestReport {
                    schema_version: 1,
                    runtime_compatibility: "wrong".to_owned(),
                    manifest_digest: request.expected_manifest_digest.clone(),
                    model_version: request.expected_model_version.clone(),
                    predicted_model_peak_bytes: 10,
                },
                "protocol",
            ),
            (
                OmniRuntimeSelfTestReport {
                    schema_version: 1,
                    runtime_compatibility: request.expected_runtime_compatibility.clone(),
                    manifest_digest: "b".repeat(64),
                    model_version: request.expected_model_version.clone(),
                    predicted_model_peak_bytes: 10,
                },
                "digest",
            ),
            (
                OmniRuntimeSelfTestReport {
                    schema_version: 1,
                    runtime_compatibility: request.expected_runtime_compatibility.clone(),
                    manifest_digest: request.expected_manifest_digest.clone(),
                    model_version: "wrong".to_owned(),
                    predicted_model_peak_bytes: 10,
                },
                "model",
            ),
        ];
        for (report, expected) in cases {
            let error = validate_process_result(
                success_status(),
                output(serde_json::to_vec(&report).expect("json")),
                output(Vec::new()),
                &request,
            )
            .expect_err("mismatch");
            assert_eq!(
                match error {
                    OmniRuntimeSelfTestError::ProtocolMismatch => "protocol",
                    OmniRuntimeSelfTestError::DigestMismatch => "digest",
                    OmniRuntimeSelfTestError::ModelVersionMismatch => "model",
                    _ => "other",
                },
                expected
            );
        }
    }

    #[test]
    fn crash_and_bounded_output_fail_closed() {
        let request = request();
        assert!(matches!(
            validate_process_result(
                failure_status(),
                output(Vec::new()),
                output(Vec::new()),
                &request
            ),
            Err(OmniRuntimeSelfTestError::Crash)
        ));
        assert!(matches!(
            validate_process_result(
                success_status(),
                BoundedOutput {
                    bytes: valid_output(&request),
                    truncated: true
                },
                output(Vec::new()),
                &request
            ),
            Err(OmniRuntimeSelfTestError::OutputTooLarge)
        ));
    }

    #[test]
    fn reader_drains_but_retains_only_the_bounded_prefix() {
        let bytes = vec![b'x'; SELF_TEST_OUTPUT_LIMIT + 17];
        let bounded = read_bounded(bytes.as_slice()).expect("bounded");
        assert_eq!(bounded.bytes.len(), SELF_TEST_OUTPUT_LIMIT);
        assert!(bounded.truncated);
    }

    #[test]
    fn timeout_and_failed_self_test_are_typed_failures() {
        let timeout = OmniRuntimeSelfTestError::Timeout;
        assert_eq!(timeout.to_string(), "the Omni runtime self-test timed out");
        let failure = validate_process_result(
            failure_status(),
            output(Vec::new()),
            output(b"self-test failed".to_vec()),
            &request(),
        )
        .expect_err("failed self-test");
        assert!(matches!(failure, OmniRuntimeSelfTestError::Crash));
    }
}
