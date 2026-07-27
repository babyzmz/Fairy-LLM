use std::collections::HashSet;
use std::path::{Component, Path};

use serde::{Deserialize, Serialize};
use thiserror::Error;

const MODEL_ID: &str = "openbmb/minicpm-o-4.5-fairy-beta";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OmniModelFile {
    pub path: String,
    pub size: u64,
    pub sha256: String,
    pub urls: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OmniModelManifest {
    pub schema_version: u16,
    pub manifest_digest: String,
    pub model_id: String,
    pub version: String,
    pub runtime_compatibility: String,
    pub license: String,
    pub predicted_peak_vram_mb: u64,
    pub upstream_runtime_revision: String,
    pub patch_set_digest: String,
    pub files: Vec<OmniModelFile>,
}

#[derive(Clone, Copy, Debug, Error, Eq, PartialEq)]
pub enum ManifestError {
    #[error("the Omni model manifest is invalid")]
    Invalid,
}

impl OmniModelManifest {
    pub fn parse_and_validate(bytes: &[u8]) -> Result<Self, ManifestError> {
        let manifest: Self = serde_json::from_slice(bytes).map_err(|_| ManifestError::Invalid)?;
        manifest.validate()?;
        Ok(manifest)
    }

    fn validate(&self) -> Result<(), ManifestError> {
        if self.schema_version != 1
            || self.model_id != MODEL_ID
            || self.version.trim().is_empty()
            || self.runtime_compatibility != "fairy-omni-runtime-v1"
            || self.license.trim().is_empty()
            || self.predicted_peak_vram_mb == 0
            || !is_lower_hex(&self.manifest_digest, 64)
            || !is_lower_hex(&self.upstream_runtime_revision, 40)
            || !is_lower_hex(&self.patch_set_digest, 64)
            || self.files.is_empty()
        {
            return Err(ManifestError::Invalid);
        }

        let mut paths = HashSet::new();
        for file in &self.files {
            if !valid_relative_path(&file.path)
                || file.size == 0
                || !is_lower_hex(&file.sha256, 64)
                || file.urls.is_empty()
                || file.urls.iter().any(|url| !url.starts_with("https://"))
                || !paths.insert(file.path.to_lowercase())
            {
                return Err(ManifestError::Invalid);
            }
        }
        Ok(())
    }
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_relative_path(value: &str) -> bool {
    if value.trim().is_empty() {
        return false;
    }
    let path = Path::new(value);
    if path.is_absolute() {
        return false;
    }
    path.components()
        .all(|component| matches!(component, Component::Normal(_)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_manifest() -> serde_json::Value {
        serde_json::json!({
            "schema_version": 1,
            "manifest_digest": "a".repeat(64),
            "model_id": "openbmb/minicpm-o-4.5-fairy-beta",
            "version": "4.5-q4-1",
            "runtime_compatibility": "fairy-omni-runtime-v1",
            "license": "Apache-2.0",
            "predicted_peak_vram_mb": 9500,
            "upstream_runtime_revision": "b".repeat(40),
            "patch_set_digest": "c".repeat(64),
            "files": [{
                "path": "MiniCPM-o-4_5-Q4_K_M.gguf",
                "size": 1024,
                "sha256": "d".repeat(64),
                "urls": ["https://models.example.invalid/minicpm.gguf"]
            }]
        })
    }

    fn parse(value: &serde_json::Value) -> Result<OmniModelManifest, ManifestError> {
        OmniModelManifest::parse_and_validate(
            serde_json::to_vec(value)
                .expect("serialize manifest")
                .as_slice(),
        )
    }

    #[test]
    fn valid_manifest_is_accepted() {
        let parsed = parse(&valid_manifest()).expect("valid manifest");
        assert_eq!(parsed.predicted_peak_vram_mb, 9500);
    }

    #[test]
    fn unsafe_or_ambiguous_paths_are_rejected() {
        for path in [
            "../model.gguf",
            "C:/model.gguf",
            "/model.gguf",
            "",
            "audio/../model.gguf",
        ] {
            let mut value = valid_manifest();
            value["files"][0]["path"] = serde_json::json!(path);
            assert!(parse(&value).is_err(), "{path} must be rejected");
        }
    }

    #[test]
    fn artifact_metadata_must_be_release_grade() {
        let mutations = [
            ("size", serde_json::json!(0)),
            ("sha256", serde_json::json!("ABC")),
            ("urls", serde_json::json!([])),
        ];
        for (field, replacement) in mutations {
            let mut value = valid_manifest();
            value["files"][0][field] = replacement;
            assert!(parse(&value).is_err(), "{field} must be rejected");
        }

        let mut value = valid_manifest();
        value["files"][0]["urls"] = serde_json::json!(["http://models.example.invalid/model.gguf"]);
        assert!(parse(&value).is_err());
    }

    #[test]
    fn duplicate_case_folded_paths_are_rejected() {
        let mut value = valid_manifest();
        let duplicate = value["files"][0].clone();
        value["files"]
            .as_array_mut()
            .expect("files")
            .push(duplicate);
        value["files"][1]["path"] = serde_json::json!("minicpm-O-4_5-q4_k_m.GGUF");
        assert!(parse(&value).is_err());
    }

    #[test]
    fn revision_and_manifest_digests_must_be_pinned() {
        for field in [
            "manifest_digest",
            "patch_set_digest",
            "upstream_runtime_revision",
        ] {
            let mut value = valid_manifest();
            value[field] = serde_json::json!("unpinned");
            assert!(parse(&value).is_err(), "{field} must be rejected");
        }
    }
}
