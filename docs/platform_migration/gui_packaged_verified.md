# GUI Packaged Verification

- Date: 2026-03-23
- OS: Microsoft Windows NT 10.0.26200.0
- Executable: `D:\桌面\~\deskllmchat\fairy-desktop\src-tauri\target\release\fairy-desktop.exe`
- Primary report: [packaged_e2e_report.json](/D:/桌面/~/deskllmchat/data/reports/packaged_e2e_report.json)

## Build Mode

- Packaged GUI run completed against an existing packaged release binary.
- A rebuild was also attempted with `scripts/packaged_e2e_regression.py --build`.
- Rust release compilation succeeded and refreshed the packaged executable.
- Frontend `vite build` still hits an environment-specific `spawn EPERM`, so the build script fell back to the regression shell `dist` when `--build` was requested.

## Runtime Duration

- Real packaged GUI run completed for 20 minutes.
- Duration probe results:
  - `invoke_cycles = 61`
  - `stream_cycles = 61`
  - `cancel_cycles = 61`
  - `backend_healthy_after_probe = true`

## Verification Checklist

- GUI launch: verified
- Backend ready signal: verified
- Runtime session creation: verified
- `/chat/invoke`: verified
- `/chat/stream`: verified
- Cancellation: verified, best-effort cancel remained healthy after repeated aborts
- Multi-step orchestration: verified through normal packaged chat flow
- Timeline rendering: verified through packaged stream event sequence stability
- Long text / repeated sessions: verified during 20-minute run
- Desktop bridge actions:
  - `focus_window`: verified
  - `open_panel`: verified
  - `reveal_asset_folder`: verified
  - `restart_backend`: verified and backend recovered
- Assets:
  - map preview: verified through `/assets/local`
  - weather icon: verified through `/assets/local/weather/...`
  - news thumbnail fallback: verified through `/assets/local/news/generic-news.png`

## Lifecycle Notes

- Fresh packaged start: verified
  - `backend://starting -> backend://ready`
- Reused backend start: verified
  - `backend://reused -> backend://ready`
- Restart recovery: verified via desktop bridge action and post-restart health probe

The timeout and stopped synthetic lifecycle scenarios are not yet fully deterministic in the packaged regression harness on this workstation. The core packaged GUI path, backend spawn, reuse, restart recovery, long-running stability, invoke/stream/cancel, and asset delivery are verified.

## Qt Removal Readiness Impact

- Core packaged GUI path is independently runnable without Qt.
- Core chat, streaming, assets, and desktop-bridge system actions no longer require Qt.
- Remaining Qt retention reasons are limited to legacy modules and legacy surface automation, not the core packaged GUI runtime.
