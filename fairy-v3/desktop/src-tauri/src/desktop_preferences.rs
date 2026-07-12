use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

const PREFERENCES_FILE: &str = "preferences/desktop.json";
const SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ThemePreference {
    System,
    Dark,
    Light,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct DesktopPreferences {
    pub schema_version: u32,
    pub revision: u64,
    pub language: String,
    pub launch_at_startup: bool,
    pub minimize_to_tray: bool,
    pub theme: ThemePreference,
    pub reduced_motion: bool,
    pub compact_density: bool,
    pub selected_profile_id: Option<String>,
    pub voice_auto_play_chat: bool,
    pub voice_auto_play_pet: bool,
    pub voice_volume_percent: u8,
    pub voice_rate_percent: u8,
    pub permission_cloud_profile: String,
    pub memory_enabled: bool,
    pub memory_retention_days: u16,
    pub analytics_enabled: bool,
    pub pet_enabled: bool,
    pub pet_always_on_top: bool,
    pub pet_muted: bool,
    pub developer_mode: bool,
}

impl Default for DesktopPreferences {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            revision: 0,
            language: "system".to_owned(),
            launch_at_startup: false,
            minimize_to_tray: true,
            theme: ThemePreference::System,
            reduced_motion: false,
            compact_density: false,
            selected_profile_id: None,
            voice_auto_play_chat: false,
            voice_auto_play_pet: true,
            voice_volume_percent: 80,
            voice_rate_percent: 100,
            permission_cloud_profile: "standard".to_owned(),
            memory_enabled: true,
            memory_retention_days: 90,
            analytics_enabled: false,
            pet_enabled: true,
            pet_always_on_top: true,
            pet_muted: false,
            developer_mode: false,
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct DesktopPreferencesUpdate {
    pub expected_revision: u64,
    pub preferences: DesktopPreferences,
}

#[derive(Debug, Deserialize)]
pub struct PetPreferencesUpdate {
    pub expected_revision: u64,
    pub voice_auto_play_pet: Option<bool>,
    pub pet_muted: Option<bool>,
    pub pet_always_on_top: Option<bool>,
}

#[derive(Debug, Error)]
pub enum DesktopPreferencesError {
    #[error("desktop preferences revision conflict")]
    RevisionConflict,
    #[error("desktop preferences are invalid: {0}")]
    Invalid(String),
    #[error("desktop preferences storage failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("desktop preferences serialization failed: {0}")]
    Json(#[from] serde_json::Error),
}

pub struct DesktopPreferencesStore {
    path: PathBuf,
}

impl DesktopPreferencesStore {
    pub fn new(data_dir: &Path) -> Self {
        Self {
            path: data_dir.join(PREFERENCES_FILE),
        }
    }

    pub fn load(&self) -> Result<DesktopPreferences, DesktopPreferencesError> {
        if !self.path.exists() {
            return Ok(DesktopPreferences::default());
        }
        let bytes = fs::read(&self.path)?;
        let preferences: DesktopPreferences = serde_json::from_slice(&bytes)?;
        validate(&preferences)?;
        Ok(preferences)
    }

    pub fn update(
        &self,
        update: DesktopPreferencesUpdate,
    ) -> Result<DesktopPreferences, DesktopPreferencesError> {
        let current = self.load()?;
        if current.revision != update.expected_revision {
            return Err(DesktopPreferencesError::RevisionConflict);
        }
        let mut next = update.preferences;
        next.schema_version = SCHEMA_VERSION;
        next.revision = current.revision + 1;
        validate(&next)?;
        self.write_atomic(&next)?;
        Ok(next)
    }

    pub fn update_pet(
        &self,
        update: PetPreferencesUpdate,
    ) -> Result<DesktopPreferences, DesktopPreferencesError> {
        let current = self.load()?;
        if current.revision != update.expected_revision {
            return Err(DesktopPreferencesError::RevisionConflict);
        }
        let mut next = current;
        if let Some(value) = update.voice_auto_play_pet {
            next.voice_auto_play_pet = value;
        }
        if let Some(value) = update.pet_muted {
            next.pet_muted = value;
        }
        if let Some(value) = update.pet_always_on_top {
            next.pet_always_on_top = value;
        }
        next.revision += 1;
        validate(&next)?;
        self.write_atomic(&next)?;
        Ok(next)
    }

    fn write_atomic(
        &self,
        preferences: &DesktopPreferences,
    ) -> Result<(), DesktopPreferencesError> {
        let parent = self.path.parent().ok_or_else(|| {
            DesktopPreferencesError::Io(std::io::Error::other(
                "desktop preferences path has no parent",
            ))
        })?;
        fs::create_dir_all(parent)?;
        let temporary = self
            .path
            .with_extension(format!("tmp-{}", std::process::id()));
        let bytes = serde_json::to_vec_pretty(preferences)?;
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        replace_file(&temporary, &self.path)?;
        if let Ok(directory) = fs::File::open(parent) {
            let _ = directory.sync_all();
        }
        Ok(())
    }
}

fn validate(preferences: &DesktopPreferences) -> Result<(), DesktopPreferencesError> {
    if preferences.schema_version != SCHEMA_VERSION {
        return Err(DesktopPreferencesError::Invalid(
            "unsupported schema version".to_owned(),
        ));
    }
    if !["system", "en", "zh-CN"].contains(&preferences.language.as_str()) {
        return Err(DesktopPreferencesError::Invalid(
            "unsupported language".to_owned(),
        ));
    }
    if !(0..=100).contains(&preferences.voice_volume_percent) {
        return Err(DesktopPreferencesError::Invalid(
            "voice volume must be between 0 and 100".to_owned(),
        ));
    }
    if !(50..=200).contains(&preferences.voice_rate_percent) {
        return Err(DesktopPreferencesError::Invalid(
            "voice rate must be between 50 and 200".to_owned(),
        ));
    }
    if !(1..=3650).contains(&preferences.memory_retention_days) {
        return Err(DesktopPreferencesError::Invalid(
            "memory retention must be between 1 and 3650 days".to_owned(),
        ));
    }
    if !["observe", "standard", "autonomous"]
        .contains(&preferences.permission_cloud_profile.as_str())
    {
        return Err(DesktopPreferencesError::Invalid(
            "unsupported cloud permission profile".to_owned(),
        ));
    }
    Ok(())
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
    use super::{
        DesktopPreferences, DesktopPreferencesError, DesktopPreferencesStore,
        DesktopPreferencesUpdate, PetPreferencesUpdate,
    };

    #[test]
    fn preferences_are_revision_fenced_and_replace_the_previous_file() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let initial = store.load().expect("default preferences");
        assert_eq!(initial, DesktopPreferences::default());

        let mut changed = initial.clone();
        changed.developer_mode = true;
        let saved = store
            .update(DesktopPreferencesUpdate {
                expected_revision: 0,
                preferences: changed,
            })
            .expect("save preferences");
        assert_eq!(saved.revision, 1);
        assert!(saved.developer_mode);
        assert_eq!(store.load().expect("reload preferences"), saved);

        let conflict = store.update(DesktopPreferencesUpdate {
            expected_revision: 0,
            preferences: initial,
        });
        assert!(matches!(
            conflict,
            Err(DesktopPreferencesError::RevisionConflict)
        ));
    }

    #[test]
    fn pet_update_can_only_change_companion_preferences() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let saved = store
            .update_pet(PetPreferencesUpdate {
                expected_revision: 0,
                voice_auto_play_pet: Some(false),
                pet_muted: Some(true),
                pet_always_on_top: Some(false),
            })
            .expect("save pet preferences");

        assert_eq!(saved.revision, 1);
        assert!(!saved.voice_auto_play_pet);
        assert!(saved.pet_muted);
        assert!(!saved.pet_always_on_top);
        assert!(!saved.developer_mode);
        assert_eq!(saved.permission_cloud_profile, "standard");
    }
}
