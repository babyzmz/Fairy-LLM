use crate::omni_model_manifest::{ManifestError, OmniModelManifest};

const BUNDLED_MINICPM_O45_MANIFEST: &[u8] = include_bytes!("../resources/omni/minicpm-o-4.5.json");

pub fn bundled_minicpm_o45_manifest() -> Result<OmniModelManifest, ManifestError> {
    OmniModelManifest::parse_and_validate(BUNDLED_MINICPM_O45_MANIFEST)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_manifest_is_valid_and_revision_pinned() {
        let manifest = bundled_minicpm_o45_manifest().expect("bundled manifest");
        assert_eq!(
            manifest.model_revision,
            "502eec5b03eaee9d0d2ce17a176e3490103c9a63"
        );
        assert_eq!(
            manifest.upstream_runtime_revision,
            "74699a53df6ca0f4947ff37066f851532c20b12d"
        );
        assert_eq!(manifest.total_size(), 6_781_995_488);
    }

    #[test]
    fn bundled_manifest_excludes_tts_artifacts() {
        let manifest = bundled_minicpm_o45_manifest().expect("bundled manifest");
        assert!(manifest.files.iter().all(|file| {
            let path = file.path.to_ascii_lowercase();
            !path.contains("tts") && !path.contains("token2wav") && !path.contains("reference")
        }));
    }
}
