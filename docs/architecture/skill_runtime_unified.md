# Skill Runtime Unified Architecture

This repository now uses a single bundle-oriented skill runtime for non-deterministic
chat execution.

## Direct path

Deterministic requests stay in the runtime facade:

- weather
- time
- location / map
- system actions

## Bundle runtime path

All other conversational tool use should route through the lazy bundle runtime:

- `web-research`
- `screen-understanding`
- `news-intelligence`
- `document-editing`
- `terminal-agent`

## Canonical layout

Bundle definitions:

- `app/skills/bundles/<bundle_dir>/SKILL.md`
- `app/skills/bundles/<bundle_dir>/tools.json`
- `app/skills/bundles/<bundle_dir>/runtime.py`

Support layers:

- `app/lazy_skill_router/*`
- `app/lazy_runtime/lazy_dispatcher.py`
- `app/fairy_core.py`
- `app/web_access/*`
- `app/tools/*`

## Design constraints

- No main-chat imports from root-level `skills.*`
- No parallel chat routing through deprecated router / dispatcher layers
- Web browsing and screen understanding must route through bundle runtime
- User-visible naming should use canonical bundle ids instead of historical `*_skill`

## Current compatibility caveats

- Some runtime internals still use `legacy_surface` / `legacy_executor` naming for
  desktop automation compatibility. These are compatibility names, not canonical
  bundle identifiers.
- The root-level `skills/` directory may still exist on disk if sandbox deletion
  fails, but active imports must not reference it.
