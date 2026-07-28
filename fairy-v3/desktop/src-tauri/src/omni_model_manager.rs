use std::collections::HashSet;
use std::fs;
use std::io::{BufReader, Read};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::omni_model_download::{OmniArtifactTransfer, OmniModelDownloadError};
use crate::omni_model_manifest::{ManifestError, OmniModelFile, OmniModelManifest};
use crate::omni_model_store::{
    OmniModelInstallPhase, OmniModelInstallState, OmniModelStore, OmniModelStoreError,
};

const GIB: u64 = 1024 * 1024 * 1024;
const TEMPORARY_ALLOWANCE: u64 = 2 * GIB;
const POST_INSTALL_FREE_SPACE: u64 = 5 * GIB;
const HASH_BUFFER_SIZE: usize = 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ActiveOperation {
    Install,
    Verify,
    RuntimeSelfTest,
}

#[derive(Clone, Debug)]
pub struct OmniCancellationToken(Arc<AtomicBool>);

impl OmniCancellationToken {
    pub(crate) fn new() -> Self {
        Self(Arc::new(AtomicBool::new(false)))
    }

    pub fn cancel(&self) {
        self.0.store(true, Ordering::Release);
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }
}

#[derive(Debug, Error)]
pub enum OmniModelManagerError {
    #[error("an Omni model operation is already active")]
    Busy,
    #[error("the Omni model operation is not active")]
    NotActive,
    #[error("the Omni model operation was cancelled")]
    Cancelled,
    #[error(
        "the Omni model requires {required_bytes} bytes but only {available_bytes} are available"
    )]
    InsufficientDisk {
        required_bytes: u64,
        available_bytes: u64,
    },
    #[error("the Omni model artifact failed verification: {0}")]
    Verification(&'static str),
    #[error(transparent)]
    Manifest(#[from] ManifestError),
    #[error(transparent)]
    Store(#[from] OmniModelStoreError),
    #[error("the Omni model filesystem failed: {0}")]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Download(#[from] OmniModelDownloadError),
}

pub struct OmniModelManager {
    store: OmniModelStore,
    manifest: OmniModelManifest,
    state: OmniModelInstallState,
    active: Option<ActiveOperation>,
    cancellation: OmniCancellationToken,
}

impl OmniModelManager {
    pub fn new(
        models_root: &Path,
        manifest: OmniModelManifest,
    ) -> Result<Self, OmniModelManagerError> {
        manifest.validate()?;
        let store = OmniModelStore::new(models_root)?;
        let mut state = store.load_state(&manifest)?;
        if state.phase.is_active() {
            state.sequence = state
                .sequence
                .checked_add(1)
                .ok_or(OmniModelStoreError::InvalidState)?;
            state.phase = OmniModelInstallPhase::Partial;
            state.error_code = Some("MODEL_OPERATION_INTERRUPTED".to_owned());
            state.current_file = None;
            store.write_state(&state)?;
        }
        Ok(Self {
            store,
            manifest,
            state,
            active: None,
            cancellation: OmniCancellationToken::new(),
        })
    }

    pub fn status(&self) -> &OmniModelInstallState {
        &self.state
    }

    pub fn manifest(&self) -> &OmniModelManifest {
        &self.manifest
    }

    pub fn required_disk_bytes(&self) -> Result<u64, OmniModelManagerError> {
        let partial = self.store.partial_bytes(&self.manifest)?;
        self.manifest
            .total_size()
            .saturating_sub(partial)
            .checked_add(TEMPORARY_ALLOWANCE)
            .and_then(|required| required.checked_add(POST_INSTALL_FREE_SPACE))
            .ok_or(OmniModelManagerError::Verification("MODEL_SIZE_OVERFLOW"))
    }

    pub fn begin_install(
        &mut self,
        disk_available_bytes: u64,
    ) -> Result<OmniCancellationToken, OmniModelManagerError> {
        self.require_idle()?;
        self.transition(
            OmniModelInstallPhase::CheckingSpace,
            None,
            None,
            self.store.partial_bytes(&self.manifest)?,
        )?;
        let required = self.required_disk_bytes()?;
        if disk_available_bytes < required {
            self.transition(
                OmniModelInstallPhase::Partial,
                None,
                Some("MODEL_DISK_INSUFFICIENT"),
                self.state.received_bytes,
            )?;
            return Err(OmniModelManagerError::InsufficientDisk {
                required_bytes: required,
                available_bytes: disk_available_bytes,
            });
        }
        self.store.prepare_staging(&self.manifest)?;
        self.cancellation = OmniCancellationToken::new();
        self.active = Some(ActiveOperation::Install);
        self.transition(
            OmniModelInstallPhase::Downloading,
            None,
            None,
            self.store.partial_bytes(&self.manifest)?,
        )?;
        Ok(self.cancellation.clone())
    }

    pub fn begin_verify(&mut self) -> Result<OmniCancellationToken, OmniModelManagerError> {
        self.require_idle()?;
        self.cancellation = OmniCancellationToken::new();
        self.active = Some(ActiveOperation::Verify);
        self.transition(
            OmniModelInstallPhase::Verifying,
            None,
            None,
            self.state.received_bytes,
        )?;
        Ok(self.cancellation.clone())
    }

    pub fn update_download_progress(
        &mut self,
        file: &OmniModelFile,
        received_bytes: u64,
    ) -> Result<(), OmniModelManagerError> {
        if self.active != Some(ActiveOperation::Install) {
            return Err(OmniModelManagerError::NotActive);
        }
        if received_bytes > self.manifest.total_size() {
            return Err(OmniModelManagerError::Verification(
                "MODEL_PROGRESS_OVERFLOW",
            ));
        }
        self.transition(
            OmniModelInstallPhase::Downloading,
            Some(&file.path),
            None,
            received_bytes,
        )
    }

    pub fn download_and_install<T: OmniArtifactTransfer>(
        &mut self,
        transfer: &T,
    ) -> Result<(), OmniModelManagerError> {
        if self.active != Some(ActiveOperation::Install) {
            return Err(OmniModelManagerError::NotActive);
        }
        let result = self.download_and_install_active(transfer);
        if let Err(error) = &result {
            self.settle_operation_error(error)?;
        }
        result
    }

    fn download_and_install_active<T: OmniArtifactTransfer>(
        &mut self,
        transfer: &T,
    ) -> Result<(), OmniModelManagerError> {
        let mut completed = 0_u64;
        for file in self.manifest.files.clone() {
            if self.cancellation.is_cancelled() {
                self.finish_cancel()?;
                return Err(OmniModelManagerError::Cancelled);
            }
            let artifact = self.store.staging_artifact_path(&self.manifest, &file)?;
            if artifact.exists() {
                match verify_file(&artifact, &file, &self.cancellation) {
                    Ok(()) => {
                        completed = completed.saturating_add(file.size);
                        self.update_download_progress(&file, completed)?;
                        continue;
                    }
                    Err(OmniModelManagerError::Verification(_)) => {
                        let metadata = fs::symlink_metadata(&artifact)?;
                        if metadata.file_type().is_symlink() || !metadata.is_file() {
                            return self.fail_download(
                                OmniModelManagerError::Verification("MODEL_ARTIFACT_UNSAFE"),
                                OmniModelInstallPhase::Corrupt,
                            );
                        }
                        fs::remove_file(&artifact)?;
                    }
                    Err(error) => return Err(error),
                }
            }

            let partial = self.store.staging_partial_path(&self.manifest, &file)?;
            let cancellation = self.cancellation.clone();
            let download =
                transfer.download(&file, &partial, &cancellation, &mut |file_received| {
                    self.update_download_progress(&file, completed.saturating_add(file_received))
                        .map_err(|_| OmniModelDownloadError::ProgressState)
                });
            if let Err(error) = download {
                let phase = if matches!(error, OmniModelDownloadError::Cancelled) {
                    OmniModelInstallPhase::Partial
                } else if download_error_is_corrupt(&error) {
                    OmniModelInstallPhase::Corrupt
                } else {
                    OmniModelInstallPhase::Partial
                };
                return self.fail_download(OmniModelManagerError::Download(error), phase);
            }
            if let Err(error) = verify_file(&partial, &file, &cancellation) {
                return self.fail_download(error, OmniModelInstallPhase::Corrupt);
            }
            self.store.finalize_partial(&self.manifest, &file)?;
            completed = completed
                .checked_add(file.size)
                .ok_or(OmniModelManagerError::Verification("MODEL_SIZE_OVERFLOW"))?;
            self.update_download_progress(&file, completed)?;
        }
        self.verify_staging_and_promote()
    }

    pub(crate) fn settle_operation_error(
        &mut self,
        error: &OmniModelManagerError,
    ) -> Result<(), OmniModelManagerError> {
        if self.active.is_none() {
            return Ok(());
        }
        self.active = None;
        let phase = match error {
            OmniModelManagerError::Cancelled
            | OmniModelManagerError::InsufficientDisk { .. }
            | OmniModelManagerError::Io(_)
            | OmniModelManagerError::Download(
                OmniModelDownloadError::Client(_)
                | OmniModelDownloadError::Cancelled
                | OmniModelDownloadError::ProgressState
                | OmniModelDownloadError::Io(_),
            ) => OmniModelInstallPhase::Partial,
            _ => OmniModelInstallPhase::Corrupt,
        };
        let partial = self.store.partial_bytes(&self.manifest)?;
        self.transition(phase, None, Some(verification_error_code(error)), partial)
    }

    pub(crate) fn settle_operation_panic(&mut self) -> Result<(), OmniModelManagerError> {
        if self.active.is_none() {
            return Ok(());
        }
        self.active = None;
        let partial = self.store.partial_bytes(&self.manifest)?;
        self.transition(
            OmniModelInstallPhase::Corrupt,
            None,
            Some("OMNI_MODEL_OPERATION_PANICKED"),
            partial,
        )
    }

    pub fn request_cancel(&mut self) -> Result<(), OmniModelManagerError> {
        if self.active.is_none() {
            return Err(OmniModelManagerError::NotActive);
        }
        self.cancellation.cancel();
        self.transition(
            OmniModelInstallPhase::Cancelling,
            self.state.current_file.clone().as_deref(),
            None,
            self.state.received_bytes,
        )
    }

    pub fn finish_cancel(&mut self) -> Result<(), OmniModelManagerError> {
        if self.active.is_none() {
            return Err(OmniModelManagerError::NotActive);
        }
        self.active = None;
        self.transition(
            OmniModelInstallPhase::Partial,
            None,
            Some("MODEL_DOWNLOAD_CANCELLED"),
            self.store.partial_bytes(&self.manifest)?,
        )
    }

    pub fn verify_staging_and_promote(&mut self) -> Result<(), OmniModelManagerError> {
        if self.active != Some(ActiveOperation::Install) {
            return Err(OmniModelManagerError::NotActive);
        }
        let staging = self.store.staging_dir(&self.manifest)?;
        let result = (|| {
            self.store.validate_managed_tree(&staging)?;
            let staged_manifest = staging.join("manifest.json");
            if staged_manifest.exists() {
                let metadata = fs::symlink_metadata(&staged_manifest)?;
                if metadata.file_type().is_symlink() || !metadata.is_file() {
                    return Err(OmniModelManagerError::Verification("MODEL_MANIFEST_UNSAFE"));
                }
                fs::remove_file(staged_manifest)?;
            }
            self.verify_directory(&staging, false)?;
            self.transition(
                OmniModelInstallPhase::LayoutCheck,
                None,
                None,
                self.manifest.total_size(),
            )?;
            self.store.write_staged_manifest(&self.manifest)?;
            self.verify_layout(&staging, true)?;
            self.store.promote(&self.manifest)?;
            Ok(())
        })();
        self.active = None;
        match result {
            Ok(()) => self.transition(
                OmniModelInstallPhase::RuntimeMissing,
                None,
                Some("OMNI_RUNTIME_MISSING"),
                self.manifest.total_size(),
            ),
            Err(OmniModelManagerError::Cancelled) => {
                let partial = self.store.partial_bytes(&self.manifest)?;
                self.transition(
                    OmniModelInstallPhase::Partial,
                    None,
                    Some("MODEL_VERIFICATION_CANCELLED"),
                    partial,
                )?;
                Err(OmniModelManagerError::Cancelled)
            }
            Err(error) => {
                self.transition(
                    OmniModelInstallPhase::Corrupt,
                    None,
                    Some(verification_error_code(&error)),
                    0,
                )?;
                Err(error)
            }
        }
    }

    pub fn verify_installed(&mut self) -> Result<(), OmniModelManagerError> {
        if self.active != Some(ActiveOperation::Verify) {
            return Err(OmniModelManagerError::NotActive);
        }
        let version = self.store.version_dir(&self.manifest)?;
        let result = self
            .verify_directory(&version, true)
            .and_then(|()| self.verify_stored_manifest(&version));
        self.active = None;
        match result {
            Ok(()) => self.transition(
                OmniModelInstallPhase::RuntimeMissing,
                None,
                Some("OMNI_RUNTIME_MISSING"),
                self.manifest.total_size(),
            ),
            Err(OmniModelManagerError::Cancelled) => {
                self.transition(
                    OmniModelInstallPhase::Partial,
                    None,
                    Some("MODEL_VERIFICATION_CANCELLED"),
                    self.state.received_bytes,
                )?;
                Err(OmniModelManagerError::Cancelled)
            }
            Err(error) => {
                self.transition(
                    OmniModelInstallPhase::Corrupt,
                    None,
                    Some(verification_error_code(&error)),
                    0,
                )?;
                Err(error)
            }
        }
    }

    pub fn begin_runtime_self_test(&mut self) -> Result<(), OmniModelManagerError> {
        self.require_idle()?;
        if !matches!(
            self.state.phase,
            OmniModelInstallPhase::RuntimeMissing | OmniModelInstallPhase::SelfTestFailed
        ) {
            return Err(OmniModelManagerError::Verification(
                "OMNI_MODEL_NOT_VERIFIED",
            ));
        }
        self.cancellation = OmniCancellationToken::new();
        self.active = Some(ActiveOperation::RuntimeSelfTest);
        self.transition(
            OmniModelInstallPhase::RuntimeSelfTest,
            None,
            None,
            self.manifest.total_size(),
        )
    }

    pub fn finish_runtime_self_test(
        &mut self,
        error_code: Option<&str>,
    ) -> Result<(), OmniModelManagerError> {
        if self.active != Some(ActiveOperation::RuntimeSelfTest) {
            return Err(OmniModelManagerError::NotActive);
        }
        self.active = None;
        match error_code {
            None => self.transition(
                OmniModelInstallPhase::Ready,
                None,
                None,
                self.manifest.total_size(),
            ),
            Some(code) => self.transition(
                OmniModelInstallPhase::SelfTestFailed,
                None,
                Some(code),
                self.manifest.total_size(),
            ),
        }
    }

    pub fn remove(&mut self) -> Result<(), OmniModelManagerError> {
        self.require_idle()?;
        self.store.remove_version(&self.manifest)?;
        self.store.remove_staging(&self.manifest)?;
        self.transition(OmniModelInstallPhase::NotInstalled, None, None, 0)
    }

    fn verify_directory(
        &mut self,
        root: &Path,
        allow_manifest: bool,
    ) -> Result<(), OmniModelManagerError> {
        self.store.validate_managed_tree(root)?;
        if !root.is_dir() {
            return Err(OmniModelManagerError::Verification("MODEL_LAYOUT_MISSING"));
        }
        self.transition(
            OmniModelInstallPhase::Verifying,
            None,
            None,
            self.state.received_bytes,
        )?;
        let mut verified = 0_u64;
        for file in self.manifest.files.clone() {
            self.check_cancelled()?;
            let path = root.join(file.path.replace('/', std::path::MAIN_SEPARATOR_STR));
            verify_file(&path, &file, &self.cancellation)?;
            verified = verified
                .checked_add(file.size)
                .ok_or(OmniModelManagerError::Verification("MODEL_SIZE_OVERFLOW"))?;
            self.transition(
                OmniModelInstallPhase::Verifying,
                Some(&file.path),
                None,
                verified,
            )?;
        }
        self.verify_layout(root, allow_manifest)
    }

    fn verify_layout(
        &self,
        root: &Path,
        allow_manifest: bool,
    ) -> Result<(), OmniModelManagerError> {
        let mut expected: HashSet<String> = self
            .manifest
            .files
            .iter()
            .map(|file| file.path.to_ascii_lowercase())
            .collect();
        if allow_manifest {
            expected.insert("manifest.json".to_owned());
        }
        let actual = collect_relative_files(root)?;
        if actual == expected {
            Ok(())
        } else {
            Err(OmniModelManagerError::Verification(
                "MODEL_LAYOUT_UNEXPECTED",
            ))
        }
    }

    fn verify_stored_manifest(&self, version: &Path) -> Result<(), OmniModelManagerError> {
        let bytes = fs::read(version.join("manifest.json"))?;
        let stored = OmniModelManifest::parse_and_validate(&bytes)?;
        if stored == self.manifest {
            Ok(())
        } else {
            Err(OmniModelManagerError::Verification(
                "MODEL_MANIFEST_MISMATCH",
            ))
        }
    }

    fn check_cancelled(&self) -> Result<(), OmniModelManagerError> {
        if self.cancellation.is_cancelled() {
            Err(OmniModelManagerError::Cancelled)
        } else {
            Ok(())
        }
    }

    fn require_idle(&self) -> Result<(), OmniModelManagerError> {
        if self.active.is_some() || self.state.phase.is_active() {
            Err(OmniModelManagerError::Busy)
        } else {
            Ok(())
        }
    }

    fn fail_download<T>(
        &mut self,
        error: OmniModelManagerError,
        phase: OmniModelInstallPhase,
    ) -> Result<T, OmniModelManagerError> {
        self.active = None;
        let code = verification_error_code(&error);
        self.transition(
            phase,
            None,
            Some(code),
            self.store.partial_bytes(&self.manifest)?,
        )?;
        Err(error)
    }

    fn transition(
        &mut self,
        phase: OmniModelInstallPhase,
        current_file: Option<&str>,
        error_code: Option<&str>,
        received_bytes: u64,
    ) -> Result<(), OmniModelManagerError> {
        self.state.sequence =
            self.state
                .sequence
                .checked_add(1)
                .ok_or(OmniModelManagerError::Verification(
                    "MODEL_SEQUENCE_OVERFLOW",
                ))?;
        self.state.phase = phase;
        self.state.current_file = current_file.map(str::to_owned);
        self.state.error_code = error_code.map(str::to_owned);
        self.state.received_bytes = received_bytes.min(self.state.total_bytes);
        self.store.write_state(&self.state)?;
        Ok(())
    }
}

fn verification_error_code(error: &OmniModelManagerError) -> &'static str {
    match error {
        OmniModelManagerError::Verification(code) => code,
        OmniModelManagerError::Manifest(_) => "MODEL_MANIFEST_INVALID",
        OmniModelManagerError::Store(_) => "MODEL_STORE_FAILED",
        OmniModelManagerError::Io(_) => "MODEL_IO_FAILED",
        OmniModelManagerError::Download(download) => match download {
            OmniModelDownloadError::UrlNotAllowed => "MODEL_DOWNLOAD_URL_DENIED",
            OmniModelDownloadError::Client(_) => "MODEL_DOWNLOAD_NETWORK_FAILED",
            OmniModelDownloadError::InvalidResponse => "MODEL_DOWNLOAD_INVALID_RESPONSE",
            OmniModelDownloadError::InvalidRange => "MODEL_DOWNLOAD_INVALID_RANGE",
            OmniModelDownloadError::SizeExceeded => "MODEL_DOWNLOAD_SIZE_EXCEEDED",
            OmniModelDownloadError::Cancelled => "MODEL_DOWNLOAD_CANCELLED",
            OmniModelDownloadError::ProgressState => "MODEL_PROGRESS_STATE_FAILED",
            OmniModelDownloadError::Io(_) => "MODEL_DOWNLOAD_IO_FAILED",
        },
        OmniModelManagerError::Cancelled => "MODEL_VERIFICATION_CANCELLED",
        OmniModelManagerError::InsufficientDisk { .. } => "MODEL_DISK_INSUFFICIENT",
        OmniModelManagerError::Busy => "MODEL_OPERATION_BUSY",
        OmniModelManagerError::NotActive => "MODEL_OPERATION_NOT_ACTIVE",
    }
}

fn download_error_is_corrupt(error: &OmniModelDownloadError) -> bool {
    matches!(
        error,
        OmniModelDownloadError::UrlNotAllowed
            | OmniModelDownloadError::InvalidResponse
            | OmniModelDownloadError::InvalidRange
            | OmniModelDownloadError::SizeExceeded
    )
}

fn verify_file(
    path: &Path,
    expected: &OmniModelFile,
    cancellation: &OmniCancellationToken,
) -> Result<(), OmniModelManagerError> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(OmniModelManagerError::Verification("MODEL_ARTIFACT_UNSAFE"));
    }
    if metadata.len() != expected.size {
        return Err(OmniModelManagerError::Verification(
            "MODEL_ARTIFACT_SIZE_MISMATCH",
        ));
    }
    let mut reader = BufReader::with_capacity(HASH_BUFFER_SIZE, fs::File::open(path)?);
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; HASH_BUFFER_SIZE];
    loop {
        if cancellation.is_cancelled() {
            return Err(OmniModelManagerError::Cancelled);
        }
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    if format!("{:x}", digest.finalize()) != expected.sha256 {
        return Err(OmniModelManagerError::Verification(
            "MODEL_ARTIFACT_DIGEST_MISMATCH",
        ));
    }
    Ok(())
}

fn collect_relative_files(root: &Path) -> Result<HashSet<String>, OmniModelManagerError> {
    let mut pending = vec![root.to_path_buf()];
    let mut files = HashSet::new();
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let metadata = entry.file_type()?;
            if metadata.is_symlink() {
                return Err(OmniModelManagerError::Verification("MODEL_LAYOUT_SYMLINK"));
            }
            if metadata.is_dir() {
                pending.push(entry.path());
            } else if metadata.is_file() {
                let relative = entry
                    .path()
                    .strip_prefix(root)
                    .map_err(|_| OmniModelManagerError::Verification("MODEL_LAYOUT_OUTSIDE_ROOT"))?
                    .to_string_lossy()
                    .replace('\\', "/")
                    .to_ascii_lowercase();
                files.insert(relative);
            } else {
                return Err(OmniModelManagerError::Verification(
                    "MODEL_LAYOUT_UNSUPPORTED_ENTRY",
                ));
            }
        }
    }
    Ok(files)
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::Mutex;

    use super::*;
    use crate::local_readiness::{LocalReadinessService, OmniRuntimeReadiness};
    use crate::omni_model_catalog::bundled_minicpm_o45_manifest;
    use crate::omni_model_download::OmniModelDownloader;
    use fairy_realtime_worker::RealtimeActivityProfile;

    struct FixtureTransfer {
        payloads: Vec<(String, Vec<u8>)>,
        requested: Mutex<Vec<String>>,
    }

    impl FixtureTransfer {
        fn new(manifest: &OmniModelManifest, contents: &[&[u8]; 3]) -> Self {
            Self {
                payloads: manifest
                    .files
                    .iter()
                    .zip(contents)
                    .map(|(file, content)| (file.path.clone(), content.to_vec()))
                    .collect(),
                requested: Mutex::new(Vec::new()),
            }
        }
    }

    impl OmniArtifactTransfer for FixtureTransfer {
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
            self.requested
                .lock()
                .expect("requested lock")
                .push(file.path.clone());
            let payload = self
                .payloads
                .iter()
                .find(|(path, _)| path == &file.path)
                .map(|(_, payload)| payload)
                .ok_or(OmniModelDownloadError::InvalidResponse)?;
            fs::write(partial_path, payload)?;
            progress(payload.len() as u64)
        }
    }

    fn fixture_manifest(contents: &[&[u8]; 3]) -> OmniModelManifest {
        let mut manifest = bundled_minicpm_o45_manifest().expect("manifest");
        for (file, content) in manifest.files.iter_mut().zip(contents) {
            file.size = content.len() as u64;
            file.sha256 = format!("{:x}", Sha256::digest(content));
        }
        manifest.manifest_digest = manifest.computed_digest().expect("digest");
        manifest.validate().expect("fixture manifest");
        manifest
    }

    fn write_staging(
        manager: &OmniModelManager,
        contents: &[&[u8]; 3],
    ) -> Result<(), OmniModelManagerError> {
        for (file, content) in manager.manifest.files.iter().zip(contents) {
            let path = manager
                .store
                .staging_artifact_path(&manager.manifest, file)?;
            fs::write(path, content)?;
        }
        Ok(())
    }

    #[test]
    fn disk_preflight_includes_temporary_and_post_install_allowances() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        assert_eq!(
            manager.required_disk_bytes().expect("required"),
            contents.iter().map(|value| value.len() as u64).sum::<u64>() + 7 * GIB
        );
    }

    #[test]
    fn disk_preflight_counts_only_bounded_resumable_partials() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        let partial = manager
            .store
            .staging_partial_path(&manager.manifest, &manager.manifest.files[0])
            .expect("partial path");
        fs::write(&partial, b"ll").expect("partial");
        assert_eq!(
            manager.required_disk_bytes().expect("required"),
            manager.manifest.total_size() - 2 + 7 * GIB
        );
        fs::write(&partial, b"oversized").expect("oversized partial");
        assert_eq!(
            manager.required_disk_bytes().expect("required"),
            manager.manifest.total_size() + 7 * GIB
        );
    }

    #[test]
    fn insufficient_disk_fails_before_download_state() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        assert!(matches!(
            manager.begin_install(GIB),
            Err(OmniModelManagerError::InsufficientDisk { .. })
        ));
        assert_eq!(manager.status().phase, OmniModelInstallPhase::Partial);
        assert_eq!(
            manager.status().error_code.as_deref(),
            Some("MODEL_DISK_INSUFFICIENT")
        );
    }

    #[test]
    fn only_one_operation_can_be_active_and_cancel_leaves_partial_state() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        manager.begin_install(8 * GIB).expect("begin install");
        assert!(matches!(
            manager.begin_verify(),
            Err(OmniModelManagerError::Busy)
        ));
        manager.request_cancel().expect("cancel");
        manager.finish_cancel().expect("finish cancel");
        assert_eq!(manager.status().phase, OmniModelInstallPhase::Partial);
    }

    #[test]
    fn verified_layout_promotes_atomically_but_runtime_remains_missing() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        manager.begin_install(8 * GIB).expect("begin install");
        write_staging(&manager, &contents).expect("staging files");
        manager
            .verify_staging_and_promote()
            .expect("verify and promote");

        assert_eq!(
            manager.status().phase,
            OmniModelInstallPhase::RuntimeMissing
        );
        assert!(manager
            .store
            .version_dir(&manager.manifest)
            .expect("version")
            .join("manifest.json")
            .is_file());
        assert!(!manager
            .store
            .staging_dir(&manager.manifest)
            .expect("staging")
            .exists());
    }

    #[test]
    fn transfer_downloads_in_manifest_order_and_cannot_mark_runtime_ready() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let manifest = fixture_manifest(&contents);
        let transfer = FixtureTransfer::new(&manifest, &contents);
        let expected_order: Vec<String> = manifest
            .files
            .iter()
            .map(|file| file.path.clone())
            .collect();
        let mut manager = OmniModelManager::new(directory.path(), manifest).expect("manager");

        manager.begin_install(8 * GIB).expect("begin install");
        manager
            .download_and_install(&transfer)
            .expect("download and install");

        assert_eq!(
            *transfer.requested.lock().expect("requested lock"),
            expected_order
        );
        assert_eq!(
            manager.status().phase,
            OmniModelInstallPhase::RuntimeMissing
        );
        assert_eq!(
            manager.status().error_code.as_deref(),
            Some("OMNI_RUNTIME_MISSING")
        );
        assert_eq!(
            manager.status().received_bytes,
            manager.manifest.total_size()
        );
        for (file, content) in manager.manifest.files.iter().zip(contents) {
            assert_eq!(
                fs::read(
                    manager
                        .store
                        .installed_artifact_path(&manager.manifest, file)
                        .expect("installed path")
                )
                .expect("installed artifact"),
                content
            );
        }
    }

    #[test]
    fn cancelled_verification_preserves_staging_as_partial() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        let cancellation = manager.begin_install(8 * GIB).expect("begin install");
        write_staging(&manager, &contents).expect("staging files");
        cancellation.cancel();
        assert!(matches!(
            manager.verify_staging_and_promote(),
            Err(OmniModelManagerError::Cancelled)
        ));
        assert_eq!(manager.status().phase, OmniModelInstallPhase::Partial);
        assert!(manager
            .store
            .staging_dir(&manager.manifest)
            .expect("staging")
            .exists());
    }

    #[test]
    fn checksum_failure_never_promotes_ready_state() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        manager.begin_install(8 * GIB).expect("begin install");
        let corrupt: [&[u8]; 3] = [b"bad", b"vision", b"audio"];
        write_staging(&manager, &corrupt).expect("staging files");
        assert!(matches!(
            manager.verify_staging_and_promote(),
            Err(OmniModelManagerError::Verification(
                "MODEL_ARTIFACT_DIGEST_MISMATCH"
            ))
        ));
        assert_eq!(manager.status().phase, OmniModelInstallPhase::Corrupt);
    }

    #[test]
    fn unexpected_files_fail_the_layout_gate() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        manager.begin_install(8 * GIB).expect("begin install");
        write_staging(&manager, &contents).expect("staging files");
        let extra = manager
            .store
            .staging_dir(&manager.manifest)
            .expect("staging")
            .join("unexpected.bin");
        fs::write(extra, b"unexpected").expect("extra file");

        assert!(matches!(
            manager.verify_staging_and_promote(),
            Err(OmniModelManagerError::Verification(
                "MODEL_LAYOUT_UNEXPECTED"
            ))
        ));
        assert_eq!(manager.status().phase, OmniModelInstallPhase::Corrupt);
    }

    #[test]
    fn restart_recovers_an_active_state_as_partial() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let manifest = fixture_manifest(&contents);
        let mut manager =
            OmniModelManager::new(directory.path(), manifest.clone()).expect("manager");
        manager.begin_install(8 * GIB).expect("begin install");
        drop(manager);

        let recovered = OmniModelManager::new(directory.path(), manifest).expect("recover");
        assert_eq!(recovered.status().phase, OmniModelInstallPhase::Partial);
        assert_eq!(
            recovered.status().error_code.as_deref(),
            Some("MODEL_OPERATION_INTERRUPTED")
        );
    }

    #[test]
    fn remove_clears_only_the_managed_version_and_resets_state() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let unrelated = directory.path().join("keep.txt");
        fs::write(&unrelated, b"keep").expect("unrelated file");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        manager.begin_install(8 * GIB).expect("begin install");
        write_staging(&manager, &contents).expect("staging files");
        manager
            .verify_staging_and_promote()
            .expect("verify and promote");
        manager.remove().expect("remove");

        assert_eq!(manager.status().phase, OmniModelInstallPhase::NotInstalled);
        assert!(unrelated.exists());
        assert!(!manager
            .store
            .version_dir(&manager.manifest)
            .expect("version")
            .exists());
    }

    #[test]
    fn runtime_ready_requires_an_explicit_self_test_transition() {
        let contents: [&[u8]; 3] = [b"llm", b"vision", b"audio"];
        let directory = tempfile::tempdir().expect("models root");
        let mut manager =
            OmniModelManager::new(directory.path(), fixture_manifest(&contents)).expect("manager");
        manager.begin_install(8 * GIB).expect("begin install");
        write_staging(&manager, &contents).expect("staging files");
        manager
            .verify_staging_and_promote()
            .expect("verify and promote");
        assert_eq!(
            manager.status().phase,
            OmniModelInstallPhase::RuntimeMissing
        );

        manager
            .begin_runtime_self_test()
            .expect("begin runtime self-test");
        assert_eq!(
            manager.status().phase,
            OmniModelInstallPhase::RuntimeSelfTest
        );
        manager
            .finish_runtime_self_test(Some("OMNI_SELF_TEST_CRASHED"))
            .expect("failed runtime self-test");
        assert_eq!(
            manager.status().phase,
            OmniModelInstallPhase::SelfTestFailed
        );
        assert_eq!(
            manager.status().error_code.as_deref(),
            Some("OMNI_SELF_TEST_CRASHED")
        );

        manager
            .begin_runtime_self_test()
            .expect("retry runtime self-test");
        manager
            .finish_runtime_self_test(None)
            .expect("passed runtime self-test");
        assert_eq!(manager.status().phase, OmniModelInstallPhase::Ready);
    }

    #[test]
    #[ignore = "downloads the pinned production model and requires a CUDA-capable NVIDIA GPU"]
    fn live_minicpm_o45_install_and_cuda_self_test() {
        let models_root = required_live_directory("FAIRY_LIVE_OMNI_MODELS_ROOT");
        let runtime_root = required_live_directory("FAIRY_LIVE_OMNI_RUNTIME_ROOT");
        assert!(
            models_root.ends_with("models"),
            "FAIRY_LIVE_OMNI_MODELS_ROOT must end with a dedicated models directory"
        );
        assert!(
            runtime_root.join("omni").is_dir(),
            "FAIRY_LIVE_OMNI_RUNTIME_ROOT must contain the staged omni directory"
        );

        let manifest = bundled_minicpm_o45_manifest().expect("production manifest");
        let mut manager =
            OmniModelManager::new(&models_root, manifest.clone()).expect("model manager");
        match manager.status().phase {
            OmniModelInstallPhase::NotInstalled | OmniModelInstallPhase::Partial => {
                manager
                    .begin_install(u64::MAX)
                    .expect("begin governed model install");
                manager
                    .download_and_install(
                        &OmniModelDownloader::new().expect("production model downloader"),
                    )
                    .expect("download, verify, and atomically promote pinned model");
            }
            OmniModelInstallPhase::Ready
            | OmniModelInstallPhase::SelfTestFailed
            | OmniModelInstallPhase::Corrupt => {
                manager
                    .begin_verify()
                    .expect("begin installed verification");
                manager
                    .verify_installed()
                    .expect("verify installed model before CUDA probe");
            }
            OmniModelInstallPhase::RuntimeMissing => {}
            phase => panic!("live model store is not idle: {phase:?}"),
        }
        assert_eq!(
            manager.status().phase,
            OmniModelInstallPhase::RuntimeMissing
        );

        manager
            .begin_runtime_self_test()
            .expect("begin production CUDA self-test");
        let mut readiness =
            LocalReadinessService::production(&models_root, &runtime_root, manifest)
                .expect("production readiness service");
        let self_test = readiness.run_self_test(manager.status());
        match self_test {
            Ok(report) => {
                assert!(report.cuda_compiled);
                assert!(report.backend_ready);
                assert_eq!(report.model_probe, "ready");
                manager
                    .finish_runtime_self_test(None)
                    .expect("persist ready model state");
            }
            Err(error) => {
                manager
                    .finish_runtime_self_test(Some("OMNI_LIVE_SELF_TEST_FAILED"))
                    .expect("persist failed live self-test state");
                panic!("production CUDA self-test failed: {error}");
            }
        }

        let report = readiness
            .report(manager.status(), RealtimeActivityProfile::Focus, true)
            .expect("live readiness report");
        assert_eq!(manager.status().phase, OmniModelInstallPhase::Ready);
        assert_eq!(report.runtime, OmniRuntimeReadiness::Passed);
        eprintln!(
            "live local readiness: {}",
            serde_json::to_string(&report).expect("serialize live readiness report")
        );
    }

    fn required_live_directory(name: &str) -> PathBuf {
        let path = PathBuf::from(
            std::env::var_os(name)
                .unwrap_or_else(|| panic!("{name} must be set for the ignored live model gate")),
        );
        assert!(path.is_absolute(), "{name} must be an absolute path");
        assert!(path.is_dir(), "{name} must be an existing directory");
        path
    }
}
