use std::collections::HashSet;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

const MODEL_ID: &str = "openbmb/minicpm-o-4.5-fairy-beta";
const MODEL_REPOSITORY: &str = "openbmb/MiniCPM-o-4_5-gguf";
const MAX_FILE_SIZE: u64 = 16 * 1024 * 1024 * 1024;
const MAX_TOTAL_SIZE: u64 = 20 * 1024 * 1024 * 1024;
const REQUIRED_FILES: [&str; 3] = [
    "MiniCPM-o-4_5-Q4_K_M.gguf",
    "vision/MiniCPM-o-4_5-vision-F16.gguf",
    "audio/MiniCPM-o-4_5-audio-F16.gguf",
];

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
    pub model_revision: String,
    pub runtime_compatibility: String,
    pub license: String,
    pub predicted_peak_vram_mb: u64,
    pub upstream_runtime_revision: String,
    pub patch_set_digest: String,
    pub files: Vec<OmniModelFile>,
}

#[derive(Serialize)]
struct OmniModelManifestPayload<'a> {
    schema_version: u16,
    model_id: &'a str,
    version: &'a str,
    model_revision: &'a str,
    runtime_compatibility: &'a str,
    license: &'a str,
    predicted_peak_vram_mb: u64,
    upstream_runtime_revision: &'a str,
    patch_set_digest: &'a str,
    files: &'a [OmniModelFile],
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

    pub fn total_size(&self) -> u64 {
        self.files
            .iter()
            .fold(0_u64, |total, file| total.saturating_add(file.size))
    }

    pub fn computed_digest(&self) -> Result<String, ManifestError> {
        let payload = OmniModelManifestPayload {
            schema_version: self.schema_version,
            model_id: &self.model_id,
            version: &self.version,
            model_revision: &self.model_revision,
            runtime_compatibility: &self.runtime_compatibility,
            license: &self.license,
            predicted_peak_vram_mb: self.predicted_peak_vram_mb,
            upstream_runtime_revision: &self.upstream_runtime_revision,
            patch_set_digest: &self.patch_set_digest,
            files: &self.files,
        };
        let bytes = serde_json::to_vec(&payload).map_err(|_| ManifestError::Invalid)?;
        Ok(format!("{:x}", Sha256::digest(bytes)))
    }

    fn validate(&self) -> Result<(), ManifestError> {
        if self.schema_version != 1
            || self.model_id != MODEL_ID
            || self.version.trim().is_empty()
            || !is_lower_hex(&self.model_revision, 40)
            || self.runtime_compatibility != "fairy-omni-runtime-v1"
            || self.license != "Apache-2.0"
            || self.predicted_peak_vram_mb == 0
            || !is_lower_hex(&self.manifest_digest, 64)
            || !is_lower_hex(&self.upstream_runtime_revision, 40)
            || !is_lower_hex(&self.patch_set_digest, 64)
            || self.files.len() != REQUIRED_FILES.len()
            || self.computed_digest()? != self.manifest_digest
        {
            return Err(ManifestError::Invalid);
        }

        let mut paths = HashSet::new();
        let mut total_size = 0_u64;
        for file in &self.files {
            if !valid_relative_path(&file.path)
                || file.size == 0
                || file.size > MAX_FILE_SIZE
                || !is_lower_hex(&file.sha256, 64)
                || file.urls.is_empty()
                || file
                    .urls
                    .iter()
                    .any(|url| !valid_download_url(url, &self.model_revision, &file.path))
                || !paths.insert(file.path.to_lowercase())
            {
                return Err(ManifestError::Invalid);
            }
            total_size = total_size
                .checked_add(file.size)
                .ok_or(ManifestError::Invalid)?;
        }
        if total_size > MAX_TOTAL_SIZE
            || REQUIRED_FILES
                .iter()
                .any(|required| !paths.contains(&required.to_lowercase()))
        {
            return Err(ManifestError::Invalid);
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
    if value.trim().is_empty()
        || value.contains('\\')
        || value.contains(':')
        || value.bytes().any(|byte| byte.is_ascii_control())
    {
        return false;
    }
    value.split('/').all(valid_windows_component)
}

fn valid_windows_component(component: &str) -> bool {
    if component.is_empty()
        || component == "."
        || component == ".."
        || component.ends_with('.')
        || component.ends_with(' ')
    {
        return false;
    }
    let stem = component
        .split('.')
        .next()
        .unwrap_or(component)
        .to_ascii_uppercase();
    !matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        && !matches!(
            stem.as_str(),
            "COM1"
                | "COM2"
                | "COM3"
                | "COM4"
                | "COM5"
                | "COM6"
                | "COM7"
                | "COM8"
                | "COM9"
                | "LPT1"
                | "LPT2"
                | "LPT3"
                | "LPT4"
                | "LPT5"
                | "LPT6"
                | "LPT7"
                | "LPT8"
                | "LPT9"
        )
}

fn valid_download_url(url: &str, revision: &str, path: &str) -> bool {
    let prefix = format!("https://huggingface.co/{MODEL_REPOSITORY}/resolve/{revision}/");
    url == format!("{prefix}{path}") && !url.contains('?') && !url.contains('#')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_manifest() -> serde_json::Value {
        let mut value = serde_json::json!({
            "schema_version": 1,
            "manifest_digest": "0".repeat(64),
            "model_id": "openbmb/minicpm-o-4.5-fairy-beta",
            "version": "4.5-q4-1",
            "model_revision": "a".repeat(40),
            "runtime_compatibility": "fairy-omni-runtime-v1",
            "license": "Apache-2.0",
            "predicted_peak_vram_mb": 9500,
            "upstream_runtime_revision": "b".repeat(40),
            "patch_set_digest": "c".repeat(64),
            "files": [
                {
                    "path": "MiniCPM-o-4_5-Q4_K_M.gguf",
                    "size": 5_026_714_400_u64,
                    "sha256": "d".repeat(64),
                    "urls": [format!(
                        "https://huggingface.co/{MODEL_REPOSITORY}/resolve/{}/MiniCPM-o-4_5-Q4_K_M.gguf",
                        "a".repeat(40)
                    )]
                },
                {
                    "path": "vision/MiniCPM-o-4_5-vision-F16.gguf",
                    "size": 1_095_113_184_u64,
                    "sha256": "e".repeat(64),
                    "urls": [format!(
                        "https://huggingface.co/{MODEL_REPOSITORY}/resolve/{}/vision/MiniCPM-o-4_5-vision-F16.gguf",
                        "a".repeat(40)
                    )]
                },
                {
                    "path": "audio/MiniCPM-o-4_5-audio-F16.gguf",
                    "size": 660_167_904_u64,
                    "sha256": "f".repeat(64),
                    "urls": [format!(
                        "https://huggingface.co/{MODEL_REPOSITORY}/resolve/{}/audio/MiniCPM-o-4_5-audio-F16.gguf",
                        "a".repeat(40)
                    )]
                }
            ]
        });
        sign(&mut value);
        value
    }

    fn parse(value: &serde_json::Value) -> Result<OmniModelManifest, ManifestError> {
        OmniModelManifest::parse_and_validate(
            serde_json::to_vec(value)
                .expect("serialize manifest")
                .as_slice(),
        )
    }

    fn sign(value: &mut serde_json::Value) {
        let manifest: OmniModelManifest =
            serde_json::from_value(value.clone()).expect("deserialize unsigned manifest");
        value["manifest_digest"] =
            serde_json::json!(manifest.computed_digest().expect("compute digest"));
    }

    fn parse_signed(value: &mut serde_json::Value) -> Result<OmniModelManifest, ManifestError> {
        sign(value);
        parse(value)
    }

    #[test]
    fn valid_manifest_is_accepted() {
        let parsed = parse(&valid_manifest()).expect("valid manifest");
        assert_eq!(parsed.predicted_peak_vram_mb, 9500);
        assert_eq!(parsed.files.len(), 3);
        assert_eq!(parsed.total_size(), 6_781_995_488);
    }

    #[test]
    fn manifest_digest_covers_every_payload_field() {
        let original = valid_manifest();
        let mut tampered = original.clone();
        tampered["files"][0]["size"] = serde_json::json!(1);
        assert!(parse(&tampered).is_err());

        let mut resigned = tampered;
        assert!(parse_signed(&mut resigned).is_ok());
    }

    #[test]
    fn unsafe_or_ambiguous_paths_are_rejected() {
        for path in [
            "../model.gguf",
            "C:/model.gguf",
            "/model.gguf",
            "",
            "audio/../model.gguf",
            "audio\\model.gguf",
            "audio/model.gguf:stream",
            "audio/CON.gguf",
            "audio/model.gguf.",
            "audio/model.gguf ",
        ] {
            let mut value = valid_manifest();
            value["files"][0]["path"] = serde_json::json!(path);
            value["files"][0]["urls"][0] = serde_json::json!(format!(
                "https://huggingface.co/{MODEL_REPOSITORY}/resolve/{}/{path}",
                "a".repeat(40)
            ));
            assert!(parse_signed(&mut value).is_err(), "{path} must be rejected");
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
            assert!(
                parse_signed(&mut value).is_err(),
                "{field} must be rejected"
            );
        }

        let mut value = valid_manifest();
        value["files"][0]["urls"] = serde_json::json!(["http://models.example.invalid/model.gguf"]);
        assert!(parse_signed(&mut value).is_err());

        let mut value = valid_manifest();
        value["files"][0]["size"] = serde_json::json!(MAX_FILE_SIZE + 1);
        assert!(parse_signed(&mut value).is_err());
    }

    #[test]
    fn duplicate_case_folded_paths_are_rejected() {
        let mut value = valid_manifest();
        value["files"][1]["path"] = serde_json::json!("minicpm-O-4_5-q4_k_m.GGUF");
        value["files"][1]["urls"][0] = serde_json::json!(format!(
            "https://huggingface.co/{MODEL_REPOSITORY}/resolve/{}/minicpm-O-4_5-q4_k_m.GGUF",
            "a".repeat(40)
        ));
        assert!(parse_signed(&mut value).is_err());
    }

    #[test]
    fn exact_beta_file_set_and_pinned_urls_are_required() {
        let mut value = valid_manifest();
        value["files"][2]["path"] = serde_json::json!("tts/token2wav.gguf");
        value["files"][2]["urls"][0] = serde_json::json!(format!(
            "https://huggingface.co/{MODEL_REPOSITORY}/resolve/{}/tts/token2wav.gguf",
            "a".repeat(40)
        ));
        assert!(parse_signed(&mut value).is_err());

        let mut value = valid_manifest();
        value["files"][0]["urls"][0] = serde_json::json!(format!(
            "https://huggingface.co/{MODEL_REPOSITORY}/resolve/main/MiniCPM-o-4_5-Q4_K_M.gguf"
        ));
        assert!(parse_signed(&mut value).is_err());
    }

    #[test]
    fn revision_and_manifest_digests_must_be_pinned() {
        for field in [
            "patch_set_digest",
            "upstream_runtime_revision",
            "model_revision",
        ] {
            let mut value = valid_manifest();
            value[field] = serde_json::json!("unpinned");
            assert!(
                parse_signed(&mut value).is_err(),
                "{field} must be rejected"
            );
        }
    }
}
