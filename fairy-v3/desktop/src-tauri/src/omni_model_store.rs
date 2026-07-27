use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::omni_model_manifest::{OmniModelFile, OmniModelManifest};

const MODEL_DIRECTORY: &str = "minicpm-o-4.5";
const STATE_FILE: &str = "install-state.json";

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OmniModelInstallPhase {
    NotInstalled,
    CheckingSpace,
    Downloading,
    Cancelling,
    Partial,
    Verifying,
    LayoutCheck,
    RuntimeSelfTest,
    Ready,
    Corrupt,
    RuntimeMissing,
    SelfTestFailed,
}

impl OmniModelInstallPhase {
    pub fn is_active(self) -> bool {
        matches!(
            self,
            Self::CheckingSpace
                | Self::Downloading
                | Self::Cancelling
                | Self::Verifying
                | Self::LayoutCheck
                | Self::RuntimeSelfTest
        )
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OmniModelInstallState {
    pub schema_version: u16,
    pub sequence: u64,
    pub phase: OmniModelInstallPhase,
    pub model_version: String,
    pub manifest_digest: String,
    pub current_file: Option<String>,
    pub received_bytes: u64,
    pub total_bytes: u64,
    pub error_code: Option<String>,
}

impl OmniModelInstallState {
    pub fn initial(manifest: &OmniModelManifest) -> Self {
        Self {
            schema_version: 1,
            sequence: 0,
            phase: OmniModelInstallPhase::NotInstalled,
            model_version: manifest.version.clone(),
            manifest_digest: manifest.manifest_digest.clone(),
            current_file: None,
            received_bytes: 0,
            total_bytes: manifest.total_size(),
            error_code: None,
        }
    }
}

#[derive(Debug, Error)]
pub enum OmniModelStoreError {
    #[error("the Omni model path is outside the managed store")]
    UnsafePath,
    #[error("the Omni model store contains a symbolic link")]
    SymbolicLink,
    #[error("the Omni model state is invalid")]
    InvalidState,
    #[error("the Omni model store failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("the Omni model state could not be serialized: {0}")]
    Json(#[from] serde_json::Error),
}

#[derive(Clone, Debug)]
pub struct OmniModelStore {
    root: PathBuf,
}

impl OmniModelStore {
    pub fn new(models_root: &Path) -> Result<Self, OmniModelStoreError> {
        let root = models_root.join(MODEL_DIRECTORY);
        fs::create_dir_all(&root)?;
        reject_symlink(&root)?;
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn version_dir(
        &self,
        manifest: &OmniModelManifest,
    ) -> Result<PathBuf, OmniModelStoreError> {
        self.managed_join(Path::new(&manifest.version))
    }

    pub fn staging_dir(
        &self,
        manifest: &OmniModelManifest,
    ) -> Result<PathBuf, OmniModelStoreError> {
        self.managed_join(Path::new(&format!(".staging-{}", manifest.version)))
    }

    pub fn state_path(&self) -> PathBuf {
        self.root.join(STATE_FILE)
    }

    pub fn load_state(
        &self,
        manifest: &OmniModelManifest,
    ) -> Result<OmniModelInstallState, OmniModelStoreError> {
        let path = self.state_path();
        if !path.exists() {
            return Ok(OmniModelInstallState::initial(manifest));
        }
        reject_symlink(&path)?;
        let state: OmniModelInstallState = serde_json::from_slice(&fs::read(path)?)?;
        if state.schema_version != 1
            || state.model_version != manifest.version
            || state.manifest_digest != manifest.manifest_digest
            || state.total_bytes != manifest.total_size()
            || state.received_bytes > state.total_bytes
            || state
                .current_file
                .as_ref()
                .is_some_and(|current| !manifest.files.iter().any(|file| &file.path == current))
        {
            return Err(OmniModelStoreError::InvalidState);
        }
        Ok(state)
    }

    pub fn write_state(&self, state: &OmniModelInstallState) -> Result<(), OmniModelStoreError> {
        if state.schema_version != 1 {
            return Err(OmniModelStoreError::InvalidState);
        }
        let path = self.state_path();
        self.ensure_managed_path(&path)?;
        let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
        let bytes = serde_json::to_vec_pretty(state)?;
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        replace_file(&temporary, &path)?;
        sync_directory(&self.root);
        Ok(())
    }

    pub fn prepare_staging(
        &self,
        manifest: &OmniModelManifest,
    ) -> Result<PathBuf, OmniModelStoreError> {
        let staging = self.staging_dir(manifest)?;
        self.ensure_managed_path(&staging)?;
        fs::create_dir_all(&staging)?;
        reject_symlink(&staging)?;
        Ok(staging)
    }

    pub fn staging_artifact_path(
        &self,
        manifest: &OmniModelManifest,
        file: &OmniModelFile,
    ) -> Result<PathBuf, OmniModelStoreError> {
        let staging = self.staging_dir(manifest)?;
        let path = self.artifact_path(&staging, file)?;
        if let Some(directory) = path.parent() {
            fs::create_dir_all(directory)?;
            reject_symlink_tree(&self.root, directory)?;
        }
        Ok(path)
    }

    pub fn installed_artifact_path(
        &self,
        manifest: &OmniModelManifest,
        file: &OmniModelFile,
    ) -> Result<PathBuf, OmniModelStoreError> {
        let version = self.version_dir(manifest)?;
        self.artifact_path(&version, file)
    }

    pub fn staging_partial_path(
        &self,
        manifest: &OmniModelManifest,
        file: &OmniModelFile,
    ) -> Result<PathBuf, OmniModelStoreError> {
        let artifact = self.staging_artifact_path(manifest, file)?;
        Ok(partial_path(&artifact))
    }

    pub fn finalize_partial(
        &self,
        manifest: &OmniModelManifest,
        file: &OmniModelFile,
    ) -> Result<PathBuf, OmniModelStoreError> {
        let artifact = self.staging_artifact_path(manifest, file)?;
        let partial = partial_path(&artifact);
        self.ensure_managed_path(&partial)?;
        reject_symlink(&partial)?;
        if artifact.exists() {
            reject_symlink(&artifact)?;
        }
        replace_file(&partial, &artifact)?;
        if let Some(parent) = artifact.parent() {
            sync_directory(parent);
        }
        Ok(artifact)
    }

    pub fn write_staged_manifest(
        &self,
        manifest: &OmniModelManifest,
    ) -> Result<(), OmniModelStoreError> {
        let staging = self.prepare_staging(manifest)?;
        let path = staging.join("manifest.json");
        self.ensure_managed_path(&path)?;
        let mut file = fs::File::create(path)?;
        file.write_all(&serde_json::to_vec_pretty(manifest)?)?;
        file.sync_all()?;
        Ok(())
    }

    pub fn promote(&self, manifest: &OmniModelManifest) -> Result<PathBuf, OmniModelStoreError> {
        let staging = self.staging_dir(manifest)?;
        let version = self.version_dir(manifest)?;
        self.ensure_managed_path(&staging)?;
        self.ensure_managed_path(&version)?;
        reject_symlink_tree(&self.root, &staging)?;
        let backup = self.managed_join(Path::new(&format!(".previous-{}", manifest.version)))?;
        if backup.exists() {
            return Err(OmniModelStoreError::InvalidState);
        }
        if version.exists() {
            reject_symlink_tree(&self.root, &version)?;
            fs::rename(&version, &backup)?;
        }
        if let Err(error) = fs::rename(&staging, &version) {
            if backup.exists() {
                let _ = fs::rename(&backup, &version);
            }
            return Err(error.into());
        }
        if backup.exists() {
            fs::remove_dir_all(backup)?;
        }
        sync_directory(&self.root);
        Ok(version)
    }

    pub fn remove_version(&self, manifest: &OmniModelManifest) -> Result<(), OmniModelStoreError> {
        let version = self.version_dir(manifest)?;
        self.ensure_managed_path(&version)?;
        if version.exists() {
            reject_symlink_tree(&self.root, &version)?;
            fs::remove_dir_all(version)?;
            sync_directory(&self.root);
        }
        Ok(())
    }

    pub fn remove_staging(&self, manifest: &OmniModelManifest) -> Result<(), OmniModelStoreError> {
        let staging = self.staging_dir(manifest)?;
        self.ensure_managed_path(&staging)?;
        if staging.exists() {
            reject_symlink_tree(&self.root, &staging)?;
            fs::remove_dir_all(staging)?;
            sync_directory(&self.root);
        }
        Ok(())
    }

    pub fn validate_managed_tree(&self, path: &Path) -> Result<(), OmniModelStoreError> {
        self.ensure_managed_path(path)?;
        reject_symlink_tree(&self.root, path)
    }

    pub fn partial_bytes(&self, manifest: &OmniModelManifest) -> Result<u64, OmniModelStoreError> {
        let staging = self.staging_dir(manifest)?;
        manifest.files.iter().try_fold(0_u64, |total, file| {
            let path = partial_path(&self.artifact_path(&staging, file)?);
            let size = if path.exists() {
                reject_symlink(&path)?;
                let length = fs::metadata(path)?.len();
                if length <= file.size {
                    length
                } else {
                    0
                }
            } else {
                0
            };
            total
                .checked_add(size)
                .ok_or(OmniModelStoreError::InvalidState)
        })
    }

    fn artifact_path(
        &self,
        parent: &Path,
        file: &OmniModelFile,
    ) -> Result<PathBuf, OmniModelStoreError> {
        let path = parent.join(file.path.replace('/', std::path::MAIN_SEPARATOR_STR));
        self.ensure_managed_path(&path)?;
        Ok(path)
    }

    fn managed_join(&self, relative: &Path) -> Result<PathBuf, OmniModelStoreError> {
        let path = self.root.join(relative);
        self.ensure_managed_path(&path)?;
        Ok(path)
    }

    fn ensure_managed_path(&self, path: &Path) -> Result<(), OmniModelStoreError> {
        if path == self.root || path.starts_with(&self.root) {
            Ok(())
        } else {
            Err(OmniModelStoreError::UnsafePath)
        }
    }
}

fn partial_path(artifact: &Path) -> PathBuf {
    let mut name = artifact.as_os_str().to_os_string();
    name.push(".partial");
    PathBuf::from(name)
}

fn reject_symlink(path: &Path) -> Result<(), OmniModelStoreError> {
    if fs::symlink_metadata(path)?.file_type().is_symlink() {
        Err(OmniModelStoreError::SymbolicLink)
    } else {
        Ok(())
    }
}

fn reject_symlink_tree(root: &Path, path: &Path) -> Result<(), OmniModelStoreError> {
    if path != root && !path.starts_with(root) {
        return Err(OmniModelStoreError::UnsafePath);
    }
    let relative = path
        .strip_prefix(root)
        .map_err(|_| OmniModelStoreError::UnsafePath)?;
    let mut current = root.to_path_buf();
    reject_symlink(&current)?;
    for component in relative.components() {
        current.push(component);
        if current.exists() {
            reject_symlink(&current)?;
        }
    }
    Ok(())
}

fn sync_directory(path: &Path) {
    if let Ok(directory) = fs::File::open(path) {
        let _ = directory.sync_all();
    }
}

#[cfg(windows)]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
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

#[cfg(not(windows))]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    fs::rename(source, destination)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::omni_model_catalog::bundled_minicpm_o45_manifest;

    #[test]
    fn state_round_trips_without_absolute_paths() {
        let directory = tempfile::tempdir().expect("temporary models root");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let store = OmniModelStore::new(directory.path()).expect("store");
        let mut state = OmniModelInstallState::initial(&manifest);
        state.sequence = 1;
        state.phase = OmniModelInstallPhase::Partial;
        state.current_file = Some(manifest.files[0].path.clone());
        state.received_bytes = 128;
        store.write_state(&state).expect("write state");

        assert_eq!(store.load_state(&manifest).expect("load state"), state);
        let serialized = fs::read_to_string(store.state_path()).expect("state text");
        assert!(!serialized.contains(directory.path().to_string_lossy().as_ref()));
    }

    #[test]
    fn managed_paths_remain_under_the_model_root() {
        let directory = tempfile::tempdir().expect("temporary models root");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let store = OmniModelStore::new(directory.path()).expect("store");
        let staging = store.prepare_staging(&manifest).expect("staging");
        for file in &manifest.files {
            assert!(store
                .staging_artifact_path(&manifest, file)
                .expect("artifact")
                .starts_with(&staging));
        }
    }

    #[cfg(windows)]
    #[test]
    fn symbolic_links_inside_the_store_are_rejected() {
        use std::os::windows::fs::symlink_dir;

        let directory = tempfile::tempdir().expect("temporary models root");
        let outside = tempfile::tempdir().expect("outside");
        let manifest = bundled_minicpm_o45_manifest().expect("manifest");
        let store = OmniModelStore::new(directory.path()).expect("store");
        let staging = store.staging_dir(&manifest).expect("staging");
        fs::create_dir_all(&staging).expect("create staging");
        let vision = staging.join("vision");
        symlink_dir(outside.path(), &vision).expect("create directory link");

        assert!(matches!(
            store.staging_artifact_path(&manifest, &manifest.files[1]),
            Err(OmniModelStoreError::SymbolicLink)
        ));
    }
}
