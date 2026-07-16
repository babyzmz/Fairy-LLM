# Extension Recovery And Presence Retry Acceptance

## Observable behavior

- An interrupted Skill install, update, removal, or state write never prevents Fairy Core
  from starting on the next launch.
- Recovery chooses the last complete governed Skill package. Partial staging directories are
  never loaded or exposed to the model.
- A failed Skill operation leaves the filesystem, Skill registry, tool registry, and enabled
  state in the same observable state they had before the operation.
- A malformed package is isolated from healthy packages. Healthy Skills remain available,
  while the malformed package is quarantined and cannot contribute a model-visible tool.
- A transient native pet-window creation failure is retried with bounded backoff. One failed
  attempt does not permanently disable Presence for the desktop session.
- A successful Presence initialization is idempotent: later page-load notifications do not
  create duplicate windows or coordinator threads.

## State ownership

| State | Owner | Scope key | Restart rule |
|---|---|---|---|
| Skill package | `SkillManager` filesystem root | Skill name | recover complete destination or backup; never load staging |
| Skill enabled state | `.state.json` | Skill name | atomic replace; malformed state disables packages before recovery |
| Model-visible Skill tool | `SkillRegistry` / `ToolRegistry` | `skill.<name>` | rebuilt only from validated enabled packages |
| MCP server state | Core repository | tenant + server ID | reload accepted schema and policy; no rediscovery required |
| Presence initialization | Rust `PresenceStartupGate` | desktop process | one active attempt; bounded automatic retries after failure |
| Native pet windows | Tauri / `PresenceCoordinator` | window labels | reuse existing windows and launch one coordinator |

## Invariants

- Hidden operation paths (`.install-*`, `.backup-*`, `.remove-*`, `.quarantine-*`) are never
  passed to `SkillRegistry.install`.
- If an interrupted update has no destination, its backup is restored before package loading.
- If both update destination and backup exist, a valid destination wins; an invalid destination
  is quarantined and the prior backup is restored.
- An interrupted removal is rolled back because the operation did not finish cleanup or return
  success.
- Registry mutations are rolled back when durable state persistence fails.
- A malformed enabled-state file must not default unknown packages to enabled.
- At most one Presence initialization attempt is active. Success closes the retry sequence;
  failure releases the gate before scheduling another attempt.
- Automatic Presence retries are finite and use increasing delays. A later explicit page load
  may start a fresh attempt after the automatic budget is exhausted.

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
|---|---|---|---|
| Crash after destination moved to update backup | backup restored and Skill loaded | Core startup failure or missing Skill | filesystem recovery test |
| Crash after new update destination written | valid new destination loaded; backup removed | duplicate registry entry | recovery test |
| New destination corrupt, backup valid | corrupt destination quarantined; old package loaded | corrupt model-visible tool | recovery test |
| Crash during removal | removed directory restored and loaded | silent package loss | recovery test |
| Stale install staging | staging removed, no tool registered | staging instructions exposed | recovery test |
| Malformed state file | Core starts with Skills disabled | implicit enable or startup failure | state recovery test |
| State write failure during enable/remove | registry and files return to prior state | process-only divergence | failure-injection test |
| First pet-window creation fails | gate releases and retry is scheduled | permanently absent pet | Rust gate test + native log |
| Retry later succeeds | gate remains ready; no more retries | duplicate coordinator/window | Rust state test + Tauri smoke |
| Repeated failures | retry budget stops | unbounded retry loop | Rust timing/state test |
| Existing Skill and Context7 after Core restart | Skill tool and accepted MCP tools are available | forced reinstall or rediscovery | real local Core restart probe |

## Test truthfulness

- Skill recovery tests use a real temporary filesystem and real package loader/registries. The
  pinned network download is replaced only to avoid relying on GitHub during deterministic tests.
- Presence gate tests prove concurrency and retry policy, not HWND creation. The running Tauri
  development process and native smoke provide separate evidence for actual WebView creation.
- Context7 discovery uses the real HTTPS MCP endpoint only for the final local probe. Unit tests
  continue to use deterministic MCP transports and cannot claim current endpoint availability.
- Docker/PostgreSQL is not evidence for either local filesystem recovery or native Presence and
  is reported separately if unavailable.
