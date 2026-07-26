# Fairy LLM

Fairy is a local-first AI workspace. The supported desktop application is
Fairy V3, built with Tauri, React, and the modular Fairy Core.

## Active project

- Project root: `fairy-v3/`
- Desktop shell: `fairy-v3/desktop/`
- Local Core: `fairy-v3/core/`
- Capabilities: `fairy-v3/capabilities/`
- Optional cloud services: `fairy-v3/cloud/`

Development launch:

```powershell
cd fairy-v3/desktop
npm install
npm run tauri -- dev
```

The former Python/Qt desktop shell has been removed. Historical migration
records remain under `docs/history/` and `docs/platform_migration/`.
