use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::hardware_capabilities::{GpuVendor, HardwareCapabilityFacts};

#[cfg(target_os = "windows")]
mod windows;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HardwareAdapterReport {
    pub name: String,
    pub vendor: GpuVendor,
    pub vendor_id: u32,
    pub dedicated_vram_bytes: u64,
    pub budget_bytes: Option<u64>,
    pub current_usage_bytes: Option<u64>,
    pub luid: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CudaDriverReport {
    pub available: bool,
    pub driver_api_version: Option<i32>,
    pub driver_compatible: bool,
    pub device_count: u32,
    pub matched_device_ordinal: Option<u32>,
    pub adapter_luid_matches: bool,
    pub error_code: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HardwareProbeReport {
    pub schema_version: u16,
    pub windows_supported: bool,
    pub architecture_x64: bool,
    pub avx2_available: bool,
    pub system_total_bytes: Option<u64>,
    pub disk_available_bytes: Option<u64>,
    pub adapter: Option<HardwareAdapterReport>,
    pub cuda: CudaDriverReport,
    pub error_code: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RealtimeGpuMemoryReport {
    pub budget_bytes: Option<u64>,
    pub current_usage_bytes: Option<u64>,
    pub device_removed: bool,
    pub error_code: Option<String>,
}

impl HardwareProbeReport {
    pub fn populate_capability_facts(&self, facts: &mut HardwareCapabilityFacts) {
        facts.windows_supported = Some(self.windows_supported);
        facts.architecture_x64 = Some(self.architecture_x64);
        facts.gpu_vendor = self.adapter.as_ref().map(|adapter| adapter.vendor);
        facts.dedicated_vram_bytes = self
            .adapter
            .as_ref()
            .map(|adapter| adapter.dedicated_vram_bytes);
        facts.budget_bytes = self
            .adapter
            .as_ref()
            .and_then(|adapter| adapter.budget_bytes);
        facts.current_usage_bytes = self
            .adapter
            .as_ref()
            .and_then(|adapter| adapter.current_usage_bytes);
        facts.cuda_available = Some(self.cuda.available);
        facts.driver_compatible = Some(self.cuda.driver_compatible);
        facts.adapter_luid_matches = Some(self.cuda.adapter_luid_matches);
        facts.avx2_available = Some(self.avx2_available);
        facts.system_total_bytes = self.system_total_bytes;
        facts.disk_available_bytes = self.disk_available_bytes;
    }
}

pub fn probe_hardware(model_root: &Path) -> HardwareProbeReport {
    #[cfg(target_os = "windows")]
    {
        windows::probe(model_root)
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = model_root;
        HardwareProbeReport {
            schema_version: 1,
            windows_supported: false,
            architecture_x64: cfg!(target_arch = "x86_64"),
            avx2_available: false,
            system_total_bytes: None,
            disk_available_bytes: None,
            adapter: None,
            cuda: unavailable_cuda("CUDA_UNSUPPORTED_OS"),
            error_code: Some("LOCAL_BETA_UNSUPPORTED_OS".to_owned()),
        }
    }
}

pub fn sample_realtime_gpu_memory() -> RealtimeGpuMemoryReport {
    #[cfg(target_os = "windows")]
    {
        windows::sample_realtime_gpu_memory()
    }
    #[cfg(not(target_os = "windows"))]
    {
        RealtimeGpuMemoryReport {
            budget_bytes: None,
            current_usage_bytes: None,
            device_removed: false,
            error_code: Some("DXGI_UNSUPPORTED_OS".to_owned()),
        }
    }
}

fn unavailable_cuda(error_code: &str) -> CudaDriverReport {
    CudaDriverReport {
        available: false,
        driver_api_version: None,
        driver_compatible: false,
        device_count: 0,
        matched_device_ordinal: None,
        adapter_luid_matches: false,
        error_code: Some(error_code.to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_facts() -> HardwareCapabilityFacts {
        HardwareCapabilityFacts {
            windows_supported: None,
            architecture_x64: None,
            gpu_vendor: None,
            dedicated_vram_bytes: None,
            budget_bytes: None,
            current_usage_bytes: None,
            cuda_available: None,
            driver_compatible: None,
            adapter_luid_matches: None,
            avx2_available: None,
            system_total_bytes: None,
            disk_available_bytes: None,
            disk_required_bytes: None,
            model_installed: None,
            model_verified: None,
            runtime_installed: None,
            runtime_self_test_passed: None,
            runtime_quarantined: None,
            predicted_model_peak_bytes: None,
            runtime_headroom_bytes: 0,
        }
    }

    #[cfg(not(target_os = "windows"))]
    #[test]
    fn non_windows_probe_is_typed_and_fails_closed() {
        let report = probe_hardware(Path::new("."));
        assert!(!report.windows_supported);
        assert!(!report.cuda.available);
        assert_eq!(
            report.error_code.as_deref(),
            Some("LOCAL_BETA_UNSUPPORTED_OS")
        );
    }

    #[test]
    fn unavailable_cuda_never_implies_compatibility() {
        let report = unavailable_cuda("CUDA_DRIVER_MISSING");
        assert!(!report.available);
        assert!(!report.driver_compatible);
        assert!(!report.adapter_luid_matches);
    }

    #[test]
    fn probe_projection_changes_only_hardware_owned_facts() {
        let report = HardwareProbeReport {
            schema_version: 1,
            windows_supported: true,
            architecture_x64: true,
            avx2_available: true,
            system_total_bytes: Some(32),
            disk_available_bytes: Some(64),
            adapter: Some(HardwareAdapterReport {
                name: "NVIDIA".to_owned(),
                vendor: GpuVendor::Nvidia,
                vendor_id: 0x10de,
                dedicated_vram_bytes: 24,
                budget_bytes: Some(20),
                current_usage_bytes: Some(4),
                luid: "00000000:00000001".to_owned(),
            }),
            cuda: CudaDriverReport {
                available: true,
                driver_api_version: Some(12_080),
                driver_compatible: true,
                device_count: 1,
                matched_device_ordinal: Some(0),
                adapter_luid_matches: true,
                error_code: None,
            },
            error_code: None,
        };
        let mut facts = empty_facts();
        facts.model_installed = Some(true);
        facts.runtime_installed = Some(false);
        report.populate_capability_facts(&mut facts);

        assert_eq!(facts.gpu_vendor, Some(GpuVendor::Nvidia));
        assert_eq!(facts.budget_bytes, Some(20));
        assert_eq!(facts.model_installed, Some(true));
        assert_eq!(facts.runtime_installed, Some(false));
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_probe_returns_a_bounded_schema_without_starting_a_runtime() {
        let directory = tempfile::tempdir().expect("temporary model root");
        let report = probe_hardware(directory.path());
        assert_eq!(report.schema_version, 1);
        assert!(report.windows_supported);
        assert!(report.architecture_x64);
        if let Some(adapter) = report.adapter {
            assert!(!adapter.name.trim().is_empty());
            assert_eq!(adapter.luid.len(), 17);
        }
        assert!(report
            .cuda
            .matched_device_ordinal
            .is_none_or(|ordinal| { ordinal < report.cuda.device_count }));
    }
}
