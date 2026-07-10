# Fairy V3 Legacy Intake

This inventory consolidates the completed legacy task reviews on 2026-07-10.
It is intentionally asymmetric: V3 may copy approved media and rewrite proven
behavior, but it must not import or transplant legacy implementation modules.

## Intake rules

1. Copy only approved character, icon, and voice media into `fairy-v3/resources`.
2. Re-express useful behavior through V3 contracts and acceptance tests.
3. Treat legacy Python, Qt, React, routing, persistence, and database code as
   black-box references only.
4. Never add a runtime import from V3 into root `app`, `fairy-desktop`,
   `skills`, `src`, `lazy_runtime`, or `legacy_surface` paths.
5. Do not migrate a legacy database. V3 owns new schemas and migrations.
6. A missing or uncommitted legacy experiment is design evidence, not source
   code to reconstruct line for line.

## Approved intake

| Area | Copy as asset | Rebuild as behavior | Black-box acceptance |
| --- | --- | --- | --- |
| Presence | Existing Fairy visual media only | Separate Presence and Workspace windows; avatar hover input; 5 px drag threshold; 20 px edge snap; position and Quiet Mode persistence; dismissible non-critical notices; closable reply bubble | Multi-monitor placement, open/hide Workspace, hover input, notice dismissal, reply bubble retention, reduced motion |
| Companion | Doctrine documents and character copy | Ambient Presence derived from durable visible state; scene, density, AFK, and attending projections; Companion never calls an LLM or owns project state | Presence protocol, density governor, scene state machine, short-memory policy, passive screen watcher boundaries |
| Voice | `fairy_clone_core.wav` and matching transcript after license/provenance recording | A dedicated voice service; sentence-boundary TTS queue driven by public text deltas; cancellation and duplicate suppression; standard WAV contract | STT/TTS health, first-audio latency, ordered chunks, cancellation, disabled-device behavior |
| Web research | No legacy code | Search/fetch primitives, research execution, and evidence synthesis as three layers; provider diagnostics; canonical `specs`, `compare`, `release`, and `web_brief` artifacts | URL and redirect safety, cache policy, binary handling, evidence citations, provider fallback, no-tool direct answer |
| Assistant routing | Persona copy and Doctrine only | Model-led intent understanding, tool-necessity judgment, candidate tools, and explicit fallback; `direct_answer` is always available | Weather, news, project memory, system status, ordinary chat, empty/failed tool fallback, no keyword-forced route |
| Local models | No model weight in Git | Provider profiles for local and OpenAI-compatible models; health and capability negotiation | Local unavailable, fallback, cancellation, streaming, model asset missing |
| Desktop surfaces | Approved icons and character media only | Tauri Workspace, Presence, Guide, and runtime supervision with command/projection boundaries | Window capability isolation, click-through Guide that never clicks, Windows scaling, offline mode |

## Explicitly rejected

- Qt UI modules, including the monolithic `app/ui/desktop_pet.py` path.
- Legacy `skill_router.py`, `app/core/skill_router`, `lazy_skill_router`, root
  `skills`, and ad-hoc keyword routing.
- Parallel web implementations under `app/agents/web_research`,
  `app/skills/bundles/web_research`, and `app/web_access` as a set. V3 gets one
  implementation behind ports.
- Legacy `fairy_core.py`, `api/dependencies.py`, and UI stores as architectural
  templates; each combines too many responsibilities.
- `lazy_runtime`, `legacy_surface`, old invocation-service compatibility, host
  automation fallbacks, and direct host shell access.
- The deleted root `src` and `fairy-desktop-v2` experiments.
- Old card names, migration notes, mojibake strings, runtime caches, temporary
  vendor directories, local environments, and generated model artifacts.
- Browser `speechSynthesis` as the product TTS contract and global Python
  environments that mix CosyVoice with application dependencies.

## V3 ownership map

```text
fairy-v3/
  core/
    src/fairy_core/        Domain, application services, ports, adapters
    tests/                 Core contracts, security, recovery, SQLite
  cloud/
    src/fairy_cloud/       REST/SSE, auth, sync, storage, Outbox Worker
    migrations/            PostgreSQL schema authority
    tests/integration/     Real PostgreSQL and S3 gates
  desktop/
    src/                   React app and shared CoreClient transports
    src-tauri/             Tauri host, Core bridge, Rust local worker
    e2e/                   Desktop workflow tests
  contracts/               Generated public API artifacts only
  resources/               Provenanced character, icon, and voice media
  scripts/                 Boundary, contract, and full verification gates
  tools/contracts/         Isolated OpenAPI TypeScript generator
  docs/                    Current architecture, ADRs, plans, and intake
```

Tests remain package-owned until a genuinely cross-package executable suite
exists. Empty future-facing directory trees are not kept as placeholders.

Within the desktop renderer, Presence owns one domain boundary. Its avatar,
reply bubble, projections, and hooks must not be split between generic
components, pet, and companion folders. Voice owns one boundary and exposes no
duplicate runtime wrappers.

Within Core, each external dependency is reached through a port. Storage,
command execution, model providers, web access, voice, memory, previews, and
artifacts must not be wired from domain modules.

## Provenance to record before copying media

For every copied asset, add a manifest entry with source path, content hash,
license or user-ownership assertion, intended use, and destination. Large
model weights remain external downloads and are never committed.

The first approved voice candidate is:

- source: `app/ai/voice/fairy_clone_core.wav`
- transcript: `app/ai/voice/fairy_clone_core.txt`
- destination: `fairy-v3/resources/voice/fairy_clone_core.*`
- status: pending provenance manifest and acoustic validation
