use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

const PREFERENCES_FILE: &str = "preferences/desktop.json";
const SCHEMA_VERSION: u32 = 8;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ThemePreference {
    System,
    Dark,
    Light,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PetRendererMode {
    #[default]
    Auto,
    Liquid,
    Compatibility,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PetOpticsMode {
    #[default]
    Standard,
    Enhanced,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PetActivationStyle {
    Classic,
    #[default]
    #[serde(alias = "fluid_strands")]
    FluidResponse,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeProviderPreference {
    #[default]
    Auto,
    GeminiLive,
    GlmRealtimeFlash,
    GlmRealtimeAir,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeVoicePreference {
    #[default]
    Native,
    Fairy,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PetAnchorPreference {
    pub monitor_id: String,
    pub x_ratio: f64,
    pub y_ratio: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
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
    pub analytics_enabled: bool,
    #[serde(default)]
    pub realtime_provider: RealtimeProviderPreference,
    #[serde(default)]
    pub realtime_voice_mode: RealtimeVoicePreference,
    #[serde(default)]
    pub realtime_game_audio_default: bool,
    #[serde(default = "default_true")]
    pub realtime_memory_enabled: bool,
    #[serde(default = "default_realtime_session_minutes")]
    pub realtime_max_session_minutes: u8,
    #[serde(default)]
    pub trash_auto_purge_30_days: bool,
    pub pet_enabled: bool,
    pub pet_always_on_top: bool,
    pub pet_muted: bool,
    #[serde(default = "default_pet_size_percent")]
    pub pet_size_percent: u8,
    #[serde(default = "default_pet_opacity_percent")]
    pub pet_opacity_percent: u8,
    #[serde(default = "default_true")]
    pub pet_motion_enabled: bool,
    #[serde(default = "default_true")]
    pub pet_particles_enabled: bool,
    #[serde(default = "default_true")]
    pub pet_hover_enabled: bool,
    #[serde(default = "default_pet_hover_dwell_ms")]
    pub pet_hover_dwell_ms: u16,
    #[serde(default)]
    pub pet_do_not_disturb: bool,
    #[serde(default = "default_true")]
    pub ambient_dialogue_enabled: bool,
    #[serde(default)]
    pub ambient_dialogue_voice_enabled: bool,
    #[serde(default)]
    pub ambient_generated_dialogue_enabled: bool,
    #[serde(default = "default_true")]
    pub pet_remember_position: bool,
    #[serde(default)]
    pub pet_renderer_mode: PetRendererMode,
    #[serde(default)]
    pub pet_optics_mode: PetOpticsMode,
    #[serde(default)]
    pub pet_activation_style: PetActivationStyle,
    #[serde(default = "default_pet_target_fps")]
    pub pet_target_fps: u16,
    #[serde(default)]
    pub pet_anchor: Option<PetAnchorPreference>,
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
            analytics_enabled: false,
            realtime_provider: RealtimeProviderPreference::Auto,
            realtime_voice_mode: RealtimeVoicePreference::Native,
            realtime_game_audio_default: false,
            realtime_memory_enabled: true,
            realtime_max_session_minutes: default_realtime_session_minutes(),
            trash_auto_purge_30_days: false,
            pet_enabled: true,
            pet_always_on_top: true,
            pet_muted: false,
            pet_size_percent: default_pet_size_percent(),
            pet_opacity_percent: default_pet_opacity_percent(),
            pet_motion_enabled: true,
            pet_particles_enabled: true,
            pet_hover_enabled: true,
            pet_hover_dwell_ms: default_pet_hover_dwell_ms(),
            pet_do_not_disturb: false,
            ambient_dialogue_enabled: true,
            ambient_dialogue_voice_enabled: false,
            ambient_generated_dialogue_enabled: false,
            pet_remember_position: true,
            pet_renderer_mode: PetRendererMode::Auto,
            pet_optics_mode: PetOpticsMode::Standard,
            pet_activation_style: PetActivationStyle::FluidResponse,
            pet_target_fps: default_pet_target_fps(),
            pet_anchor: None,
            developer_mode: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LegacyMemorySettings {
    pub enabled: bool,
    pub retention_days: u16,
}

pub struct StartupDesktopPreferences {
    pub preferences: DesktopPreferences,
    pub legacy_memory: Option<LegacyMemorySettings>,
    pub migration_required: bool,
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
    pub pet_anchor: Option<PetAnchorPreference>,
    #[serde(default)]
    pub clear_pet_anchor: bool,
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
        let startup = self.load_for_startup()?;
        if startup.migration_required {
            self.write_atomic(&startup.preferences)?;
        }
        Ok(startup.preferences)
    }

    pub fn load_for_startup(&self) -> Result<StartupDesktopPreferences, DesktopPreferencesError> {
        if !self.path.exists() {
            return Ok(StartupDesktopPreferences {
                preferences: DesktopPreferences::default(),
                legacy_memory: None,
                migration_required: false,
            });
        }
        let bytes = fs::read(&self.path)?;
        let value = serde_json::from_slice::<serde_json::Value>(&bytes)?;
        let schema_version = value
            .get("schema_version")
            .and_then(serde_json::Value::as_u64);
        let legacy_memory_schema = matches!(schema_version, Some(1..=6));
        let needs_schema_upgrade = matches!(schema_version, Some(1..=7));
        let retired_activation_style = value
            .get("pet_activation_style")
            .and_then(serde_json::Value::as_str)
            == Some("fluid_strands");
        let legacy_memory = legacy_memory_settings(&value, legacy_memory_schema);
        let mut preferences = match serde_json::from_slice::<DesktopPreferences>(&bytes) {
            Ok(preferences) => preferences,
            Err(_) if legacy_memory_schema => DesktopPreferences::default(),
            Err(error) => return Err(error.into()),
        };
        if needs_schema_upgrade {
            preferences.schema_version = SCHEMA_VERSION;
            if validate(&preferences).is_err() {
                preferences = DesktopPreferences::default();
            }
            return Ok(StartupDesktopPreferences {
                preferences,
                legacy_memory,
                migration_required: true,
            });
        }
        validate(&preferences)?;
        Ok(StartupDesktopPreferences {
            preferences,
            legacy_memory: None,
            migration_required: retired_activation_style,
        })
    }

    pub fn complete_startup_migration(
        &self,
        preferences: &DesktopPreferences,
    ) -> Result<(), DesktopPreferencesError> {
        validate(preferences)?;
        self.write_atomic(preferences)
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
        if let Some(value) = update.pet_anchor {
            next.pet_anchor = Some(value);
        }
        if update.clear_pet_anchor {
            next.pet_anchor = None;
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
    if !(5..=120).contains(&preferences.realtime_max_session_minutes) {
        return Err(DesktopPreferencesError::Invalid(
            "realtime session length must be between 5 and 120 minutes".to_owned(),
        ));
    }
    if !(75..=150).contains(&preferences.pet_size_percent) {
        return Err(DesktopPreferencesError::Invalid(
            "pet size must be between 75 and 150 percent".to_owned(),
        ));
    }
    if !(40..=100).contains(&preferences.pet_opacity_percent) {
        return Err(DesktopPreferencesError::Invalid(
            "pet opacity must be between 40 and 100 percent".to_owned(),
        ));
    }
    if !(100..=1_000).contains(&preferences.pet_hover_dwell_ms) {
        return Err(DesktopPreferencesError::Invalid(
            "pet hover dwell must be between 100 and 1000 milliseconds".to_owned(),
        ));
    }
    if ![60, 144, 300].contains(&preferences.pet_target_fps) {
        return Err(DesktopPreferencesError::Invalid(
            "pet target fps must be 60, 144, or 300".to_owned(),
        ));
    }
    if let Some(anchor) = &preferences.pet_anchor {
        if anchor.monitor_id.is_empty() || anchor.monitor_id.len() > 200 {
            return Err(DesktopPreferencesError::Invalid(
                "pet anchor monitor is invalid".to_owned(),
            ));
        }
        if !(0.0..=1.0).contains(&anchor.x_ratio) || !(0.0..=1.0).contains(&anchor.y_ratio) {
            return Err(DesktopPreferencesError::Invalid(
                "pet anchor ratios must be between 0 and 1".to_owned(),
            ));
        }
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

fn legacy_memory_settings(
    value: &serde_json::Value,
    legacy_schema: bool,
) -> Option<LegacyMemorySettings> {
    if !legacy_schema {
        return None;
    }
    let enabled = value.get("memory_enabled")?.as_bool()?;
    let retention_days = u16::try_from(value.get("memory_retention_days")?.as_u64()?).ok()?;
    if !(1..=3_650).contains(&retention_days) {
        return None;
    }
    Some(LegacyMemorySettings {
        enabled,
        retention_days,
    })
}

const fn default_true() -> bool {
    true
}

const fn default_pet_size_percent() -> u8 {
    100
}

const fn default_pet_opacity_percent() -> u8 {
    92
}

const fn default_pet_hover_dwell_ms() -> u16 {
    250
}

const fn default_pet_target_fps() -> u16 {
    60
}

const fn default_realtime_session_minutes() -> u8 {
    30
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
        DesktopPreferencesUpdate, PetActivationStyle, PetAnchorPreference, PetOpticsMode,
        PetPreferencesUpdate,
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
                pet_anchor: Some(PetAnchorPreference {
                    monitor_id: "primary:0:0:1920:1040".to_owned(),
                    x_ratio: 0.8,
                    y_ratio: 0.7,
                }),
                clear_pet_anchor: false,
            })
            .expect("save pet preferences");

        assert_eq!(saved.revision, 1);
        assert!(!saved.voice_auto_play_pet);
        assert!(saved.pet_muted);
        assert!(!saved.pet_always_on_top);
        assert_eq!(
            saved.pet_anchor.as_ref().map(|anchor| anchor.x_ratio),
            Some(0.8)
        );
        assert!(!saved.developer_mode);
        assert_eq!(saved.permission_cloud_profile, "standard");
    }

    #[test]
    fn desktop_preferences_accept_only_the_supported_pet_frame_rates() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let mut preferences = DesktopPreferences {
            pet_target_fps: 300,
            ..DesktopPreferences::default()
        };
        let saved = store
            .update(DesktopPreferencesUpdate {
                expected_revision: 0,
                preferences: preferences.clone(),
            })
            .expect("save 300 FPS preference");
        assert_eq!(saved.pet_target_fps, 300);

        preferences.revision = saved.revision;
        preferences.pet_target_fps = 240;
        assert!(matches!(
            store.update(DesktopPreferencesUpdate {
                expected_revision: saved.revision,
                preferences,
            }),
            Err(DesktopPreferencesError::Invalid(_))
        ));
    }

    #[test]
    fn version_one_preferences_migrate_without_losing_existing_pet_values() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut legacy =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = legacy.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(1));
        object.insert("voice_auto_play_pet".to_owned(), serde_json::json!(false));
        object.insert("pet_always_on_top".to_owned(), serde_json::json!(false));
        object.insert("pet_muted".to_owned(), serde_json::json!(true));
        for field in [
            "pet_size_percent",
            "pet_opacity_percent",
            "pet_motion_enabled",
            "pet_particles_enabled",
            "pet_hover_enabled",
            "pet_hover_dwell_ms",
            "pet_do_not_disturb",
            "pet_remember_position",
            "pet_renderer_mode",
            "pet_optics_mode",
            "pet_activation_style",
            "pet_target_fps",
            "pet_anchor",
        ] {
            object.remove(field);
        }
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&legacy).expect("legacy bytes"),
        )
        .expect("write legacy preferences");

        let migrated = store.load().expect("migrate preferences");
        assert_eq!(migrated.schema_version, 8);
        assert!(!migrated.voice_auto_play_pet);
        assert!(!migrated.pet_always_on_top);
        assert!(migrated.pet_muted);
        assert_eq!(migrated.pet_size_percent, 100);
        assert_eq!(migrated.pet_renderer_mode, super::PetRendererMode::Auto);
        assert_eq!(
            migrated.pet_activation_style,
            PetActivationStyle::FluidResponse
        );
        assert_eq!(migrated.pet_target_fps, 60);
        assert_eq!(store.load().expect("reload migrated"), migrated);
    }

    #[test]
    fn version_two_preferences_add_the_default_target_frame_rate() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut legacy =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = legacy.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(2));
        object.remove("pet_target_fps");
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&legacy).expect("legacy bytes"),
        )
        .expect("write legacy preferences");

        let migrated = store.load().expect("migrate preferences");
        assert_eq!(migrated.schema_version, 8);
        assert_eq!(migrated.pet_target_fps, 60);
        assert_eq!(store.load().expect("reload migrated"), migrated);
    }

    #[test]
    fn version_three_preferences_default_to_privacy_safe_optics() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut legacy =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = legacy.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(3));
        object.remove("pet_optics_mode");
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&legacy).expect("legacy bytes"),
        )
        .expect("write legacy preferences");

        let migrated = store.load().expect("migrate preferences");
        assert_eq!(migrated.schema_version, 8);
        assert_eq!(migrated.pet_optics_mode, PetOpticsMode::Standard);
        assert_eq!(store.load().expect("reload migrated"), migrated);
    }

    #[test]
    fn existing_version_seven_preferences_default_to_fluid_response_with_classic_available() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut stored =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = stored.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(7));
        object.insert("pet_muted".to_owned(), serde_json::json!(true));
        object.remove("pet_activation_style");
        object.remove("ambient_dialogue_enabled");
        object.remove("ambient_dialogue_voice_enabled");
        object.remove("ambient_generated_dialogue_enabled");
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&stored).expect("stored bytes"),
        )
        .expect("write stored preferences");

        let loaded = store.load().expect("load version seven preferences");
        assert_eq!(loaded.schema_version, 8);
        assert!(loaded.pet_muted);
        assert!(loaded.ambient_dialogue_enabled);
        assert!(!loaded.ambient_dialogue_voice_enabled);
        assert!(!loaded.ambient_generated_dialogue_enabled);
        assert_eq!(
            loaded.pet_activation_style,
            PetActivationStyle::FluidResponse
        );

        let mut classic = loaded.clone();
        classic.pet_activation_style = PetActivationStyle::Classic;
        let saved = store
            .update(DesktopPreferencesUpdate {
                expected_revision: loaded.revision,
                preferences: classic,
            })
            .expect("save classic fallback");
        assert_eq!(saved.pet_activation_style, PetActivationStyle::Classic);
    }

    #[test]
    fn retired_fluid_strands_preference_is_rewritten_without_restoring_strands() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut stored =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        stored.as_object_mut().expect("preferences object").insert(
            "pet_activation_style".to_owned(),
            serde_json::json!("fluid_strands"),
        );
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&stored).expect("stored bytes"),
        )
        .expect("write retired preferences");

        let loaded = store.load().expect("migrate retired activation style");
        assert_eq!(
            loaded.pet_activation_style,
            PetActivationStyle::FluidResponse
        );
        let rewritten = std::fs::read_to_string(path).expect("read rewritten preferences");
        assert!(rewritten.contains("\"fluid_response\""));
        assert!(!rewritten.contains("fluid_strands"));
    }

    #[test]
    fn version_four_preferences_default_to_manual_trash_cleanup() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut legacy =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = legacy.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(4));
        object.remove("trash_auto_purge_30_days");
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&legacy).expect("legacy bytes"),
        )
        .expect("write legacy preferences");

        let migrated = store.load().expect("migrate preferences");
        assert_eq!(migrated.schema_version, 8);
        assert!(!migrated.trash_auto_purge_30_days);
        assert_eq!(store.load().expect("reload migrated"), migrated);
    }

    #[test]
    fn version_six_memory_preferences_are_exposed_once_for_core_migration() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut legacy =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = legacy.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(6));
        object.insert("memory_enabled".to_owned(), serde_json::json!(false));
        object.insert("memory_retention_days".to_owned(), serde_json::json!(45));
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&legacy).expect("legacy bytes"),
        )
        .expect("write legacy preferences");

        let startup = store.load_for_startup().expect("load startup preferences");
        assert!(startup.migration_required);
        assert_eq!(
            startup.legacy_memory,
            Some(super::LegacyMemorySettings {
                enabled: false,
                retention_days: 45,
            })
        );
        assert_eq!(startup.preferences.schema_version, 8);
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(
                &std::fs::read(&path).expect("read pending migration")
            )
            .expect("parse pending migration")["schema_version"],
            serde_json::json!(6)
        );

        store
            .complete_startup_migration(&startup.preferences)
            .expect("finalize migration");
        let persisted = serde_json::from_slice::<serde_json::Value>(
            &std::fs::read(&path).expect("read migrated preferences"),
        )
        .expect("parse migrated preferences");
        assert_eq!(persisted["schema_version"], serde_json::json!(8));
        assert!(persisted.get("memory_enabled").is_none());
        assert!(persisted.get("memory_retention_days").is_none());
        assert!(store
            .load_for_startup()
            .expect("reload startup preferences")
            .legacy_memory
            .is_none());
    }
}
