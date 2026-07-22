# Persona And Ambient Dialogue Acceptance

## Observable behavior

- Every Assistant Turn binds the same immutable Fairy Persona version and digest as its Harness
  Manifest. A later Persona update cannot change an executing Turn.
- Main chat, project completion summaries, pet replies, and TTS use the same final text. Persona
  styling never creates a second Assistant message or a second speech request.
- Ambient dialogue is text-only by default, uses the reviewed catalog without a model call, and is
  emitted only while Fairy is otherwise idle.
- Ambient voice and generated ambient dialogue are independent opt-in device settings. A disabled
  setting cannot warm Voice or invoke a model.
- Active work, approvals, typing/IME, an open pet input, recording, Realtime, fullscreen content,
  lock state, do-not-disturb, severe errors, and active TTS suppress ambient dialogue.
- Task progress stays in Presence animation. It never becomes an ambient bubble.
- Reply, failure, and approval projections preempt and dismiss an ambient projection.
- Liquid Glass, window placement, hit regions, hover, tap, and drag behavior are unchanged.

## State ownership

| State | Owner | Scope key | Persistence |
|---|---|---|---|
| Canonical Persona | Core `PersonaAuthority` | Persona version | approved product resource |
| Turn Persona binding | `HarnessContextManifest` | Task ID | Core database |
| Dialogue catalog | Core `DialogueCatalog` | locale + semantic ID | approved product resource |
| Ambient eligibility | Core `FairyDialogueDirector` | one evaluation | memory only |
| Sanitized device facts | trusted Desktop Host | desktop process | memory only |
| Ambient preferences | `DesktopPreferencesStore` | device | atomic revision-fenced JSON |
| Cooldown history | `AmbientDialogueStateStore` | device | atomic local JSON |
| Visible ambient line | Presence projection | presentation ID | ephemeral window event |

## Invariants

- Presence and pet renderers never import provider, tool, project, memory, or CoreClient modules.
- Ambient context contains only bounded timestamps, numbers, locale/surface values, and booleans;
  it contains no window titles, clipboard, screen pixels, files, contacts, mail, camera data,
  transcript, or absolute paths.
- Ambient context and text are not written to Ledger, Hermes, conversation history, or cloud sync.
- The default path performs zero provider calls. Generated dialogue has no tools, history, Memory,
  Knowledge, Workspace, or media capabilities.
- Generated dialogue is prefetched by one process-local daemon task. The local Core RPC loop returns
  `generation_pending` immediately, and a later evaluation delivers either one validated candidate
  or one reviewed-catalog fallback.
- One presentation ID can be shown and spoken at most once.
- A catalog line cannot claim a system observation or action unless its `required_facts` are true.
- High-risk scenarios use factual language with no humor, teasing, or character flourish.
- Operational labels and raw errors are not rewritten by Persona.

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
|---|---|---|---|
| Two Tasks straddle a Persona resource update | each keeps its bound digest | in-flight prompt drift | Core Harness test |
| First eligible startup each local day | protected greeting once | repeat after refresh/restart | Director persistence test |
| Four- and fifteen-minute idle thresholds | eligible reviewed line | early or repeated bubble | Director clock test |
| Active Turn or approval | no ambient line | task status bubble | Director suppression test |
| User typing with IME or pet input open | no ambient line | focus theft or dismissal race | Desktop unit/E2E |
| Reply arrives while ambient is visible | reply replaces ambient once | stacked or duplicate cards | Presence projection test |
| Ambient voice disabled | no Voice session | Voice worker warm-up | Voice controller test |
| Generated dialogue disabled | no provider request | background token use | Core fake-provider test |
| Generated provider blocks or fails | Core RPC returns promptly; next poll falls back once | blocked Core or retry loop | Core blocking-provider test |
| Generated candidate makes an unsupported claim | candidate discarded, catalog fallback | false capability claim | Truth gate test |
| Restart with partial cooldown file | safe defaults or recovered complete state | startup failure | real filesystem test |
| zh-CN/en catalog validation | semantic parity and valid placeholders | missing locale or unsafe text | resource checker test |

## Required environments

- Core unit and SQLite integration tests prove Persona, catalog, Director, Harness persistence, and
  restart recovery.
- Vitest proves preference migration, projection arbitration, deduplication, and TTS gating.
- Playwright proves settings and visible projection behavior in the WebView.
- A real Tauri development run is required only to confirm no Presence window, hit-test, drag, or
  Voice lifecycle regression. Browser tests cannot prove native window behavior.

## Automation boundaries

- Provider generation tests use a deterministic fake and prove request minimization and rejection,
  not current provider availability or output quality.
- Clock, battery, network, fullscreen, microphone, and lock signals are injected in unit tests. A
  final Windows smoke verifies the real collector without recording raw signal payloads.
- The original reference dialogue is not copied into fixtures, resources, snapshots, or builds.
