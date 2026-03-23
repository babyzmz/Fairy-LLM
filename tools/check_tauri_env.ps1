$ErrorActionPreference = "Stop"

$workspace = "D:\×ÀÃæ\~\deskllmchat"
$env:RUSTUP_HOME = Join-Path $workspace ".rustup"
$env:CARGO_HOME = Join-Path $workspace ".cargo"
$env:PATH = (Join-Path $env:CARGO_HOME "bin") + ";" + $env:PATH

$llvmRoot = Join-Path $workspace "tools\llvm-mingw\llvm-mingw-20260311-msvcrt-x86_64"
$xwinRoot = Join-Path $workspace "tools\xwin-splat"

$env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = "rust-lld"
$env:CC_x86_64_pc_windows_msvc = Join-Path $workspace "tools\llvm-msvc-shim\cl.cmd"
$env:CXX_x86_64_pc_windows_msvc = $env:CC_x86_64_pc_windows_msvc
$env:AR_x86_64_pc_windows_msvc = Join-Path $llvmRoot "bin\llvm-lib.exe"
$env:RC_x86_64_pc_windows_msvc = Join-Path $llvmRoot "bin\llvm-rc.exe"
$env:LIB = @(
  (Join-Path $xwinRoot "crt\lib\x86_64")
  (Join-Path $xwinRoot "sdk\lib\ucrt\x86_64")
  (Join-Path $xwinRoot "sdk\lib\um\x86_64")
) -join ";"
$env:INCLUDE = @(
  (Join-Path $xwinRoot "crt\include")
  (Join-Path $xwinRoot "sdk\include\ucrt")
  (Join-Path $xwinRoot "sdk\include\shared")
  (Join-Path $xwinRoot "sdk\include\um")
  (Join-Path $xwinRoot "sdk\include\winrt")
  (Join-Path $xwinRoot "sdk\include\cppwinrt")
) -join ";"

Push-Location (Join-Path $workspace "fairy-desktop")
try {
  cargo check --offline --manifest-path src-tauri\Cargo.toml
} finally {
  Pop-Location
}
