use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

const STATE_FILE: &str = "preferences/ambient-dialogue.json";
const SCHEMA_VERSION: u16 = 1;
const MAX_HISTORY_ITEMS: usize = 256;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AmbientDialogueLocalState {
    #[serde(default = "schema_version")]
    pub schema_version: u16,
    #[serde(default)]
    pub revision: u64,
    #[serde(default)]
    pub local_date: Option<String>,
    #[serde(default)]
    pub startup_date: Option<String>,
    #[serde(default)]
    pub daily_text_count: u16,
    #[serde(default)]
    pub daily_voice_count: u16,
    #[serde(default)]
    pub daily_generated_count: u16,
    #[serde(default)]
    pub last_global_at: Option<String>,
    #[serde(default)]
    pub category_last_at: BTreeMap<String, String>,
    #[serde(default)]
    pub line_last_at: BTreeMap<String, String>,
    #[serde(default)]
    pub returned_last_at: Option<String>,
    #[serde(default)]
    pub generated_digests: Vec<String>,
}

impl Default for AmbientDialogueLocalState {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            revision: 0,
            local_date: None,
            startup_date: None,
            daily_text_count: 0,
            daily_voice_count: 0,
            daily_generated_count: 0,
            last_global_at: None,
            category_last_at: BTreeMap::new(),
            line_last_at: BTreeMap::new(),
            returned_last_at: None,
            generated_digests: Vec::new(),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct AmbientDialogueStateUpdate {
    pub expected_revision: u64,
    pub state: AmbientDialogueLocalState,
}

#[derive(Debug, Error)]
pub enum AmbientDialogueStateError {
    #[error("ambient dialogue state revision conflict")]
    RevisionConflict,
    #[error("ambient dialogue state is invalid")]
    Invalid,
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

pub struct AmbientDialogueStateStore {
    path: PathBuf,
}

impl AmbientDialogueStateStore {
    pub fn new(data_dir: &Path) -> Self {
        Self {
            path: data_dir.join(STATE_FILE),
        }
    }

    pub fn load(&self) -> Result<AmbientDialogueLocalState, AmbientDialogueStateError> {
        if !self.path.exists() {
            return Ok(AmbientDialogueLocalState::default());
        }
        let bytes = fs::read(&self.path)?;
        let Ok(state) = serde_json::from_slice::<AmbientDialogueLocalState>(&bytes) else {
            return Ok(AmbientDialogueLocalState::default());
        };
        if validate(&state).is_err() {
            return Ok(AmbientDialogueLocalState::default());
        }
        Ok(state)
    }

    pub fn update(
        &self,
        update: AmbientDialogueStateUpdate,
    ) -> Result<AmbientDialogueLocalState, AmbientDialogueStateError> {
        let current = self.load()?;
        if current.revision != update.expected_revision {
            return Err(AmbientDialogueStateError::RevisionConflict);
        }
        let mut next = update.state;
        next.schema_version = SCHEMA_VERSION;
        next.revision = current.revision + 1;
        validate(&next)?;
        self.save(&next)?;
        Ok(next)
    }

    fn save(&self, state: &AmbientDialogueLocalState) -> Result<(), AmbientDialogueStateError> {
        let parent = self
            .path
            .parent()
            .ok_or(AmbientDialogueStateError::Invalid)?;
        fs::create_dir_all(parent)?;
        let temporary = parent.join(format!(".ambient-dialogue.{}.tmp", Uuid::new_v4()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(&serde_json::to_vec_pretty(state)?)?;
        file.sync_all()?;
        replace_file(&temporary, &self.path)?;
        Ok(())
    }
}

const fn schema_version() -> u16 {
    SCHEMA_VERSION
}

fn validate(state: &AmbientDialogueLocalState) -> Result<(), AmbientDialogueStateError> {
    if state.schema_version != SCHEMA_VERSION
        || state.category_last_at.len() > MAX_HISTORY_ITEMS
        || state.line_last_at.len() > MAX_HISTORY_ITEMS
        || state.generated_digests.len() > 64
        || state
            .category_last_at
            .iter()
            .chain(state.line_last_at.iter())
            .any(|(key, value)| key.len() > 128 || value.len() > 64)
        || state.generated_digests.iter().any(|digest| {
            digest.len() != 64
                || !digest
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
    {
        return Err(AmbientDialogueStateError::Invalid);
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let mut source_wide = source.as_os_str().encode_wide().collect::<Vec<_>>();
    source_wide.push(0);
    let mut destination_wide = destination.as_os_str().encode_wide().collect::<Vec<_>>();
    destination_wide.push(0);
    let result = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn replace_file(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    fs::rename(source, destination)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_is_revision_fenced_and_contains_no_dialogue_text_or_context() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = AmbientDialogueStateStore::new(directory.path());
        let initial = store.load().expect("load initial state");
        let mut state = initial.clone();
        state.local_date = Some("2026-07-23".to_owned());
        state.daily_text_count = 1;
        state.line_last_at.insert(
            "startup.daily.01".to_owned(),
            "2026-07-23T09:00:00Z".to_owned(),
        );

        let saved = store
            .update(AmbientDialogueStateUpdate {
                expected_revision: initial.revision,
                state,
            })
            .expect("save state");
        assert_eq!(saved.revision, 1);
        assert!(matches!(
            store.update(AmbientDialogueStateUpdate {
                expected_revision: 0,
                state: saved.clone(),
            }),
            Err(AmbientDialogueStateError::RevisionConflict)
        ));
        let raw = fs::read_to_string(directory.path().join(STATE_FILE)).expect("read state");
        assert!(!raw.contains("user_idle_seconds"));
        assert!(!raw.contains("系统启动"));
    }

    #[test]
    fn partial_state_file_recovers_to_safe_defaults() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = AmbientDialogueStateStore::new(directory.path());
        let path = directory.path().join(STATE_FILE);
        fs::create_dir_all(path.parent().expect("state parent")).expect("create parent");
        fs::write(&path, br#"{"revision": 4, "daily_text_count": "#).expect("write partial state");

        assert_eq!(
            store.load().expect("recover partial state"),
            AmbientDialogueLocalState::default()
        );
    }
}
