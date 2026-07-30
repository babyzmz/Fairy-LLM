use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::realtime_privacy::{normalize_excluded_applications, RealtimeCaptureMode};

const PREFERENCES_FILE: &str = "preferences/desktop.json";
const SCHEMA_VERSION: u32 = 11;

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
pub enum RealtimeBackendPreference {
    #[default]
    Auto,
    LocalMiniCpmO45,
    CloudLive,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeCloudProviderPreference {
    #[default]
    GlmRealtimeFlash,
    GeminiLive,
    GlmRealtimeAir,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeActivityProfilePreference {
    #[default]
    Auto,
    Game,
    Focus,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeInteractionIntensityPreference {
    Quiet,
    #[default]
    Standard,
    Active,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeVoiceOutputPreference {
    #[default]
    FairyVoice,
    ProviderNativeVoice,
    TextOnly,
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
    #[serde(default = "default_true")]
    pub voice_replies_enabled: bool,
    pub voice_volume_percent: u8,
    pub voice_rate_percent: u8,
    pub permission_cloud_profile: String,
    pub analytics_enabled: bool,
    #[serde(default)]
    pub realtime_backend: RealtimeBackendPreference,
    #[serde(default)]
    pub realtime_cloud_provider: RealtimeCloudProviderPreference,
    #[serde(default)]
    pub realtime_allow_cloud_fallback: bool,
    #[serde(default)]
    pub realtime_activity_profile: RealtimeActivityProfilePreference,
    #[serde(default)]
    pub realtime_interaction_intensity: RealtimeInteractionIntensityPreference,
    #[serde(default)]
    pub realtime_voice_output: RealtimeVoiceOutputPreference,
    #[serde(default)]
    pub realtime_game_audio_default: bool,
    #[serde(default)]
    pub realtime_capture_mode: RealtimeCaptureMode,
    #[serde(default)]
    pub realtime_excluded_applications: Vec<String>,
    #[serde(default)]
    pub realtime_online_assistance_enabled: bool,
    #[serde(default = "default_true")]
    pub realtime_memory_enabled: bool,
    #[serde(default = "default_realtime_presence_minutes")]
    pub realtime_presence_max_minutes: u16,
    #[serde(default = "default_realtime_cloud_daily_minutes")]
    pub realtime_cloud_daily_limit_minutes: u16,
    #[serde(default = "default_realtime_local_keep_warm_minutes")]
    pub realtime_local_keep_warm_minutes: u8,
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
            voice_replies_enabled: true,
            voice_volume_percent: 80,
            voice_rate_percent: 100,
            permission_cloud_profile: "standard".to_owned(),
            analytics_enabled: false,
            realtime_backend: RealtimeBackendPreference::Auto,
            realtime_cloud_provider: RealtimeCloudProviderPreference::GlmRealtimeFlash,
            realtime_allow_cloud_fallback: false,
            realtime_activity_profile: RealtimeActivityProfilePreference::Auto,
            realtime_interaction_intensity: RealtimeInteractionIntensityPreference::Standard,
            realtime_voice_output: RealtimeVoiceOutputPreference::FairyVoice,
            realtime_game_audio_default: false,
            realtime_capture_mode: RealtimeCaptureMode::SelectedWindow,
            realtime_excluded_applications: Vec::new(),
            realtime_online_assistance_enabled: false,
            realtime_memory_enabled: true,
            realtime_presence_max_minutes: default_realtime_presence_minutes(),
            realtime_cloud_daily_limit_minutes: default_realtime_cloud_daily_minutes(),
            realtime_local_keep_warm_minutes: default_realtime_local_keep_warm_minutes(),
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
    pub voice_replies_enabled: Option<bool>,
    pub pet_muted: Option<bool>,
    pub pet_always_on_top: Option<bool>,
    pub pet_anchor: Option<PetAnchorPreference>,
    #[serde(default)]
    pub clear_pet_anchor: bool,
}

#[derive(Clone, Copy, Debug, Default, Deserialize)]
#[serde(rename_all = "snake_case")]
enum LegacyRealtimeProviderPreference {
    #[default]
    Auto,
    GeminiLive,
    GlmRealtimeFlash,
    GlmRealtimeAir,
}

#[derive(Clone, Copy, Debug, Default, Deserialize)]
#[serde(rename_all = "snake_case")]
enum LegacyRealtimeVoicePreference {
    #[default]
    Native,
    Fairy,
}

#[derive(Clone, Copy, Debug, Deserialize)]
struct LegacyRealtimePreferencesV8 {
    #[serde(default)]
    realtime_provider: LegacyRealtimeProviderPreference,
    #[serde(default)]
    realtime_voice_mode: LegacyRealtimeVoicePreference,
    #[serde(default)]
    realtime_game_audio_default: bool,
    #[serde(default = "default_true")]
    realtime_memory_enabled: bool,
    #[serde(default = "default_legacy_realtime_session_minutes")]
    realtime_max_session_minutes: u16,
}

impl LegacyRealtimePreferencesV8 {
    fn apply_to(self, preferences: &mut DesktopPreferences) {
        preferences.realtime_backend = RealtimeBackendPreference::Auto;
        preferences.realtime_cloud_provider = match self.realtime_provider {
            LegacyRealtimeProviderPreference::Auto
            | LegacyRealtimeProviderPreference::GlmRealtimeFlash => {
                RealtimeCloudProviderPreference::GlmRealtimeFlash
            }
            LegacyRealtimeProviderPreference::GeminiLive => {
                RealtimeCloudProviderPreference::GeminiLive
            }
            LegacyRealtimeProviderPreference::GlmRealtimeAir => {
                RealtimeCloudProviderPreference::GlmRealtimeAir
            }
        };
        preferences.realtime_allow_cloud_fallback = false;
        preferences.realtime_activity_profile = RealtimeActivityProfilePreference::Auto;
        preferences.realtime_interaction_intensity =
            RealtimeInteractionIntensityPreference::Standard;
        preferences.realtime_voice_output = match self.realtime_voice_mode {
            LegacyRealtimeVoicePreference::Native => {
                RealtimeVoiceOutputPreference::ProviderNativeVoice
            }
            LegacyRealtimeVoicePreference::Fairy => RealtimeVoiceOutputPreference::FairyVoice,
        };
        preferences.realtime_game_audio_default = self.realtime_game_audio_default;
        preferences.realtime_capture_mode = RealtimeCaptureMode::SelectedWindow;
        preferences.realtime_excluded_applications.clear();
        preferences.realtime_online_assistance_enabled = false;
        preferences.realtime_memory_enabled = self.realtime_memory_enabled;
        preferences.realtime_presence_max_minutes = default_realtime_presence_minutes();
        preferences.realtime_cloud_daily_limit_minutes =
            if [30, 60, 120, 180].contains(&self.realtime_max_session_minutes) {
                self.realtime_max_session_minutes
            } else {
                default_realtime_cloud_daily_minutes()
            };
        preferences.realtime_local_keep_warm_minutes = default_realtime_local_keep_warm_minutes();
    }
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
        let legacy_realtime_schema = matches!(schema_version, Some(1..=8));
        let needs_schema_upgrade = matches!(schema_version, Some(1..=10));
        let legacy_voice_replies = if matches!(schema_version, Some(1..=10)) {
            match (
                value
                    .get("voice_auto_play_chat")
                    .and_then(serde_json::Value::as_bool),
                value
                    .get("voice_auto_play_pet")
                    .and_then(serde_json::Value::as_bool),
            ) {
                (Some(chat), Some(pet)) => Some(chat || pet),
                (Some(chat), None) => Some(chat),
                (None, Some(pet)) => Some(pet),
                (None, None) => Some(DesktopPreferences::default().voice_replies_enabled),
            }
        } else {
            None
        };
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
            if let Some(enabled) = legacy_voice_replies {
                preferences.voice_replies_enabled = enabled;
            }
            if legacy_realtime_schema {
                let legacy_realtime =
                    serde_json::from_value::<LegacyRealtimePreferencesV8>(value.clone())?;
                legacy_realtime.apply_to(&mut preferences);
            }
            preferences.schema_version = SCHEMA_VERSION;
            if normalize_realtime_exclusions(&mut preferences).is_err()
                || validate(&preferences).is_err()
            {
                preferences = DesktopPreferences::default();
            }
            return Ok(StartupDesktopPreferences {
                preferences,
                legacy_memory,
                migration_required: true,
            });
        }
        let normalized_exclusions =
            normalize_excluded_applications(&preferences.realtime_excluded_applications)
                .map_err(|_| invalid_realtime_exclusions())?;
        let exclusions_changed =
            normalized_exclusions != preferences.realtime_excluded_applications;
        preferences.realtime_excluded_applications = normalized_exclusions;
        validate(&preferences)?;
        Ok(StartupDesktopPreferences {
            preferences,
            legacy_memory: None,
            migration_required: retired_activation_style || exclusions_changed,
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
        normalize_realtime_exclusions(&mut next)?;
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
        if let Some(value) = update.voice_replies_enabled {
            next.voice_replies_enabled = value;
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

fn invalid_realtime_exclusions() -> DesktopPreferencesError {
    DesktopPreferencesError::Invalid(
        "realtime excluded applications must be unique executable basenames".to_owned(),
    )
}

fn normalize_realtime_exclusions(
    preferences: &mut DesktopPreferences,
) -> Result<(), DesktopPreferencesError> {
    preferences.realtime_excluded_applications =
        normalize_excluded_applications(&preferences.realtime_excluded_applications)
            .map_err(|_| invalid_realtime_exclusions())?;
    Ok(())
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
    if !(30..=240).contains(&preferences.realtime_presence_max_minutes) {
        return Err(DesktopPreferencesError::Invalid(
            "realtime presence maximum must be between 30 and 240 minutes".to_owned(),
        ));
    }
    if ![30, 60, 120, 180].contains(&preferences.realtime_cloud_daily_limit_minutes) {
        return Err(DesktopPreferencesError::Invalid(
            "realtime cloud daily limit must be 30, 60, 120, or 180 minutes".to_owned(),
        ));
    }
    if preferences.realtime_local_keep_warm_minutes > 30 {
        return Err(DesktopPreferencesError::Invalid(
            "realtime local keep-warm must be between 0 and 30 minutes".to_owned(),
        ));
    }
    normalize_excluded_applications(&preferences.realtime_excluded_applications)
        .map_err(|_| invalid_realtime_exclusions())?;
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

const fn default_legacy_realtime_session_minutes() -> u16 {
    30
}

const fn default_realtime_presence_minutes() -> u16 {
    240
}

const fn default_realtime_cloud_daily_minutes() -> u16 {
    180
}

const fn default_realtime_local_keep_warm_minutes() -> u8 {
    10
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
        PetPreferencesUpdate, RealtimeActivityProfilePreference, RealtimeBackendPreference,
        RealtimeCaptureMode, RealtimeCloudProviderPreference,
        RealtimeInteractionIntensityPreference, RealtimeVoiceOutputPreference,
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
                voice_replies_enabled: Some(false),
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
        assert!(!saved.voice_replies_enabled);
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
    fn schema_ten_migrates_legacy_voice_preferences_with_logical_or() {
        for (chat, pet, expected) in [
            (false, false, false),
            (false, true, true),
            (true, false, true),
            (true, true, true),
        ] {
            let directory = tempfile::tempdir().expect("temporary directory");
            let store = DesktopPreferencesStore::new(directory.path());
            let path = directory.path().join("preferences/desktop.json");
            std::fs::create_dir_all(path.parent().expect("preferences parent"))
                .expect("create preferences parent");
            let mut stored =
                serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
            let object = stored.as_object_mut().expect("preferences object");
            object.insert("schema_version".to_owned(), serde_json::json!(10));
            object.remove("voice_replies_enabled");
            object.insert("voice_auto_play_chat".to_owned(), serde_json::json!(chat));
            object.insert("voice_auto_play_pet".to_owned(), serde_json::json!(pet));
            std::fs::write(
                &path,
                serde_json::to_vec_pretty(&stored).expect("stored bytes"),
            )
            .expect("write stored preferences");

            let migrated = store.load().expect("migrate voice preferences");

            assert_eq!(migrated.schema_version, 11);
            assert_eq!(migrated.voice_replies_enabled, expected);
            let persisted =
                serde_json::from_slice::<serde_json::Value>(&std::fs::read(&path).expect("read"))
                    .expect("parse persisted preferences");
            assert_eq!(persisted["voice_replies_enabled"], expected);
            assert!(persisted.get("voice_auto_play_chat").is_none());
            assert!(persisted.get("voice_auto_play_pet").is_none());
        }
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
        object.insert("voice_auto_play_chat".to_owned(), serde_json::json!(false));
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
        assert_eq!(migrated.schema_version, 11);
        assert!(!migrated.voice_replies_enabled);
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
        assert_eq!(migrated.schema_version, 11);
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
        assert_eq!(migrated.schema_version, 11);
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
        assert_eq!(loaded.schema_version, 11);
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
        assert_eq!(migrated.schema_version, 11);
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
        assert_eq!(startup.preferences.schema_version, 11);
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
        assert_eq!(persisted["schema_version"], serde_json::json!(11));
        assert!(persisted.get("memory_enabled").is_none());
        assert!(persisted.get("memory_retention_days").is_none());
        assert!(store
            .load_for_startup()
            .expect("reload startup preferences")
            .legacy_memory
            .is_none());
    }

    #[test]
    fn version_eight_realtime_preferences_migrate_without_enabling_beta() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut stored =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = stored.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(8));
        object.insert(
            "realtime_provider".to_owned(),
            serde_json::json!("glm_realtime_flash"),
        );
        object.insert(
            "realtime_voice_mode".to_owned(),
            serde_json::json!("native"),
        );
        object.insert(
            "realtime_game_audio_default".to_owned(),
            serde_json::json!(true),
        );
        object.insert(
            "realtime_memory_enabled".to_owned(),
            serde_json::json!(false),
        );
        object.insert(
            "realtime_max_session_minutes".to_owned(),
            serde_json::json!(120),
        );
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&stored).expect("stored bytes"),
        )
        .expect("write stored preferences");

        let migrated = store.load().expect("migrate version eight");
        assert_eq!(migrated.schema_version, 11);
        assert_eq!(migrated.realtime_backend, RealtimeBackendPreference::Auto);
        assert_eq!(
            migrated.realtime_cloud_provider,
            RealtimeCloudProviderPreference::GlmRealtimeFlash
        );
        assert_eq!(
            migrated.realtime_voice_output,
            RealtimeVoiceOutputPreference::ProviderNativeVoice
        );
        assert!(migrated.realtime_game_audio_default);
        assert!(!migrated.realtime_memory_enabled);
        assert_eq!(migrated.realtime_cloud_daily_limit_minutes, 120);
        assert!(!migrated.realtime_allow_cloud_fallback);
        assert!(!migrated.realtime_online_assistance_enabled);
    }

    #[test]
    fn schema_eleven_defaults_are_privacy_safe_and_bounded() {
        let defaults = DesktopPreferences::default();

        assert_eq!(defaults.schema_version, 11);
        assert_eq!(defaults.realtime_backend, RealtimeBackendPreference::Auto);
        assert_eq!(
            defaults.realtime_activity_profile,
            RealtimeActivityProfilePreference::Auto
        );
        assert_eq!(
            defaults.realtime_interaction_intensity,
            RealtimeInteractionIntensityPreference::Standard
        );
        assert_eq!(
            defaults.realtime_voice_output,
            RealtimeVoiceOutputPreference::FairyVoice
        );
        assert_eq!(defaults.realtime_presence_max_minutes, 240);
        assert_eq!(defaults.realtime_cloud_daily_limit_minutes, 180);
        assert_eq!(defaults.realtime_local_keep_warm_minutes, 10);
        assert_eq!(
            defaults.realtime_capture_mode,
            RealtimeCaptureMode::SelectedWindow
        );
        assert!(defaults.realtime_excluded_applications.is_empty());
        assert!(!defaults.realtime_allow_cloud_fallback);
        assert!(!defaults.realtime_online_assistance_enabled);
    }

    #[test]
    fn schema_nine_migrates_capture_defaults_without_resetting_realtime_choices() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let path = directory.path().join("preferences/desktop.json");
        std::fs::create_dir_all(path.parent().expect("preferences parent"))
            .expect("create preferences parent");
        let mut stored =
            serde_json::to_value(DesktopPreferences::default()).expect("serialize defaults");
        let object = stored.as_object_mut().expect("preferences object");
        object.insert("schema_version".to_owned(), serde_json::json!(9));
        object.insert("realtime_beta_enabled".to_owned(), serde_json::json!(true));
        object.insert(
            "realtime_backend".to_owned(),
            serde_json::json!("cloud_live"),
        );
        object.remove("realtime_capture_mode");
        object.remove("realtime_excluded_applications");
        std::fs::write(
            &path,
            serde_json::to_vec_pretty(&stored).expect("stored bytes"),
        )
        .expect("write stored preferences");

        let migrated = store.load().expect("migrate version nine");
        assert_eq!(migrated.schema_version, 11);
        assert_eq!(
            migrated.realtime_backend,
            RealtimeBackendPreference::CloudLive
        );
        assert_eq!(
            migrated.realtime_capture_mode,
            RealtimeCaptureMode::SelectedWindow
        );
        assert!(migrated.realtime_excluded_applications.is_empty());
        let persisted = std::fs::read_to_string(path).expect("read migrated preferences");
        assert!(!persisted.contains("realtime_beta_enabled"));
    }

    #[test]
    fn excluded_applications_reject_paths_wildcards_duplicates_and_oversized_lists() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        for excluded in [
            vec!["C:\\private.exe".to_owned()],
            vec!["*.exe".to_owned()],
            vec!["same.exe".to_owned(), "SAME.EXE".to_owned()],
            vec!["app.exe".to_owned(); 33],
        ] {
            let preferences = DesktopPreferences {
                realtime_excluded_applications: excluded,
                ..DesktopPreferences::default()
            };
            assert!(matches!(
                store.update(DesktopPreferencesUpdate {
                    expected_revision: 0,
                    preferences,
                }),
                Err(DesktopPreferencesError::Invalid(_))
            ));
        }
    }

    #[test]
    fn excluded_applications_are_trimmed_and_normalized_before_persisting() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = DesktopPreferencesStore::new(directory.path());
        let preferences = DesktopPreferences {
            realtime_excluded_applications: vec![
                " OBS64.EXE ".to_owned(),
                "Private-App.Exe".to_owned(),
            ],
            ..DesktopPreferences::default()
        };

        let saved = store
            .update(DesktopPreferencesUpdate {
                expected_revision: 0,
                preferences,
            })
            .expect("normalize exclusions");

        assert_eq!(
            saved.realtime_excluded_applications,
            vec!["obs64.exe", "private-app.exe"]
        );
        assert_eq!(store.load().expect("reload normalized"), saved);
    }
}
