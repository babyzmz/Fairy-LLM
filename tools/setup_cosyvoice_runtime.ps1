param(
    [string]$PythonPath = ".\cosyvoice_env\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$thirdPartyDir = Join-Path $projectRoot "third_party"
$cosyVoiceZip = Join-Path $thirdPartyDir "CosyVoice-main.zip"
$cosyVoiceDir = Join-Path $thirdPartyDir "CosyVoice"

if (-not (Test-Path $PythonPath)) {
    throw "Python runtime not found: $PythonPath"
}

if (-not (Test-Path $thirdPartyDir)) {
    New-Item -ItemType Directory -Path $thirdPartyDir | Out-Null
}

& $PythonPath -c @"
from pathlib import Path
from zipfile import ZipFile
import requests, shutil, sys
root = Path(sys.argv[1])
zip_path = root / 'CosyVoice-main.zip'
out_dir = root
if not zip_path.exists():
    resp = requests.get('https://codeload.github.com/FunAudioLLM/CosyVoice/zip/refs/heads/main', timeout=180)
    resp.raise_for_status()
    zip_path.write_bytes(resp.content)
with ZipFile(zip_path) as zf:
    zf.extractall(out_dir)
src = out_dir / 'CosyVoice-main'
dst = out_dir / 'CosyVoice'
if dst.exists():
    shutil.rmtree(dst)
src.rename(dst)
print(dst)
"@ $thirdPartyDir

& $PythonPath -c @"
import sys
sys.path.insert(0, r'$cosyVoiceDir')
sys.path.insert(0, r'$cosyVoiceDir\third_party\Matcha-TTS')
from cosyvoice.cli.cosyvoice import CosyVoice
print(CosyVoice)
"@
