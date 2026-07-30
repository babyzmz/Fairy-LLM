use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use fairy_realtime_worker::RealtimeActivityProfile;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use uuid::Uuid;

use crate::hardware_capabilities::{
    evaluate_local_beta_readiness, HardwareCapabilityFacts, HardwareCapabilityReport,
};
use crate::hardware_probe::{probe_hardware, HardwareProbeReport};
use crate::omni_model_manifest::OmniModelManifest;
use crate::omni_model_store::{
    OmniModelInstallPhase, OmniModelInstallState, OmniModelStore, OmniModelStoreError,
};
use crate::omni_runtime_self_test::{
    runtime_request, OmniRuntimeSelfTestError, OmniRuntimeSelfTestReport,
    OmniRuntimeSelfTestRunner, ProcessOmniRuntimeSelfTestRunner,
};

const HARDWARE_CACHE_TTL: Duration = Duration::from_secs(15);
const MIB: u64 = 1024 * 1024;
const GIB: u64 = 1024 * 1024 * 1024;
const INSTALL_TEMPORARY_ALLOWANCE: u64 = 2 * GIB;
const INSTALL_POST_FREE_SPACE: u64 = 5 * GIB;
const DEFAULT_RUNTIME_HEADROOM: u64 = 512 * MIB;
const RUNTIME_RELATIVE_PATH: [&str; 2] = ["omni", "fairy-omni-runtime.exe"];
const ATTESTATION_SCHEMA_VERSION: u16 = 1;
const ATTESTATION_FILE_NAME: &str = "runtime-attestation.json";
const MAX_ATTESTATION_BYTES: u64 = 64 * 1024;

pub trait HardwareProbeSource: Send + Sync {
    fn probe(&self, model_root: &Path) -> HardwareProbeReport;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemHardwareProbe;

impl HardwareProbeSource for SystemHardwareProbe {
    fn probe(&self, model_root: &Path) -> HardwareProbeReport {
        probe_hardware(model_root)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OmniRuntimeReadiness {
    Missing,
    NotTested,
    Passed,
    Failed,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LocalReadinessReport {
    pub schema_version: u16,
    pub profile: RealtimeActivityProfile,
    pub hardware: HardwareProbeReport,
    pub hardware_cached: bool,
    pub model: OmniModelInstallState,
    pub model_shallow_present: bool,
    pub model_install_required_bytes: u64,
    pub runtime: OmniRuntimeReadiness,
    pub runtime_error_code: Option<String>,
    pub capability: HardwareCapabilityReport,
}

#[derive(Debug, Error)]
pub enum LocalReadinessError {
    #[error(transparent)]
    Store(#[from] OmniModelStoreError),
    #[error(transparent)]
    Runtime(#[from] OmniRuntimeSelfTestError),
    #[error("the local readiness size calculation overflowed")]
    SizeOverflow,
    #[error("the local runtime verification attestation could not be saved")]
    AttestationWrite,
}

enum SelfTestEvidence {
    Passed(OmniRuntimeSelfTestReport),
    Failed(String),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct LocalRuntimeAttestation {
    schema_version: u16,
    verified_at_unix_ms: u64,
    model_layout_fingerprint: String,
    runtime_sha256: String,
    runtime_size: u64,
    runtime_modified_unix_ns: u128,
    adapter_luid: String,
    dedicated_vram_bytes: u64,
    driver_api_version: i32,
    report: OmniRuntimeSelfTestReport,
}

pub struct LocalReadinessService {
    store: OmniModelStore,
    manifest: OmniModelManifest,
    runtime_path: PathBuf,
    hardware_probe: Box<dyn HardwareProbeSource>,
    self_test_runner: Box<dyn OmniRuntimeSelfTestRunner>,
    hardware_cache: Option<(Instant, HardwareProbeReport)>,
    self_test_evidence: Option<SelfTestEvidence>,
    attestation_path: PathBuf,
    attestation: Option<LocalRuntimeAttestation>,
    runtime_quarantined: bool,
}

impl LocalReadinessService {
    pub fn production(
        models_root: &Path,
        runtime_root: &Path,
        manifest: OmniModelManifest,
    ) -> Result<Self, LocalReadinessError> {
        Self::new(
            models_root,
            runtime_root,
            manifest,
            Box::new(SystemHardwareProbe),
            Box::new(ProcessOmniRuntimeSelfTestRunner),
        )
    }

    pub fn new(
        models_root: &Path,
        runtime_root: &Path,
        manifest: OmniModelManifest,
        hardware_probe: Box<dyn HardwareProbeSource>,
        self_test_runner: Box<dyn OmniRuntimeSelfTestRunner>,
    ) -> Result<Self, LocalReadinessError> {
        manifest
            .validate()
            .map_err(|_| OmniModelStoreError::InvalidState)?;
        let store = OmniModelStore::new(models_root)?;
        let runtime_path = RUNTIME_RELATIVE_PATH
            .iter()
            .fold(runtime_root.to_path_buf(), |path, component| {
                path.join(component)
            });
        let attestation_path = store.root().join(ATTESTATION_FILE_NAME);
        let attestation = load_attestation(&attestation_path);
        Ok(Self {
            store,
            manifest,
            runtime_path,
            hardware_probe,
            self_test_runner,
            hardware_cache: None,
            self_test_evidence: None,
            attestation_path,
            attestation,
            runtime_quarantined: false,
        })
    }

    pub fn report(
        &mut self,
        model: &OmniModelInstallState,
        profile: RealtimeActivityProfile,
        refresh_hardware: bool,
    ) -> Result<LocalReadinessReport, LocalReadinessError> {
        let (hardware, hardware_cached) = self.hardware(refresh_hardware);
        let model_shallow_present = self.shallow_model_present()?;
        let model_identity_matches = model.model_version == self.manifest.version
            && model.manifest_digest == self.manifest.manifest_digest
            && model.total_bytes == self.manifest.total_size();
        let model_lifecycle_installed = model_identity_matches && installed_phase(model.phase);
        let model_installed = model_lifecycle_installed && model_shallow_present;
        let model_verified = model_installed;
        let runtime_installed = regular_file(&self.runtime_path);
        self.reconcile_attestation(model, &hardware, model_installed, runtime_installed);
        let (runtime, runtime_error_code, self_test_passed, predicted_peak) =
            self.runtime_projection(runtime_installed);
        let mut facts = empty_facts();
        hardware.populate_capability_facts(&mut facts);
        facts.disk_required_bytes = Some(0);
        facts.model_installed = Some(model_installed);
        facts.model_verified = Some(model_verified);
        facts.runtime_installed = Some(runtime_installed);
        facts.runtime_self_test_passed = Some(self_test_passed);
        facts.runtime_quarantined = Some(self.runtime_quarantined);
        facts.predicted_model_peak_bytes = if model_verified {
            Some(
                predicted_peak.unwrap_or(
                    self.manifest
                        .predicted_peak_vram_mb
                        .checked_mul(MIB)
                        .ok_or(LocalReadinessError::SizeOverflow)?,
                ),
            )
        } else {
            None
        };
        facts.runtime_headroom_bytes = DEFAULT_RUNTIME_HEADROOM;
        let capability = evaluate_local_beta_readiness(&facts, profile);
        Ok(LocalReadinessReport {
            schema_version: 1,
            profile,
            hardware,
            hardware_cached,
            model: model.clone(),
            model_shallow_present,
            model_install_required_bytes: self.install_required_bytes(model)?,
            runtime,
            runtime_error_code,
            capability,
        })
    }

    pub fn run_self_test(
        &mut self,
        model: &OmniModelInstallState,
    ) -> Result<OmniRuntimeSelfTestReport, LocalReadinessError> {
        self.invalidate_verification_attestation();
        if !self.shallow_model_present()?
            || !matches!(
                model.phase,
                OmniModelInstallPhase::RuntimeSelfTest
                    | OmniModelInstallPhase::RuntimeMissing
                    | OmniModelInstallPhase::SelfTestFailed
                    | OmniModelInstallPhase::Ready
            )
        {
            let error = OmniRuntimeSelfTestError::InvalidResponse;
            self.self_test_evidence = Some(SelfTestEvidence::Failed(
                runtime_error_code(&error).to_owned(),
            ));
            return Err(error.into());
        }
        if !regular_file(&self.runtime_path) {
            let error = OmniRuntimeSelfTestError::RuntimeMissing;
            self.self_test_evidence = Some(SelfTestEvidence::Failed(
                runtime_error_code(&error).to_owned(),
            ));
            return Err(error.into());
        }
        self.runtime_quarantined = false;
        let model_root = self.store.version_dir(&self.manifest)?;
        let request = runtime_request(self.runtime_path.clone(), model_root, &self.manifest);
        match self.self_test_runner.run(&request) {
            Ok(report) => {
                let (hardware, _) = self.hardware(false);
                let Some(attestation) = self.build_attestation(&report, &hardware) else {
                    self.self_test_evidence = Some(SelfTestEvidence::Failed(
                        "OMNI_ATTESTATION_WRITE_FAILED".to_owned(),
                    ));
                    return Err(LocalReadinessError::AttestationWrite);
                };
                if save_attestation(&self.attestation_path, &attestation).is_err() {
                    self.self_test_evidence = Some(SelfTestEvidence::Failed(
                        "OMNI_ATTESTATION_WRITE_FAILED".to_owned(),
                    ));
                    return Err(LocalReadinessError::AttestationWrite);
                }
                self.attestation = Some(attestation);
                self.self_test_evidence = Some(SelfTestEvidence::Passed(report.clone()));
                Ok(report)
            }
            Err(error) => {
                self.self_test_evidence = Some(SelfTestEvidence::Failed(
                    runtime_error_code(&error).to_owned(),
                ));
                Err(error.into())
            }
        }
    }

    pub fn invalidate_hardware_cache(&mut self) {
        self.hardware_cache = None;
    }

    pub fn mark_runtime_quarantined(&mut self) {
        self.runtime_quarantined = true;
        self.invalidate_verification_attestation();
        self.self_test_evidence = Some(SelfTestEvidence::Failed(
            "OMNI_RUNTIME_QUARANTINED".to_owned(),
        ));
    }

    pub fn invalidate_verification_attestation(&mut self) {
        self.attestation = None;
        self.self_test_evidence = None;
        match fs::remove_file(&self.attestation_path) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => {}
        }
    }

    fn reconcile_attestation(
        &mut self,
        model: &OmniModelInstallState,
        hardware: &HardwareProbeReport,
        model_installed: bool,
        runtime_installed: bool,
    ) {
        let Some(attestation) = self.attestation.clone() else {
            return;
        };
        let restoring = self.self_test_evidence.is_none();
        if !self.attestation_matches(
            &attestation,
            model,
            hardware,
            model_installed,
            runtime_installed,
            restoring,
        ) {
            self.invalidate_verification_attestation();
            return;
        }
        if restoring {
            self.self_test_evidence = Some(SelfTestEvidence::Passed(attestation.report.clone()));
        }
    }

    fn build_attestation(
        &self,
        report: &OmniRuntimeSelfTestReport,
        hardware: &HardwareProbeReport,
    ) -> Option<LocalRuntimeAttestation> {
        let adapter = hardware.adapter.as_ref()?;
        let driver_api_version = hardware.cuda.driver_api_version?;
        if !hardware.cuda.available
            || !hardware.cuda.driver_compatible
            || !hardware.cuda.adapter_luid_matches
            || adapter.luid.is_empty()
            || adapter.luid.len() > 64
        {
            return None;
        }
        let runtime = runtime_identity(&self.runtime_path, true)?;
        Some(LocalRuntimeAttestation {
            schema_version: ATTESTATION_SCHEMA_VERSION,
            verified_at_unix_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .ok()?
                .as_millis()
                .try_into()
                .ok()?,
            model_layout_fingerprint: self.model_layout_fingerprint()?,
            runtime_sha256: runtime.sha256?,
            runtime_size: runtime.size,
            runtime_modified_unix_ns: runtime.modified_unix_ns,
            adapter_luid: adapter.luid.clone(),
            dedicated_vram_bytes: adapter.dedicated_vram_bytes,
            driver_api_version,
            report: report.clone(),
        })
    }

    fn attestation_matches(
        &self,
        attestation: &LocalRuntimeAttestation,
        model: &OmniModelInstallState,
        hardware: &HardwareProbeReport,
        model_installed: bool,
        runtime_installed: bool,
        verify_runtime_digest: bool,
    ) -> bool {
        let Some(adapter) = hardware.adapter.as_ref() else {
            return false;
        };
        let Some(driver_api_version) = hardware.cuda.driver_api_version else {
            return false;
        };
        let Some(runtime) = runtime_identity(&self.runtime_path, verify_runtime_digest) else {
            return false;
        };
        let report = &attestation.report;
        attestation.schema_version == ATTESTATION_SCHEMA_VERSION
            && model_installed
            && runtime_installed
            && model.model_version == self.manifest.version
            && model.manifest_digest == self.manifest.manifest_digest
            && report.runtime_compatibility == self.manifest.runtime_compatibility
            && report.manifest_digest == self.manifest.manifest_digest
            && report.model_version == self.manifest.version
            && report.upstream_runtime_revision == self.manifest.upstream_runtime_revision
            && report.patch_set_digest == self.manifest.patch_set_digest
            && report.build_profile == "production-cuda"
            && report.cuda_compiled
            && report.backend_ready
            && report.model_probe == "ready"
            && is_sha256(&attestation.model_layout_fingerprint)
            && is_sha256(&attestation.runtime_sha256)
            && self.model_layout_fingerprint().as_deref()
                == Some(attestation.model_layout_fingerprint.as_str())
            && runtime.size == attestation.runtime_size
            && runtime.modified_unix_ns == attestation.runtime_modified_unix_ns
            && runtime
                .sha256
                .as_deref()
                .is_none_or(|digest| digest == attestation.runtime_sha256)
            && hardware.cuda.available
            && hardware.cuda.driver_compatible
            && hardware.cuda.adapter_luid_matches
            && adapter.luid == attestation.adapter_luid
            && adapter.dedicated_vram_bytes == attestation.dedicated_vram_bytes
            && driver_api_version == attestation.driver_api_version
    }

    fn model_layout_fingerprint(&self) -> Option<String> {
        let version = self.store.version_dir(&self.manifest).ok()?;
        let mut paths = Vec::with_capacity(self.manifest.files.len() + 1);
        paths.push(("manifest.json".to_owned(), version.join("manifest.json")));
        paths.extend(self.manifest.files.iter().map(|file| {
            (
                file.path.clone(),
                version.join(file.path.replace('/', std::path::MAIN_SEPARATOR_STR)),
            )
        }));
        let mut digest = Sha256::new();
        for (relative, path) in paths {
            let metadata = fs::symlink_metadata(path).ok()?;
            if !metadata.is_file() || metadata.file_type().is_symlink() {
                return None;
            }
            let modified = modified_unix_ns(&metadata)?;
            digest.update(relative.as_bytes());
            digest.update([0]);
            digest.update(metadata.len().to_le_bytes());
            digest.update(modified.to_le_bytes());
        }
        Some(format!("{:x}", digest.finalize()))
    }

    fn hardware(&mut self, refresh: bool) -> (HardwareProbeReport, bool) {
        if !refresh {
            if let Some((sampled_at, report)) = &self.hardware_cache {
                if sampled_at.elapsed() < HARDWARE_CACHE_TTL {
                    return (report.clone(), true);
                }
            }
        }
        let report = self.hardware_probe.probe(self.store.root());
        self.hardware_cache = Some((Instant::now(), report.clone()));
        (report, false)
    }

    fn shallow_model_present(&self) -> Result<bool, LocalReadinessError> {
        let version = self.store.version_dir(&self.manifest)?;
        if !regular_directory(&version) || !regular_file(&version.join("manifest.json")) {
            return Ok(false);
        }
        if self.store.validate_managed_tree(&version).is_err() {
            return Ok(false);
        }
        Ok(self.manifest.files.iter().all(|file| {
            regular_file(&version.join(file.path.replace('/', std::path::MAIN_SEPARATOR_STR)))
        }))
    }

    fn install_required_bytes(
        &self,
        model: &OmniModelInstallState,
    ) -> Result<u64, LocalReadinessError> {
        if installed_phase(model.phase) && self.shallow_model_present()? {
            return Ok(INSTALL_POST_FREE_SPACE);
        }
        self.manifest
            .total_size()
            .saturating_sub(model.received_bytes.min(self.manifest.total_size()))
            .checked_add(INSTALL_TEMPORARY_ALLOWANCE)
            .and_then(|bytes| bytes.checked_add(INSTALL_POST_FREE_SPACE))
            .ok_or(LocalReadinessError::SizeOverflow)
    }

    fn runtime_projection(
        &self,
        runtime_installed: bool,
    ) -> (OmniRuntimeReadiness, Option<String>, bool, Option<u64>) {
        if !runtime_installed {
            return (
                OmniRuntimeReadiness::Missing,
                Some("OMNI_RUNTIME_MISSING".to_owned()),
                false,
                None,
            );
        }
        if self.runtime_quarantined {
            return (
                OmniRuntimeReadiness::Failed,
                Some("OMNI_RUNTIME_QUARANTINED".to_owned()),
                false,
                None,
            );
        }
        match &self.self_test_evidence {
            Some(SelfTestEvidence::Passed(report)) => (
                OmniRuntimeReadiness::Passed,
                None,
                true,
                Some(report.predicted_model_peak_bytes),
            ),
            Some(SelfTestEvidence::Failed(code)) => (
                OmniRuntimeReadiness::Failed,
                Some(code.clone()),
                false,
                None,
            ),
            None => (
                OmniRuntimeReadiness::NotTested,
                Some("OMNI_RUNTIME_NOT_TESTED".to_owned()),
                false,
                None,
            ),
        }
    }
}

struct RuntimeIdentity {
    size: u64,
    modified_unix_ns: u128,
    sha256: Option<String>,
}

fn runtime_identity(path: &Path, include_digest: bool) -> Option<RuntimeIdentity> {
    let metadata = fs::symlink_metadata(path).ok()?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return None;
    }
    let sha256 = if include_digest {
        let mut file = File::open(path).ok()?;
        let mut digest = Sha256::new();
        let mut buffer = [0_u8; 1024 * 1024];
        loop {
            let read = file.read(&mut buffer).ok()?;
            if read == 0 {
                break;
            }
            digest.update(&buffer[..read]);
        }
        Some(format!("{:x}", digest.finalize()))
    } else {
        None
    };
    Some(RuntimeIdentity {
        size: metadata.len(),
        modified_unix_ns: modified_unix_ns(&metadata)?,
        sha256,
    })
}

fn modified_unix_ns(metadata: &fs::Metadata) -> Option<u128> {
    metadata
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()
        .map(|duration| duration.as_nanos())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn load_attestation(path: &Path) -> Option<LocalRuntimeAttestation> {
    if !regular_file(path) || fs::metadata(path).ok()?.len() > MAX_ATTESTATION_BYTES {
        return None;
    }
    let attestation =
        serde_json::from_slice::<LocalRuntimeAttestation>(&fs::read(path).ok()?).ok()?;
    if attestation.schema_version != ATTESTATION_SCHEMA_VERSION
        || !is_sha256(&attestation.model_layout_fingerprint)
        || !is_sha256(&attestation.runtime_sha256)
        || attestation.adapter_luid.is_empty()
        || attestation.adapter_luid.len() > 64
    {
        return None;
    }
    Some(attestation)
}

fn save_attestation(
    path: &Path,
    attestation: &LocalRuntimeAttestation,
) -> Result<(), std::io::Error> {
    let parent = path
        .parent()
        .ok_or_else(|| std::io::Error::other("attestation path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".runtime-attestation.{}.tmp", Uuid::new_v4()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        let payload = serde_json::to_vec_pretty(attestation).map_err(std::io::Error::other)?;
        file.write_all(&payload)?;
        file.sync_all()?;
        replace_file(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(target_os = "windows")]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source = source
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    fs::rename(source, destination)
}

fn regular_file(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.is_file() && !metadata.file_type().is_symlink())
}

fn regular_directory(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.is_dir() && !metadata.file_type().is_symlink())
}

fn installed_phase(phase: OmniModelInstallPhase) -> bool {
    matches!(
        phase,
        OmniModelInstallPhase::RuntimeSelfTest
            | OmniModelInstallPhase::RuntimeMissing
            | OmniModelInstallPhase::SelfTestFailed
            | OmniModelInstallPhase::Ready
    )
}

pub fn runtime_error_code(error: &OmniRuntimeSelfTestError) -> &'static str {
    match error {
        OmniRuntimeSelfTestError::RuntimeMissing => "OMNI_RUNTIME_MISSING",
        OmniRuntimeSelfTestError::RuntimeUnsafe => "OMNI_RUNTIME_UNSAFE",
        OmniRuntimeSelfTestError::Timeout => "OMNI_SELF_TEST_TIMEOUT",
        OmniRuntimeSelfTestError::Crash => "OMNI_SELF_TEST_CRASHED",
        OmniRuntimeSelfTestError::OutputTooLarge => "OMNI_SELF_TEST_OUTPUT_LIMIT",
        OmniRuntimeSelfTestError::InvalidResponse => "OMNI_SELF_TEST_INVALID_RESPONSE",
        OmniRuntimeSelfTestError::ProtocolMismatch => "OMNI_PROTOCOL_MISMATCH",
        OmniRuntimeSelfTestError::DigestMismatch => "OMNI_MANIFEST_MISMATCH",
        OmniRuntimeSelfTestError::ModelVersionMismatch => "OMNI_MODEL_VERSION_MISMATCH",
        OmniRuntimeSelfTestError::UpstreamRevisionMismatch => "OMNI_UPSTREAM_REVISION_MISMATCH",
        OmniRuntimeSelfTestError::PatchSetMismatch => "OMNI_PATCH_SET_MISMATCH",
        OmniRuntimeSelfTestError::BuildProfileMismatch => "OMNI_RUNTIME_NOT_PRODUCTION",
        OmniRuntimeSelfTestError::CudaUnavailable => "OMNI_RUNTIME_CUDA_UNAVAILABLE",
        OmniRuntimeSelfTestError::BackendNotReady => "OMNI_RUNTIME_BACKEND_NOT_READY",
        OmniRuntimeSelfTestError::ModelProbeFailed => "OMNI_RUNTIME_MODEL_PROBE_FAILED",
        OmniRuntimeSelfTestError::Io(_) => "OMNI_SELF_TEST_IO_FAILED",
    }
}

fn empty_facts() -> HardwareCapabilityFacts {
    HardwareCapabilityFacts {
        windows_supported: None,
        architecture_x64: None,
        gpu_vendor: None,
        dedicated_vram_bytes: None,
        budget_bytes: None,
        current_usage_bytes: None,
        cuda_available: None,
        driver_compatible: None,
        adapter_luid_matches: None,
        avx2_available: None,
        system_total_bytes: None,
        disk_available_bytes: None,
        disk_required_bytes: None,
        model_installed: None,
        model_verified: None,
        runtime_installed: None,
        runtime_self_test_passed: None,
        runtime_quarantined: None,
        predicted_model_peak_bytes: None,
        runtime_headroom_bytes: 0,
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    use crate::hardware_capabilities::{GpuVendor, LocalBetaReadinessReason};
    use crate::hardware_probe::{CudaDriverReport, HardwareAdapterReport};
    use crate::omni_model_catalog::bundled_minicpm_o45_manifest;

    use super::*;

    struct CountingProbe(Arc<AtomicUsize>);

    impl HardwareProbeSource for CountingProbe {
        fn probe(&self, _model_root: &Path) -> HardwareProbeReport {
            self.0.fetch_add(1, Ordering::SeqCst);
            eligible_hardware_report()
        }
    }

    struct FixedProbe(HardwareProbeReport);

    impl HardwareProbeSource for FixedProbe {
        fn probe(&self, _model_root: &Path) -> HardwareProbeReport {
            self.0.clone()
        }
    }

    fn eligible_hardware_report() -> HardwareProbeReport {
        HardwareProbeReport {
            schema_version: 1,
            windows_supported: true,
            architecture_x64: true,
            avx2_available: true,
            system_total_bytes: Some(32 * GIB),
            disk_available_bytes: Some(40 * GIB),
            adapter: Some(HardwareAdapterReport {
                name: "Test NVIDIA".to_owned(),
                vendor: GpuVendor::Nvidia,
                vendor_id: 0x10de,
                dedicated_vram_bytes: 24 * GIB,
                budget_bytes: Some(22 * GIB),
                current_usage_bytes: Some(2 * GIB),
                luid: "00000000:00000001".to_owned(),
            }),
            cuda: CudaDriverReport {
                available: true,
                driver_api_version: Some(12_080),
                driver_compatible: true,
                device_count: 1,
                matched_device_ordinal: Some(0),
                adapter_luid_matches: true,
                error_code: None,
            },
            error_code: None,
        }
    }

    struct FakeSelfTest {
        result: Result<OmniRuntimeSelfTestReport, &'static str>,
    }

    impl OmniRuntimeSelfTestRunner for FakeSelfTest {
        fn run(
            &self,
            request: &crate::omni_runtime_self_test::OmniRuntimeSelfTestRequest,
        ) -> Result<OmniRuntimeSelfTestReport, OmniRuntimeSelfTestError> {
            self.result.clone().map_err(|_| {
                let _ = request;
                OmniRuntimeSelfTestError::Crash
            })
        }
    }

    fn write_shallow_model(
        models_root: &Path,
        manifest: &OmniModelManifest,
    ) -> OmniModelInstallState {
        let store = OmniModelStore::new(models_root).expect("store");
        let version = store.version_dir(manifest).expect("version");
        fs::create_dir_all(&version).expect("version directory");
        fs::write(
            version.join("manifest.json"),
            serde_json::to_vec(manifest).expect("manifest json"),
        )
        .expect("manifest");
        for file in &manifest.files {
            let path = version.join(file.path.replace('/', std::path::MAIN_SEPARATOR_STR));
            fs::create_dir_all(path.parent().expect("parent")).expect("artifact parent");
            fs::write(path, []).expect("artifact presence");
        }
        let mut state = OmniModelInstallState::initial(manifest);
        state.phase = OmniModelInstallPhase::RuntimeMissing;
        state.received_bytes = state.total_bytes;
        state
    }

    fn write_runtime(runtime_root: &Path, contents: &[u8]) -> PathBuf {
        let runtime_path = runtime_root.join("omni").join("fairy-omni-runtime.exe");
        fs::create_dir_all(runtime_path.parent().expect("runtime parent")).expect("runtime dir");
        fs::write(&runtime_path, contents).expect("runtime presence");
        runtime_path
    }

    fn passed_self_test_report(manifest: &OmniModelManifest) -> OmniRuntimeSelfTestReport {
        OmniRuntimeSelfTestReport {
            schema_version: 2,
            runtime_compatibility: manifest.runtime_compatibility.clone(),
            manifest_digest: manifest.manifest_digest.clone(),
            model_version: manifest.version.clone(),
            predicted_model_peak_bytes: 9 * GIB,
            upstream_runtime_revision: manifest.upstream_runtime_revision.clone(),
            patch_set_digest: manifest.patch_set_digest.clone(),
            build_profile: "production-cuda".to_owned(),
            cuda_compiled: true,
            backend_ready: true,
            model_probe: "ready".to_owned(),
        }
    }

    fn persist_valid_attestation(
        models_root: &Path,
        runtime_root: &Path,
        manifest: &OmniModelManifest,
        state: &OmniModelInstallState,
    ) {
        let mut service = LocalReadinessService::new(
            models_root,
            runtime_root,
            manifest.clone(),
            Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
            Box::new(FakeSelfTest {
                result: Ok(passed_self_test_report(manifest)),
            }),
        )
        .expect("verified service");
        service.run_self_test(state).expect("self-test");
    }

    #[test]
    fn phase_one_runtime_missing_is_truthful_and_never_ready() {
        let models = tempfile::tempdir().expect("models");
        let runtime = tempfile::tempdir().expect("runtime");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let state = write_shallow_model(models.path(), &manifest);
        let calls = Arc::new(AtomicUsize::new(0));
        let mut service = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest,
            Box::new(CountingProbe(calls)),
            Box::new(FakeSelfTest {
                result: Err("must not run"),
            }),
        )
        .expect("service");

        let report = service
            .report(&state, RealtimeActivityProfile::Focus, false)
            .expect("report");

        assert!(report.model_shallow_present);
        assert_eq!(report.runtime, OmniRuntimeReadiness::Missing);
        assert_eq!(
            report.capability.reason,
            LocalBetaReadinessReason::RuntimeMissing
        );
        assert!(!report.capability.local_beta_eligible);
    }

    #[test]
    fn hardware_is_cached_but_explicit_refresh_bypasses_it() {
        let models = tempfile::tempdir().expect("models");
        let runtime = tempfile::tempdir().expect("runtime");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let state = OmniModelInstallState::initial(&manifest);
        let calls = Arc::new(AtomicUsize::new(0));
        let mut service = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest,
            Box::new(CountingProbe(calls.clone())),
            Box::new(FakeSelfTest {
                result: Err("unused"),
            }),
        )
        .expect("service");

        assert!(
            !service
                .report(&state, RealtimeActivityProfile::Auto, false)
                .expect("first")
                .hardware_cached
        );
        assert!(
            service
                .report(&state, RealtimeActivityProfile::Auto, false)
                .expect("cached")
                .hardware_cached
        );
        assert!(
            !service
                .report(&state, RealtimeActivityProfile::Auto, true)
                .expect("refresh")
                .hardware_cached
        );
        assert_eq!(calls.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn explicit_self_test_can_supply_the_budget_but_navigation_cannot_run_it() {
        let models = tempfile::tempdir().expect("models");
        let runtime = tempfile::tempdir().expect("runtime");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let state = write_shallow_model(models.path(), &manifest);
        let runtime_path = runtime.path().join("omni").join("fairy-omni-runtime.exe");
        fs::create_dir_all(runtime_path.parent().expect("runtime parent")).expect("runtime dir");
        fs::write(&runtime_path, b"fixture").expect("runtime presence");
        let self_test_report = OmniRuntimeSelfTestReport {
            schema_version: 2,
            runtime_compatibility: manifest.runtime_compatibility.clone(),
            manifest_digest: manifest.manifest_digest.clone(),
            model_version: manifest.version.clone(),
            predicted_model_peak_bytes: 9 * GIB,
            upstream_runtime_revision: manifest.upstream_runtime_revision.clone(),
            patch_set_digest: manifest.patch_set_digest.clone(),
            build_profile: "production-cuda".to_owned(),
            cuda_compiled: true,
            backend_ready: true,
            model_probe: "ready".to_owned(),
        };
        let mut service = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest,
            Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
            Box::new(FakeSelfTest {
                result: Ok(self_test_report),
            }),
        )
        .expect("service");

        let before = service
            .report(&state, RealtimeActivityProfile::Focus, false)
            .expect("before");
        assert_eq!(before.runtime, OmniRuntimeReadiness::NotTested);
        assert!(!before.capability.local_beta_eligible);

        service.run_self_test(&state).expect("self-test");
        let after = service
            .report(&state, RealtimeActivityProfile::Focus, false)
            .expect("after");
        assert_eq!(after.runtime, OmniRuntimeReadiness::Passed);
        assert!(after.capability.local_beta_eligible);
        assert_eq!(
            after.capability.required_budget_bytes,
            Some(9 * GIB + DEFAULT_RUNTIME_HEADROOM)
        );
    }

    #[test]
    fn successful_self_test_restores_after_reopen_without_running_again() {
        let models = tempfile::tempdir().expect("models");
        let runtime = tempfile::tempdir().expect("runtime");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let state = write_shallow_model(models.path(), &manifest);
        let runtime_path = runtime.path().join("omni").join("fairy-omni-runtime.exe");
        fs::create_dir_all(runtime_path.parent().expect("runtime parent")).expect("runtime dir");
        fs::write(&runtime_path, b"verified runtime fixture").expect("runtime presence");
        let self_test_report = OmniRuntimeSelfTestReport {
            schema_version: 2,
            runtime_compatibility: manifest.runtime_compatibility.clone(),
            manifest_digest: manifest.manifest_digest.clone(),
            model_version: manifest.version.clone(),
            predicted_model_peak_bytes: 9 * GIB,
            upstream_runtime_revision: manifest.upstream_runtime_revision.clone(),
            patch_set_digest: manifest.patch_set_digest.clone(),
            build_profile: "production-cuda".to_owned(),
            cuda_compiled: true,
            backend_ready: true,
            model_probe: "ready".to_owned(),
        };
        let mut verified = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest.clone(),
            Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
            Box::new(FakeSelfTest {
                result: Ok(self_test_report),
            }),
        )
        .expect("verified service");
        verified.run_self_test(&state).expect("self-test");
        drop(verified);

        let mut reopened = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest,
            Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
            Box::new(FakeSelfTest {
                result: Err("must not run during readiness"),
            }),
        )
        .expect("reopened service");
        let report = reopened
            .report(&state, RealtimeActivityProfile::Focus, false)
            .expect("restored report");

        assert_eq!(report.runtime, OmniRuntimeReadiness::Passed);
        assert!(report.capability.local_beta_eligible);
    }

    #[test]
    fn changed_runtime_or_model_layout_invalidates_persisted_evidence() {
        for change_runtime in [true, false] {
            let models = tempfile::tempdir().expect("models");
            let runtime = tempfile::tempdir().expect("runtime");
            let manifest = bundled_minicpm_o45_manifest().expect("manifest");
            let state = write_shallow_model(models.path(), &manifest);
            let runtime_path = write_runtime(runtime.path(), b"verified runtime fixture");
            persist_valid_attestation(models.path(), runtime.path(), &manifest, &state);

            if change_runtime {
                fs::write(runtime_path, b"changed runtime").expect("changed runtime");
            } else {
                let version = OmniModelStore::new(models.path())
                    .expect("store")
                    .version_dir(&manifest)
                    .expect("version");
                let first = &manifest.files[0];
                fs::write(
                    version.join(first.path.replace('/', std::path::MAIN_SEPARATOR_STR)),
                    b"changed model layout",
                )
                .expect("changed model");
            }

            let mut reopened = LocalReadinessService::new(
                models.path(),
                runtime.path(),
                manifest,
                Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
                Box::new(FakeSelfTest {
                    result: Err("must not run during readiness"),
                }),
            )
            .expect("reopened service");
            let report = reopened
                .report(&state, RealtimeActivityProfile::Focus, false)
                .expect("invalidated report");

            assert_eq!(report.runtime, OmniRuntimeReadiness::NotTested);
            assert!(!report.capability.local_beta_eligible);
        }
    }

    #[test]
    fn changed_adapter_or_driver_invalidates_persisted_evidence() {
        for change_driver in [true, false] {
            let models = tempfile::tempdir().expect("models");
            let runtime = tempfile::tempdir().expect("runtime");
            let manifest = bundled_minicpm_o45_manifest().expect("manifest");
            let state = write_shallow_model(models.path(), &manifest);
            write_runtime(runtime.path(), b"verified runtime fixture");
            persist_valid_attestation(models.path(), runtime.path(), &manifest, &state);
            let mut hardware = eligible_hardware_report();
            if change_driver {
                hardware.cuda.driver_api_version = Some(12_090);
            } else {
                hardware.adapter.as_mut().expect("adapter").luid = "00000000:00000002".to_owned();
            }

            let mut reopened = LocalReadinessService::new(
                models.path(),
                runtime.path(),
                manifest,
                Box::new(FixedProbe(hardware)),
                Box::new(FakeSelfTest {
                    result: Err("must not run during readiness"),
                }),
            )
            .expect("reopened service");
            let report = reopened
                .report(&state, RealtimeActivityProfile::Focus, false)
                .expect("invalidated report");

            assert_eq!(report.runtime, OmniRuntimeReadiness::NotTested);
            assert!(!report.capability.local_beta_eligible);
        }
    }

    #[test]
    fn malformed_or_unknown_attestation_fails_closed() {
        let models = tempfile::tempdir().expect("models");
        let runtime = tempfile::tempdir().expect("runtime");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let state = write_shallow_model(models.path(), &manifest);
        write_runtime(runtime.path(), b"verified runtime fixture");
        let store = OmniModelStore::new(models.path()).expect("store");
        fs::write(
            store.root().join(ATTESTATION_FILE_NAME),
            br#"{"schema_version":999,"unexpected":"field"}"#,
        )
        .expect("malformed attestation");

        let mut service = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest,
            Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
            Box::new(FakeSelfTest {
                result: Err("must not run during readiness"),
            }),
        )
        .expect("service");
        let report = service
            .report(&state, RealtimeActivityProfile::Focus, false)
            .expect("report");

        assert_eq!(report.runtime, OmniRuntimeReadiness::NotTested);
        assert!(!report.capability.local_beta_eligible);
    }

    #[test]
    fn failed_self_test_remains_a_specific_fail_closed_reason() {
        let models = tempfile::tempdir().expect("models");
        let runtime = tempfile::tempdir().expect("runtime");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let state = write_shallow_model(models.path(), &manifest);
        let runtime_path = runtime.path().join("omni").join("fairy-omni-runtime.exe");
        fs::create_dir_all(runtime_path.parent().expect("runtime parent")).expect("runtime dir");
        fs::write(runtime_path, b"fixture").expect("runtime presence");
        let mut service = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest,
            Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
            Box::new(FakeSelfTest {
                result: Err("crash"),
            }),
        )
        .expect("service");

        assert!(matches!(
            service.run_self_test(&state),
            Err(LocalReadinessError::Runtime(
                OmniRuntimeSelfTestError::Crash
            ))
        ));
        let report = service
            .report(&state, RealtimeActivityProfile::Game, false)
            .expect("report");
        assert_eq!(report.runtime, OmniRuntimeReadiness::Failed);
        assert_eq!(
            report.runtime_error_code.as_deref(),
            Some("OMNI_SELF_TEST_CRASHED")
        );
        assert_eq!(
            report.capability.reason,
            LocalBetaReadinessReason::SelfTestFailed
        );
    }

    #[test]
    fn quarantine_requires_explicit_verify_before_readiness_can_recover() {
        let models = tempfile::tempdir().expect("models");
        let runtime = tempfile::tempdir().expect("runtime");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let state = write_shallow_model(models.path(), &manifest);
        let runtime_path = runtime.path().join("omni").join("fairy-omni-runtime.exe");
        fs::create_dir_all(runtime_path.parent().expect("runtime parent")).expect("runtime dir");
        fs::write(&runtime_path, b"fixture").expect("runtime presence");
        let self_test_report = OmniRuntimeSelfTestReport {
            schema_version: 2,
            runtime_compatibility: manifest.runtime_compatibility.clone(),
            manifest_digest: manifest.manifest_digest.clone(),
            model_version: manifest.version.clone(),
            predicted_model_peak_bytes: 9 * GIB,
            upstream_runtime_revision: manifest.upstream_runtime_revision.clone(),
            patch_set_digest: manifest.patch_set_digest.clone(),
            build_profile: "production-cuda".to_owned(),
            cuda_compiled: true,
            backend_ready: true,
            model_probe: "ready".to_owned(),
        };
        let mut service = LocalReadinessService::new(
            models.path(),
            runtime.path(),
            manifest,
            Box::new(CountingProbe(Arc::new(AtomicUsize::new(0)))),
            Box::new(FakeSelfTest {
                result: Ok(self_test_report),
            }),
        )
        .expect("service");

        service.mark_runtime_quarantined();
        let quarantined = service
            .report(&state, RealtimeActivityProfile::Focus, false)
            .expect("quarantined report");
        assert_eq!(
            quarantined.capability.reason,
            LocalBetaReadinessReason::RuntimeQuarantined
        );
        assert!(!quarantined.capability.local_beta_eligible);

        service.run_self_test(&state).expect("explicit verify");
        let recovered = service
            .report(&state, RealtimeActivityProfile::Focus, false)
            .expect("recovered report");
        assert_eq!(recovered.runtime, OmniRuntimeReadiness::Passed);
        assert!(recovered.capability.local_beta_eligible);
    }
}
