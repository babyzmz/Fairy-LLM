use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

const CONFIG_FILE: &str = "providers/openrouter.json";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct OpenRouterConfiguration {
    pub model_id: String,
}

#[derive(Debug, Error)]
pub enum ProviderConfigurationError {
    #[error("OpenRouter model id is invalid")]
    InvalidModel,
    #[error("provider configuration failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("provider configuration is malformed: {0}")]
    Json(#[from] serde_json::Error),
}

pub struct ProviderConfigurationStore {
    path: PathBuf,
}

impl ProviderConfigurationStore {
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        Self {
            path: data_dir.as_ref().join(CONFIG_FILE),
        }
    }

    pub fn save_openrouter(
        &self,
        model_id: &str,
    ) -> Result<OpenRouterConfiguration, ProviderConfigurationError> {
        let model_id = validate_model_id(model_id)?;
        let configuration = OpenRouterConfiguration { model_id };
        let parent = self.path.parent().ok_or_else(|| {
            ProviderConfigurationError::Io(std::io::Error::other(
                "provider configuration path has no parent",
            ))
        })?;
        fs::create_dir_all(parent)?;
        let temporary = self.path.with_extension("tmp");
        fs::write(&temporary, serde_json::to_vec_pretty(&configuration)?)?;
        fs::rename(temporary, &self.path)?;
        Ok(configuration)
    }

    pub fn load_openrouter(
        &self,
    ) -> Result<Option<OpenRouterConfiguration>, ProviderConfigurationError> {
        if !self.path.is_file() {
            return Ok(None);
        }
        let configuration: OpenRouterConfiguration =
            serde_json::from_slice(&fs::read(&self.path)?)?;
        Ok(Some(OpenRouterConfiguration {
            model_id: validate_model_id(&configuration.model_id)?,
        }))
    }

    pub fn delete_openrouter(&self) -> Result<(), ProviderConfigurationError> {
        match fs::remove_file(&self.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error.into()),
        }
    }
}

pub fn openrouter_profiles_json(configuration: &OpenRouterConfiguration) -> String {
    serde_json::json!([{
        "id": "openrouter",
        "display_name": "OpenRouter",
        "kind": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "model_id": configuration.model_id,
        "capabilities": ["text", "tools", "vision"],
        "credential_ref": "openrouter",
        "fallback_profile_id": null,
        "timeout_seconds": 180,
        "enabled": true
    }])
    .to_string()
}

fn validate_model_id(value: &str) -> Result<String, ProviderConfigurationError> {
    let normalized = value.trim();
    if normalized.is_empty()
        || normalized.len() > 255
        || !normalized
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "/:._-".contains(character))
    {
        return Err(ProviderConfigurationError::InvalidModel);
    }
    Ok(normalized.to_owned())
}

#[cfg(test)]
mod tests {
    use super::{openrouter_profiles_json, ProviderConfigurationStore};

    #[test]
    fn configuration_round_trips_without_secret_material() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = ProviderConfigurationStore::new(directory.path());
        let saved = store
            .save_openrouter("nvidia/nemotron-3-ultra-550b-a55b:free")
            .expect("save configuration");

        assert_eq!(
            store.load_openrouter().expect("load configuration"),
            Some(saved.clone())
        );
        let profiles = openrouter_profiles_json(&saved);
        assert!(profiles.contains("nvidia/nemotron-3-ultra-550b-a55b:free"));
        assert!(!profiles.contains("api_key"));
        assert!(!profiles.contains("secret"));
    }

    #[test]
    fn configuration_rejects_model_ids_that_could_escape_json_or_urls() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = ProviderConfigurationStore::new(directory.path());
        assert!(store.save_openrouter("bad model\"}").is_err());
    }
}
