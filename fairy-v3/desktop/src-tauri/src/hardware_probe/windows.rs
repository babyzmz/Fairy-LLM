use std::mem::{size_of, zeroed};
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use windows::core::{s, w, Interface, PCSTR};
use windows::Win32::Foundation::{FreeLibrary, HMODULE, LUID};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, IDXGIAdapter1, IDXGIAdapter3, IDXGIFactory1, IDXGIFactory6,
    DXGI_ADAPTER_FLAG_SOFTWARE, DXGI_ERROR_NOT_FOUND, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
    DXGI_MEMORY_SEGMENT_GROUP_LOCAL, DXGI_QUERY_VIDEO_MEMORY_INFO,
};
use windows::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryW};
use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};

use super::{
    unavailable_cuda, CudaDriverReport, HardwareAdapterReport, HardwareProbeReport,
    RealtimeGpuMemoryReport,
};
use crate::hardware_capabilities::GpuVendor;

const NVIDIA_VENDOR_ID: u32 = 0x10de;
const MIN_CUDA_DRIVER_API_VERSION: i32 = 12_000;

#[derive(Clone, Debug, Eq, PartialEq)]
struct AdapterCandidate {
    name: String,
    vendor_id: u32,
    dedicated_vram_bytes: u64,
    budget_bytes: Option<u64>,
    current_usage_bytes: Option<u64>,
    luid: String,
    software: bool,
}

pub(super) fn probe(model_root: &Path) -> HardwareProbeReport {
    let windows_supported = windows_10_or_later();
    let architecture_x64 = cfg!(target_arch = "x86_64");
    let avx2_available = avx2_available();
    let system_total_bytes = system_total_bytes();
    let disk_available_bytes = disk_available_bytes(model_root);

    let (adapter, adapter_error) = match enumerate_adapters() {
        Ok(candidates) => (
            select_adapter(&candidates).map(candidate_report),
            if candidates.is_empty() {
                Some("DXGI_ADAPTER_NOT_FOUND".to_owned())
            } else {
                None
            },
        ),
        Err(code) => (None, Some(code.to_owned())),
    };
    let selected_luid = adapter
        .as_ref()
        .and_then(|selected| parse_luid(&selected.luid));
    let cuda = probe_cuda(selected_luid);

    HardwareProbeReport {
        schema_version: 1,
        windows_supported,
        architecture_x64,
        avx2_available,
        system_total_bytes,
        disk_available_bytes,
        adapter,
        cuda,
        error_code: adapter_error,
    }
}

pub(super) fn sample_realtime_gpu_memory() -> RealtimeGpuMemoryReport {
    match enumerate_adapters() {
        Ok(candidates) => {
            let Some(adapter) = select_adapter(&candidates) else {
                return RealtimeGpuMemoryReport {
                    budget_bytes: None,
                    current_usage_bytes: None,
                    device_removed: true,
                    error_code: Some("DXGI_ADAPTER_REMOVED".to_owned()),
                };
            };
            let complete = adapter.budget_bytes.is_some() && adapter.current_usage_bytes.is_some();
            RealtimeGpuMemoryReport {
                budget_bytes: adapter.budget_bytes,
                current_usage_bytes: adapter.current_usage_bytes,
                device_removed: !complete,
                error_code: (!complete).then(|| "DXGI_MEMORY_ACCESS_LOST".to_owned()),
            }
        }
        Err(code) => RealtimeGpuMemoryReport {
            budget_bytes: None,
            current_usage_bytes: None,
            device_removed: true,
            error_code: Some(code.to_owned()),
        },
    }
}

fn enumerate_adapters() -> Result<Vec<AdapterCandidate>, &'static str> {
    let factory: IDXGIFactory1 =
        unsafe { CreateDXGIFactory1() }.map_err(|_| "DXGI_FACTORY_UNAVAILABLE")?;
    if let Ok(factory6) = factory.cast::<IDXGIFactory6>() {
        enumerate_high_performance(&factory6)
    } else {
        enumerate_legacy(&factory)
    }
}

fn enumerate_high_performance(
    factory: &IDXGIFactory6,
) -> Result<Vec<AdapterCandidate>, &'static str> {
    let mut candidates = Vec::new();
    for index in 0..64 {
        let adapter: IDXGIAdapter1 = match unsafe {
            factory.EnumAdapterByGpuPreference(index, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE)
        } {
            Ok(adapter) => adapter,
            Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(_) => return Err("DXGI_ADAPTER_ENUM_FAILED"),
        };
        candidates.push(read_candidate(&adapter)?);
    }
    Ok(candidates)
}

fn enumerate_legacy(factory: &IDXGIFactory1) -> Result<Vec<AdapterCandidate>, &'static str> {
    let mut candidates = Vec::new();
    for index in 0..64 {
        let adapter = match unsafe { factory.EnumAdapters1(index) } {
            Ok(adapter) => adapter,
            Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(_) => return Err("DXGI_ADAPTER_ENUM_FAILED"),
        };
        candidates.push(read_candidate(&adapter)?);
    }
    Ok(candidates)
}

fn read_candidate(adapter: &IDXGIAdapter1) -> Result<AdapterCandidate, &'static str> {
    let description =
        unsafe { adapter.GetDesc1() }.map_err(|_| "DXGI_ADAPTER_DESCRIPTION_FAILED")?;
    let (budget_bytes, current_usage_bytes) = adapter
        .cast::<IDXGIAdapter3>()
        .ok()
        .and_then(|adapter3| {
            let mut info = DXGI_QUERY_VIDEO_MEMORY_INFO::default();
            unsafe {
                adapter3
                    .QueryVideoMemoryInfo(0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &mut info)
                    .ok()
            }
            .map(|()| (Some(info.Budget), Some(info.CurrentUsage)))
        })
        .unwrap_or((None, None));
    Ok(AdapterCandidate {
        name: utf16z(&description.Description),
        vendor_id: description.VendorId,
        dedicated_vram_bytes: description.DedicatedVideoMemory as u64,
        budget_bytes,
        current_usage_bytes,
        luid: format_luid(description.AdapterLuid),
        software: description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE.0 as u32 != 0,
    })
}

fn select_adapter(candidates: &[AdapterCandidate]) -> Option<AdapterCandidate> {
    candidates
        .iter()
        .find(|candidate| !candidate.software && candidate.vendor_id == NVIDIA_VENDOR_ID)
        .or_else(|| candidates.iter().find(|candidate| !candidate.software))
        .cloned()
}

fn candidate_report(candidate: AdapterCandidate) -> HardwareAdapterReport {
    HardwareAdapterReport {
        name: candidate.name,
        vendor: vendor(candidate.vendor_id),
        vendor_id: candidate.vendor_id,
        dedicated_vram_bytes: candidate.dedicated_vram_bytes,
        budget_bytes: candidate.budget_bytes,
        current_usage_bytes: candidate.current_usage_bytes,
        luid: candidate.luid,
    }
}

fn vendor(vendor_id: u32) -> GpuVendor {
    match vendor_id {
        NVIDIA_VENDOR_ID => GpuVendor::Nvidia,
        0x1002 | 0x1022 => GpuVendor::Amd,
        0x8086 => GpuVendor::Intel,
        _ => GpuVendor::Other,
    }
}

fn probe_cuda(selected_luid: Option<[u8; 8]>) -> CudaDriverReport {
    let module = match unsafe { LoadLibraryW(w!("nvcuda.dll")) } {
        Ok(module) => Library(module),
        Err(_) => return unavailable_cuda("CUDA_DRIVER_MISSING"),
    };
    let Some(cu_init) = (unsafe { symbol::<CuInit>(module.0, s!("cuInit")) }) else {
        return unavailable_cuda("CUDA_SYMBOL_MISSING");
    };
    let Some(cu_driver_get_version) =
        (unsafe { symbol::<CuDriverGetVersion>(module.0, s!("cuDriverGetVersion")) })
    else {
        return unavailable_cuda("CUDA_SYMBOL_MISSING");
    };
    let Some(cu_device_get_count) =
        (unsafe { symbol::<CuDeviceGetCount>(module.0, s!("cuDeviceGetCount")) })
    else {
        return unavailable_cuda("CUDA_SYMBOL_MISSING");
    };
    let Some(cu_device_get_luid) =
        (unsafe { symbol::<CuDeviceGetLuid>(module.0, s!("cuDeviceGetLuid")) })
    else {
        return unavailable_cuda("CUDA_LUID_UNAVAILABLE");
    };

    if unsafe { cu_init(0) } != 0 {
        return unavailable_cuda("CUDA_INITIALIZATION_FAILED");
    }
    let mut driver_api_version = 0_i32;
    if unsafe { cu_driver_get_version(&mut driver_api_version) } != 0 {
        return unavailable_cuda("CUDA_DRIVER_VERSION_FAILED");
    }
    let mut count = 0_i32;
    if unsafe { cu_device_get_count(&mut count) } != 0 || count <= 0 {
        return CudaDriverReport {
            available: true,
            driver_api_version: Some(driver_api_version),
            driver_compatible: driver_api_version >= MIN_CUDA_DRIVER_API_VERSION,
            device_count: 0,
            matched_device_ordinal: None,
            adapter_luid_matches: false,
            error_code: Some("CUDA_DEVICE_NOT_FOUND".to_owned()),
        };
    }

    let matched_device_ordinal = selected_luid.and_then(|expected| {
        (0..count).find_map(|ordinal| {
            let mut luid = [0_i8; 8];
            let mut node_mask = 0_u32;
            if unsafe { cu_device_get_luid(luid.as_mut_ptr(), &mut node_mask, ordinal) } != 0 {
                return None;
            }
            let actual = luid.map(|byte| byte as u8);
            (actual == expected).then_some(ordinal as u32)
        })
    });
    let adapter_luid_matches = matched_device_ordinal.is_some();
    CudaDriverReport {
        available: true,
        driver_api_version: Some(driver_api_version),
        driver_compatible: driver_api_version >= MIN_CUDA_DRIVER_API_VERSION,
        device_count: count as u32,
        matched_device_ordinal,
        adapter_luid_matches,
        error_code: (!adapter_luid_matches).then(|| "CUDA_DXGI_ADAPTER_MISMATCH".to_owned()),
    }
}

type CuInit = unsafe extern "system" fn(u32) -> i32;
type CuDriverGetVersion = unsafe extern "system" fn(*mut i32) -> i32;
type CuDeviceGetCount = unsafe extern "system" fn(*mut i32) -> i32;
type CuDeviceGetLuid = unsafe extern "system" fn(*mut i8, *mut u32, i32) -> i32;
type RtlGetVersion = unsafe extern "system" fn(*mut RtlOsVersionInfo) -> i32;

unsafe fn symbol<T: Copy>(module: HMODULE, name: PCSTR) -> Option<T> {
    let procedure = unsafe { GetProcAddress(module, name) }?;
    debug_assert_eq!(size_of::<T>(), std::mem::size_of_val(&procedure));
    Some(unsafe { std::mem::transmute_copy(&procedure) })
}

struct Library(HMODULE);

impl Drop for Library {
    fn drop(&mut self) {
        let _ = unsafe { FreeLibrary(self.0) };
    }
}

#[repr(C)]
#[allow(non_snake_case)]
struct RtlOsVersionInfo {
    dwOSVersionInfoSize: u32,
    dwMajorVersion: u32,
    dwMinorVersion: u32,
    dwBuildNumber: u32,
    dwPlatformId: u32,
    szCSDVersion: [u16; 128],
}

fn windows_10_or_later() -> bool {
    let module = match unsafe { LoadLibraryW(w!("ntdll.dll")) } {
        Ok(module) => Library(module),
        Err(_) => return false,
    };
    let Some(rtl_get_version) = (unsafe { symbol::<RtlGetVersion>(module.0, s!("RtlGetVersion")) })
    else {
        return false;
    };
    let mut version: RtlOsVersionInfo = unsafe { zeroed() };
    version.dwOSVersionInfoSize = size_of::<RtlOsVersionInfo>() as u32;
    (unsafe { rtl_get_version(&mut version) }) >= 0 && version.dwMajorVersion >= 10
}

fn avx2_available() -> bool {
    #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
    {
        std::is_x86_feature_detected!("avx2")
    }
    #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
    {
        false
    }
}

fn system_total_bytes() -> Option<u64> {
    let mut status: MEMORYSTATUSEX = unsafe { zeroed() };
    status.dwLength = size_of::<MEMORYSTATUSEX>() as u32;
    (unsafe { GlobalMemoryStatusEx(&mut status) } != 0).then_some(status.ullTotalPhys)
}

fn disk_available_bytes(model_root: &Path) -> Option<u64> {
    let existing = nearest_existing_ancestor(model_root)?;
    let wide: Vec<u16> = existing
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let mut available = 0_u64;
    let mut total = 0_u64;
    let mut free = 0_u64;
    (unsafe { GetDiskFreeSpaceExW(wide.as_ptr(), &mut available, &mut total, &mut free) } != 0)
        .then_some(available)
}

fn nearest_existing_ancestor(path: &Path) -> Option<PathBuf> {
    let mut candidate = path.to_path_buf();
    loop {
        if candidate.exists() {
            return Some(candidate);
        }
        if !candidate.pop() {
            return None;
        }
    }
}

fn luid_bytes(luid: LUID) -> [u8; 8] {
    let mut bytes = [0_u8; 8];
    bytes[..4].copy_from_slice(&luid.LowPart.to_le_bytes());
    bytes[4..].copy_from_slice(&(luid.HighPart as u32).to_le_bytes());
    bytes
}

fn format_luid(luid: LUID) -> String {
    format!("{:08x}:{:08x}", luid.HighPart as u32, luid.LowPart)
}

fn parse_luid(value: &str) -> Option<[u8; 8]> {
    let (high, low) = value.split_once(':')?;
    let high = u32::from_str_radix(high, 16).ok()?;
    let low = u32::from_str_radix(low, 16).ok()?;
    Some(luid_bytes(LUID {
        LowPart: low,
        HighPart: high as i32,
    }))
}

fn utf16z(value: &[u16]) -> String {
    let length = value
        .iter()
        .position(|character| *character == 0)
        .unwrap_or(value.len());
    String::from_utf16_lossy(&value[..length])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(vendor_id: u32, software: bool, name: &str) -> AdapterCandidate {
        AdapterCandidate {
            name: name.to_owned(),
            vendor_id,
            dedicated_vram_bytes: 16 * 1024 * 1024 * 1024,
            budget_bytes: Some(14 * 1024 * 1024 * 1024),
            current_usage_bytes: Some(2 * 1024 * 1024 * 1024),
            luid: "01010101:01010101".to_owned(),
            software,
        }
    }

    #[test]
    fn adapter_selection_prefers_the_first_hardware_nvidia_candidate() {
        let candidates = [
            candidate(NVIDIA_VENDOR_ID, true, "software"),
            candidate(0x8086, false, "integrated"),
            candidate(NVIDIA_VENDOR_ID, false, "nvidia-1"),
            candidate(NVIDIA_VENDOR_ID, false, "nvidia-2"),
        ];
        assert_eq!(
            select_adapter(&candidates).map(|adapter| adapter.name),
            Some("nvidia-1".to_owned())
        );
    }

    #[test]
    fn unsupported_hardware_still_reports_the_first_real_adapter() {
        let candidates = [
            candidate(NVIDIA_VENDOR_ID, true, "software"),
            candidate(0x8086, false, "integrated"),
            candidate(0x1002, false, "amd"),
        ];
        assert_eq!(
            select_adapter(&candidates).map(|adapter| adapter.name),
            Some("integrated".to_owned())
        );
    }

    #[test]
    fn luid_format_round_trips_the_raw_windows_identity() {
        let luid = LUID {
            LowPart: 0x1122_3344,
            HighPart: 0x5566_7788_i32,
        };
        let formatted = format_luid(luid);
        assert_eq!(parse_luid(&formatted), Some(luid_bytes(luid)));
    }

    #[test]
    fn known_gpu_vendors_are_bounded() {
        assert_eq!(vendor(NVIDIA_VENDOR_ID), GpuVendor::Nvidia);
        assert_eq!(vendor(0x1002), GpuVendor::Amd);
        assert_eq!(vendor(0x8086), GpuVendor::Intel);
        assert_eq!(vendor(0xffff), GpuVendor::Other);
    }
}
