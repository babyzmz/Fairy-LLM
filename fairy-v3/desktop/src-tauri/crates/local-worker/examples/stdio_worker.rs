//! Lightweight native acceptance entrypoint; no Tauri window or desktop runtime.

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let managed_root = std::env::var_os("FAIRY_MANAGED_ROOT")
        .map(std::path::PathBuf::from)
        .ok_or("FAIRY_MANAGED_ROOT is required")?;
    fairy_local_worker::run_stdio(managed_root)?;
    Ok(())
}
