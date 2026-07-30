# Realtime Companion Beta Troubleshooting

Fairy surfaces stable public reasons. Do not paste API keys, full transcripts,
prompts, screenshots, audio, or raw provider responses into a diagnostic
report.

## Local readiness

| Public reason | Meaning | Safe action |
| --- | --- | --- |
| `unsupported_os` / `unsupported_architecture` | Local Beta requires Windows 10/11 x64 | Use Cloud when configured |
| `unsupported_vendor` | The selected adapter is not a qualifying NVIDIA GPU | Use Cloud when configured |
| `vram_below12gb` | Dedicated VRAM is below the 12 GB Beta hardware floor | Use Cloud; CPU fallback is not Local Beta |
| `avx2_unavailable` | CPU lacks the required instruction support | Use Cloud |
| `cuda_unavailable` / `driver_incompatible` | CUDA driver initialization or compatibility failed | Update the NVIDIA driver, restart, and Verify again |
| `adapter_mismatch` | CUDA and DXGI did not identify the same adapter | Select the correct GPU or update drivers |
| `model_missing` | Managed MiniCPM-o files are not installed | Use the Settings install flow |
| `model_verification_failed` | Size, hash, or layout validation failed | Retry the managed install; do not rename files manually |
| `runtime_missing` | Production Omni runtime is absent | Repair or reinstall Fairy |
| `self_test_failed` | Runtime integrity or protocol self-test failed | Verify again; use Cloud if the failure persists |
| `insufficient_free_vram` | Current applications leave less than the verified model peak plus runtime headroom | Close GPU-heavy apps, refresh, or use Cloud |
| `insufficient_disk` | Download/install reserve is too small | Free disk space and retry |
| `runtime_quarantined` | The local Sidecar failed twice | Use Verify and retry only after checking driver/runtime health |

## Active Session

| State or code | Safe action |
| --- | --- |
| GPU pressure/high | Fairy reduces visual cadence; close GPU-heavy work if responses degrade |
| GPU critical | Local media pauses; close GPU-heavy work or explicitly continue through Cloud |
| device removed | The local Segment ends; update/restart the driver before retrying |
| selected window closed | Choose a new window; microphone conversation can remain available |
| application audio failed | Re-select the application audio source; microphone and video can continue |
| microphone failed | Re-select the microphone; visual observation can continue |
| Fairy Voice failed | Continue with text and repair Voice from Settings |
| Core Assistance failed | Reopen the linked main chat or retry the request there |

One local Sidecar failure can create one recovery Segment. A second failure is
quarantined; Fairy does not loop, switch to CPU, or silently switch to Cloud.

## Cloud categories

Authentication errors require updating the provider credential through
Fairy's secure credential flow. Quota/rate categories require waiting or
changing the provider in Settings. Network interruption ends the current
Cloud Segment and offers an explicit retry. Provider response bodies and keys
are never required for ordinary diagnosis.

## What to include in a support report

Include only the Fairy version, stable public error code, Backend, profile,
whether Verify passed, total VRAM class, and the approximate time of failure.
Do not include content captured or spoken during a Session.
