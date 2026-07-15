use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

const CONFIG_FILE: &str = "providers/openrouter.json";
const CONFIG_SCHEMA_VERSION: u32 = 2;
pub const OPENROUTER_ACCOUNT_ID: &str = "openrouter-default";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct OpenRouterConfiguration {
    pub schema_version: u32,
    pub account_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum StoredOpenRouterConfiguration {
    Current(OpenRouterConfiguration),
    Legacy { model_id: String },
}

#[derive(Debug, Error)]
pub enum ProviderConfigurationError {
    #[error("OpenRouter provider configuration is invalid")]
    InvalidConfiguration,
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

    pub fn save_openrouter(&self) -> Result<OpenRouterConfiguration, ProviderConfigurationError> {
        let configuration = default_configuration();
        let parent = self.path.parent().ok_or_else(|| {
            ProviderConfigurationError::Io(std::io::Error::other(
                "provider configuration path has no parent",
            ))
        })?;
        fs::create_dir_all(parent)?;
        let temporary = self.path.with_extension("tmp");
        fs::write(&temporary, serde_json::to_vec_pretty(&configuration)?)?;
        if self.path.exists() {
            fs::remove_file(&self.path)?;
        }
        fs::rename(temporary, &self.path)?;
        Ok(configuration)
    }

    pub fn load_openrouter(
        &self,
    ) -> Result<Option<OpenRouterConfiguration>, ProviderConfigurationError> {
        if !self.path.is_file() {
            return Ok(None);
        }
        let stored: StoredOpenRouterConfiguration = serde_json::from_slice(&fs::read(&self.path)?)?;
        let configuration = match stored {
            StoredOpenRouterConfiguration::Current(configuration) => {
                validate_configuration(configuration)?
            }
            StoredOpenRouterConfiguration::Legacy { model_id } => {
                validate_legacy_model_id(&model_id)?;
                let migrated = default_configuration();
                self.save_openrouter()?;
                migrated
            }
        };
        Ok(Some(configuration))
    }

    pub fn delete_openrouter(&self) -> Result<(), ProviderConfigurationError> {
        match fs::remove_file(&self.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error.into()),
        }
    }
}

pub fn openrouter_profiles_json(_configuration: &OpenRouterConfiguration) -> String {
    serde_json::json!([
        profile(
            "openrouter-deepseek-v4-pro",
            "DeepSeek V4 Pro",
            "deepseek/deepseek-v4-pro",
            &["text", "tools", "structured_output"],
            None,
        ),
        profile(
            "openrouter-glm-5-2",
            "GLM 5.2",
            "z-ai/glm-5.2",
            &["text", "tools", "structured_output"],
            None,
        ),
        profile(
            "openrouter-kimi-k2-7-code",
            "Kimi K2.7 Code",
            "moonshotai/kimi-k2.7-code",
            &["text", "tools", "vision", "structured_output"],
            None,
        ),
        profile(
            "openrouter-nemotron-free",
            "Nemotron 3 Ultra",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            &["text", "tools"],
            None,
        ),
        profile(
            "openrouter-qwen3-coder-free",
            "Qwen3 Coder",
            "qwen/qwen3-coder:free",
            &["text", "tools"],
            None,
        ),
    ])
    .to_string()
}

fn profile(
    id: &str,
    display_name: &str,
    model_id: &str,
    capabilities: &[&str],
    fallback_profile_id: Option<&str>,
) -> serde_json::Value {
    serde_json::json!({
        "id": id,
        "display_name": display_name,
        "kind": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "model_id": model_id,
        "capabilities": capabilities,
        "credential_ref": "openrouter",
        "fallback_profile_id": fallback_profile_id,
        "timeout_seconds": 180,
        "enabled": true
    })
}

fn default_configuration() -> OpenRouterConfiguration {
    OpenRouterConfiguration {
        schema_version: CONFIG_SCHEMA_VERSION,
        account_id: OPENROUTER_ACCOUNT_ID.to_owned(),
    }
}

fn validate_configuration(
    configuration: OpenRouterConfiguration,
) -> Result<OpenRouterConfiguration, ProviderConfigurationError> {
    if configuration.schema_version != CONFIG_SCHEMA_VERSION
        || configuration.account_id != OPENROUTER_ACCOUNT_ID
    {
        return Err(ProviderConfigurationError::InvalidConfiguration);
    }
    Ok(configuration)
}

fn validate_legacy_model_id(value: &str) -> Result<(), ProviderConfigurationError> {
    let normalized = value.trim();
    if normalized.is_empty()
        || normalized.len() > 255
        || !normalized
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "/:._-".contains(character))
    {
        return Err(ProviderConfigurationError::InvalidConfiguration);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::{openrouter_profiles_json, ProviderConfigurationStore, OPENROUTER_ACCOUNT_ID};

    #[test]
    fn account_configuration_round_trips_without_secret_material() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = ProviderConfigurationStore::new(directory.path());
        let saved = store.save_openrouter().expect("save configuration");

        assert_eq!(saved.account_id, OPENROUTER_ACCOUNT_ID);
        assert_eq!(
            store.load_openrouter().expect("load configuration"),
            Some(saved)
        );
        let stored = fs::read_to_string(directory.path().join("providers/openrouter.json"))
            .expect("read public configuration");
        assert!(!stored.contains("model_id"));
        assert!(!stored.contains("api_key"));
        assert!(!stored.contains("secret"));
    }

    #[test]
    fn legacy_model_configuration_migrates_to_the_account_schema() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("providers/openrouter.json");
        fs::create_dir_all(path.parent().expect("provider directory"))
            .expect("create provider directory");
        fs::write(
            &path,
            r#"{"model_id":"nvidia/nemotron-3-ultra-550b-a55b:free"}"#,
        )
        .expect("write legacy configuration");

        let configuration = ProviderConfigurationStore::new(directory.path())
            .load_openrouter()
            .expect("migrate configuration")
            .expect("configured account");
        assert_eq!(configuration.account_id, OPENROUTER_ACCOUNT_ID);
        assert!(!fs::read_to_string(path)
            .expect("read migrated file")
            .contains("model_id"));
    }

    #[test]
    fn profiles_are_fixed_to_the_approved_text_allowlist() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let configuration = ProviderConfigurationStore::new(directory.path())
            .save_openrouter()
            .expect("save configuration");
        let profiles = openrouter_profiles_json(&configuration);

        for model_id in [
            "deepseek/deepseek-v4-pro",
            "z-ai/glm-5.2",
            "moonshotai/kimi-k2.7-code",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "qwen/qwen3-coder:free",
        ] {
            assert!(profiles.contains(model_id));
        }
        assert!(!profiles.contains("tencent/hy3:free"));
        assert!(!profiles.contains("openrouter/free"));
        assert!(!profiles.contains("api_key"));
    }
}
