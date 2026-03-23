# Fairy Desktop

Minimal Phase 2 desktop shell for Fairy.

## Dev startup

1. You can still start the Python backend manually from the project root:

```powershell
uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000
```

2. Start the frontend shell:

```powershell
cd fairy-desktop
npm install
npm run tauri:dev
```

Tauri will try to start the backend automatically on port `8000` if it is not already running.

If you only want the web UI during development:

```powershell
cd fairy-desktop
npm install
npm run dev
```

## Environment

Optional frontend env:

```powershell
$env:VITE_FAIRY_API_BASE_URL='http://127.0.0.1:8000'
```

## Notes

- The Qt shell is still kept in the main application.
- This frontend only consumes the backend contract and does not execute tools locally.
- Local backend asset paths are displayed through the API asset adapter route.
- The default chat path is `/chat/stream`, with `/chat/invoke` as fallback.

## Windows local toolchain

For this workspace, the Rust/Tauri build can be checked with:

```powershell
D:\×ÀÃæ\~\deskllmchat\tools\check_tauri_env.ps1
```
