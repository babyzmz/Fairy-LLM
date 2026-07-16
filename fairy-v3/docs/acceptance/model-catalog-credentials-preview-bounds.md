# Model Catalog, Credential, And Preview Bounds Acceptance

## Observable behavior

- Model catalog refresh returns the complete approved allowlist, preserves the last known
  good metadata during an outage, and never blocks unrelated database work while waiting
  for OpenRouter.
- Saving an OpenRouter key succeeds only after a candidate Core validates it against the
  official catalog. A rejected or unavailable candidate leaves the previous credential and
  provider configuration usable.
- Settings and Core responses expose only `configured`, `invalid`, or `unavailable`; API key
  text, file paths, prefixes, response bodies, and DPAPI ciphertext never enter JSON-RPC,
  Ledger events, logs, or persisted Core state.
- Built-in file previews refuse inputs that exceed an explicit source or decoded-resource
  budget and show a stable error instead of freezing the WebView or allocating without bound.
- Streaming audio, video, and PDF transport remains available for files within Workspace
  policy; preview limits do not delete or mutate the source file.

## State ownership

| State | Owner | Scope key | Failure or restart rule |
|---|---|---|---|
| Provider credential | Tauri DPAPI store | Windows user + `openrouter-default` | candidate rollback restores opaque previous bytes |
| Provider configuration | Tauri configuration store | device + `openrouter-default` | presence is restored with the credential |
| Model catalog | Core repository | tenant | optimistic revision; last known good survives refresh failure |
| Model selection | Core repository | tenant/device deployment | independent of catalog refresh |
| File source | immutable Workspace Version | workspace + version + path + hash | never changed by presentation |
| Viewer resources | active WebView component | scope key + selected path | disposed on error, scope switch, or unmount |

## Invariants

- Network I/O never occurs inside a model-catalog database unit of work.
- Each catalog response body is at most 4 MiB and contains at most 4,096 records before
  allowlist projection. Redirects and non-OpenRouter destinations remain disabled.
- Concurrent refreshes cannot replace a newer catalog with an older revision.
- Candidate credential validation must return `credential_status=configured` with no catalog
  source error. Missing individual allowlisted models may be recorded as unavailable, but an
  authentication, protocol, timeout, or endpoint failure rejects the candidate.
- Failed credential replacement restores the prior encrypted blob without converting it to
  plaintext. First-time failed setup leaves no configured account.
- Built-in 3D preview accepts at most 256 members and 128 MiB of aggregate source data, then
  rejects scenes above 25,000 objects, 10,000 meshes, or 5,000,000 triangles.
- Built-in image preview rejects decoded images above 64 megapixels. PDF rendering never
  allocates a canvas above 16,777,216 pixels and rejects a page whose CSS pixel area already
  exceeds that bound.
- A rejected preview releases opened viewer/GPU resources and does not start an unbounded
  retry loop.

## Risk triggers

- Catalog HTTP and credentials require body bounds, destination pinning, no secret logging,
  and real-provider validation.
- Credential persistence requires failure injection, replacement, first-save rollback, and
  process restart coverage.
- Preview decoding requires oversized source, decoded-dimension, scene-complexity, scope
  switch, cleanup, and terminal error coverage.

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
|---|---|---|---|
| Slow catalog source | unrelated repository write completes | DB transaction held over HTTP | Core unit test with tracked UoW |
| Oversized catalog body | typed `MODEL_CATALOG_RESPONSE_TOO_LARGE` | full-body JSON parse | HTTP mock test |
| Catalog outage | prior entries remain, error/stale visible | allowlist erased | Core close/reopen test |
| Valid candidate key | DPAPI candidate commits and Core reports configured | plaintext persistence | native Rust + catalog test |
| Invalid replacement key | old key/configuration restored and Core restarted | broken account replaces old | native Rust rollback test |
| Invalid first key | no credential/configuration remains | configured status | native Rust rollback test |
| Oversized glTF dependency set | viewer shows bounded error before stream fan-out | hundreds of streams/large parse | Vitest + Core FileSet metadata |
| Excessive decoded scene | scene is disposed and error is terminal | active render loop | Vitest helper + component cleanup |
| Huge image/PDF page | bounded error or bounded backing canvas | oversized bitmap/canvas | Vitest |
| Real paid account directory | all eight IDs found on four official endpoints | mock-only availability claim | cost-free live GET probe |

## Environment and truthfulness

- OpenRouter HTTP adapter tests use `httpx.MockTransport`; they prove request shape, bounds,
  and error classification, not current provider availability.
- The cost-free live probe reads the user-supplied key in memory and reports only endpoint
  status, record counts, and missing allowlist IDs. It performs no inference or media job.
- DPAPI encryption and rollback require Windows Rust tests. Browser tests cannot substitute
  for that boundary.
- Viewer limit tests use deterministic metadata and geometry counts. They do not prove every
  browser decoder's resistance to malformed codecs; malformed media remains isolated to the
  WebView and requires native soak testing before release.
- Docker/PostgreSQL is unrelated to the local DPAPI boundary and remains separately reported
  if the daemon is unavailable.
