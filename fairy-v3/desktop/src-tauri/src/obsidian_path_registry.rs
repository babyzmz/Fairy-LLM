use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

const REGISTRY_SCHEMA_VERSION: u16 = 1;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ObsidianVaultSelection {
    pub local_path_token: String,
    pub display_name: String,
    pub available_directories: Vec<String>,
}

#[derive(Debug, Error)]
pub enum ObsidianPathRegistryError {
    #[error("Vault must be a real local directory")]
    InvalidVault,
    #[error("Vault path registry is invalid")]
    InvalidRegistry,
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

#[derive(Clone, Debug)]
pub struct ObsidianPathRegistry {
    path: PathBuf,
}

#[derive(Debug, Deserialize, Serialize)]
struct RegistryFile {
    schema_version: u16,
    paths: BTreeMap<String, PathRecord>,
}

#[derive(Debug, Deserialize, Serialize)]
struct PathRecord {
    path: String,
    display_name: String,
    created_at: String,
}

impl ObsidianPathRegistry {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    pub fn register(
        &self,
        selected: &Path,
    ) -> Result<ObsidianVaultSelection, ObsidianPathRegistryError> {
        let canonical = selected
            .canonicalize()
            .map_err(|_| ObsidianPathRegistryError::InvalidVault)?;
        if !canonical.is_dir() || is_remote_or_reparse(&canonical)? {
            return Err(ObsidianPathRegistryError::InvalidVault);
        }
        let display_name = canonical
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
            .unwrap_or("Obsidian Vault")
            .to_owned();
        let mut registry = self.load()?;
        let token = Uuid::new_v4().to_string();
        registry.paths.insert(
            token.clone(),
            PathRecord {
                path: canonical.to_string_lossy().into_owned(),
                display_name: display_name.clone(),
                created_at: unix_timestamp().to_string(),
            },
        );
        self.save(&registry)?;
        Ok(ObsidianVaultSelection {
            local_path_token: token,
            display_name,
            available_directories: top_level_directories(&canonical),
        })
    }

    fn load(&self) -> Result<RegistryFile, ObsidianPathRegistryError> {
        if !self.path.exists() {
            return Ok(RegistryFile {
                schema_version: REGISTRY_SCHEMA_VERSION,
                paths: BTreeMap::new(),
            });
        }
        let registry: RegistryFile = serde_json::from_slice(&fs::read(&self.path)?)?;
        if registry.schema_version != REGISTRY_SCHEMA_VERSION {
            return Err(ObsidianPathRegistryError::InvalidRegistry);
        }
        Ok(registry)
    }

    fn save(&self, registry: &RegistryFile) -> Result<(), ObsidianPathRegistryError> {
        let parent = self
            .path
            .parent()
            .ok_or(ObsidianPathRegistryError::InvalidRegistry)?;
        fs::create_dir_all(parent)?;
        let temporary = parent.join(format!(
            ".{}.{}.tmp",
            self.path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("obsidian-paths"),
            Uuid::new_v4()
        ));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(&serde_json::to_vec(registry)?)?;
        file.sync_all()?;
        replace_file(&temporary, &self.path)?;
        Ok(())
    }
}

fn top_level_directories(vault: &Path) -> Vec<String> {
    let Ok(entries) = fs::read_dir(vault) else {
        return Vec::new();
    };
    let mut directories = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            let file_type = entry.file_type().ok()?;
            if name.starts_with('.') || !file_type.is_dir() || file_type.is_symlink() {
                return None;
            }
            if is_reparse(&entry.path()).ok()? {
                return None;
            }
            Some(name)
        })
        .collect::<Vec<_>>();
    directories.sort_by_key(|value| value.to_lowercase());
    directories
}

fn is_remote_or_reparse(path: &Path) -> Result<bool, std::io::Error> {
    Ok(is_remote_path(path) || is_reparse(path)?)
}

#[cfg(target_os = "windows")]
fn is_remote_path(path: &Path) -> bool {
    use std::path::{Component, Prefix};

    matches!(
        path.components().next(),
        Some(Component::Prefix(prefix))
            if matches!(
                prefix.kind(),
                Prefix::UNC(_, _)
                    | Prefix::VerbatimUNC(_, _)
                    | Prefix::DeviceNS(_)
                    | Prefix::Verbatim(_)
            )
    )
}

#[cfg(not(target_os = "windows"))]
fn is_remote_path(_path: &Path) -> bool {
    false
}

#[cfg(target_os = "windows")]
fn is_reparse(path: &Path) -> Result<bool, std::io::Error> {
    use std::os::windows::fs::MetadataExt;
    use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;

    Ok(fs::symlink_metadata(path)?.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0)
}

#[cfg(not(target_os = "windows"))]
fn is_reparse(path: &Path) -> Result<bool, std::io::Error> {
    Ok(fs::symlink_metadata(path)?.file_type().is_symlink())
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
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let moved = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if moved == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    fs::rename(source, destination)
}

fn unix_timestamp() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registration_persists_only_in_the_device_registry() {
        let temporary = tempfile::tempdir().expect("temporary directory");
        let vault = temporary.path().join("Project Vault");
        fs::create_dir_all(vault.join("Notes")).expect("vault directories");
        fs::create_dir_all(vault.join(".obsidian")).expect("hidden directory");
        let registry = ObsidianPathRegistry::new(temporary.path().join("obsidian-paths.json"));

        let selection = registry.register(&vault).expect("register Vault");

        assert_eq!(selection.display_name, "Project Vault");
        assert_eq!(selection.available_directories, vec!["Notes"]);
        assert!(!selection.local_path_token.contains("Project"));
        let persisted = fs::read_to_string(temporary.path().join("obsidian-paths.json"))
            .expect("registry contents");
        assert!(persisted.contains("Project Vault"));
        assert!(persisted.contains(&selection.local_path_token));
    }
}
