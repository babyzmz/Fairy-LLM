# Fairy Eye brand icons

## Approved scope

Replace the History header ring and Windows application/titlebar/taskbar icon with
the existing Fairy-DSH eye, open and centered. Keep the header layout, text, chat
animation, pet renderer, preferences and sessions unchanged. Static icons create
no animation runtime, listeners, GPU loop or model consumers.

## Ownership and generation

The checked-in eye markup/geometry is the source. A reproducible desktop script
derives a self-contained static SVG and transparent multi-resolution Windows ICO.
No screenshots of user content, remote assets, credentials or new dependencies.
Existing DSH license/notice attribution is retained. Tauri retains its existing
icon path and tray default-window-icon behavior.

## Acceptance

- Asset tests: SVG exists; every local resource reference resolves; transient
  flicker/glitch is hidden; no scripts or animation elements; ICO includes small
  Windows/DPI sizes and 256px, with transparent corners and a blue eye (not a square).
- Run generation twice/check mode to detect source/artifact drift.
- TypeScript, affected Fairy Eye tests, and native debug resource compilation.
- Visually inspect rendered assets at 16/24/32/48/256px on dark/light backgrounds.
- Native dev: titlebar and History both show the eye; existing chat/pet unchanged.
  Windows Explorer/taskbar may retain an old cached icon; do not delete user caches.
- No release/Docker build. Close only test-owned processes after verification.

## Evidence

### Follow-up: small sidebar and blurry Windows titlebar

- User reproduced undersized History branding and blurry native titlebar branding.
  The header still reserved only 20px. Tauri 2.6.3 codegen `CachedIcon::new_ico`
  decodes only ICO entry zero (16px), not the current window DPI's entry.
- Increase the static header image to 32px. Derive optical small ICO variants
  (16–48px) without halo/scanlines, with a tighter crop; preserve the pet artwork.
- Load native small/big icons from the executable's multi-size resource at current
  Windows DPI and refresh on DPI changes. Own/destroy only newly loaded handles;
  leave Tauri-owned handles alone. No animation or periodic polling.
- Regression: rendered header dimensions, tight small-icon opaque bounds,
  resource loading at 96/120/144/192 DPI, and actual native HICON dimensions.
  Native screenshot acceptance remains required; physical cross-monitor movement
  is reported separately if unavailable.

- Publication recheck: TypeScript passed; WorkspaceShell/brandIcons/SettingsApp
  53 tests passed; native Windows ICO loader verified 96/120/144/192 DPI sizes.
  Generation `--check` matched; Clippy all targets passed. No native UI input was
  performed during publication; physical-DPI/titlebar visual checks remain pending.

- Generated `desktop/src/fairyEye/brand.svg` and the existing Tauri ICO from
  `createEyeMarkup`; the animation implementation itself is unchanged. Added
  `node scripts/generate-brand-icons.mjs [--check]` (desktop working directory).
- Red: missing static SVG and the old ICO containing only a 32px placeholder.
  Green: both asset tests pass, including decoded PNG alpha/color at all nine sizes.
  A test sampling point was corrected from the iris (.30) to sclera (.24); no
  existing assertion was modified for this task.
- `npx tsc --noEmit`: passed. Fairy Eye suite: 14 passed. WorkspaceShell/App:
  49 passed. Generation `--check`: byte-for-byte match.
- Full Vitest: 675 passed, 1 failed. The failure is the previously recorded
  `MessageList.outline.test.tsx` stale `Fairy is responding…` expectation versus
  `No reply recorded`, not introduced by the brand assets. Left outside this commit.
- Native first run reproduced a second issue: the sidebar eye was correct but
  Cargo reused the binary's old titlebar icon (0.51s, no recompilation). Added
  `cargo:rerun-if-changed=icons/icon.ico` to the existing build script.
- Second Tauri dev compiled successfully (49.42s). Foreground WebView2 inspection
  at 1440px confirmed the open-eye titlebar icon and 20px sidebar mark, transparent
  background, unchanged header spacing, and Core ready. The independent test data
  directory had no credentials/chats, so provider-unavailable was expected; no
  real user settings/database were changed. No capture consent or input needed.
- Visually inspected the generated 256px raster and native small icons on dark
  background. Full physical-DPI/light-theme/Explorer icon-cache acceptance was not
  performed; the ICO includes 16/20/24/32/40/48/64/128/256px variants for Windows.
- No release, Docker, dependency installation or remote upload. Earlier unfinished
  voice/provider repairs are preserved and excluded from this change's commit.
- Both owned native dev lifecycles were closed. Final process inspection found no
  project Fairy/Python/Cargo/Vite process and no listener on port 1430; Codex's own
  Computer Use Node processes were left untouched.
