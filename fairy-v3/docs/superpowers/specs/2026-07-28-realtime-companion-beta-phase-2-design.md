# Realtime Companion Beta Phase 2 Design

## Status and authority

This document defines Phase 2 of the accepted Fairy Realtime Companion Beta
design for Fairy V3 Windows Desktop. ADR 0022, the Phase 0 contracts, the Phase
1 readiness design, and the authoritative product design remain normative.

Phase 2 delivers the local `fairy-omni-runtime.exe` sidecar boundary:

- a reproducibly pinned and patched `tc-mb/llama.cpp-omni` source baseline;
- a non-elevated, offline C++ runtime with bounded control and media IPC;
- in-memory LLM, VPM, and APM input paths;
- structured LISTEN/SPEAK candidates;
- parent-owned lifecycle and crash isolation; and
- a runtime self-test that Phase 1 readiness can verify without inventing a
  successful model or GPU claim.

Phase 2 does not select Local versus Cloud, capture the microphone or screen,
play speech, apply the Realtime Persona director, or expose a complete user
session. Those responsibilities begin in Phases 3 and 4.

## Reviewed upstream baseline

The production backend is fixed to:

- repository: `https://github.com/tc-mb/llama.cpp-omni.git`;
- revision: `74699a53df6ca0f4947ff37066f851532c20b12d`;
- compatibility: `fairy-omni-runtime-v1`;
- model revision: `502eec5b03eaee9d0d2ce17a176e3490103c9a63`.

The source is synchronized into an ignored dependency directory by a
repository script. The script fetches the exact commit, detaches HEAD, rejects
another revision or a dirty dependency tree, and applies the ordered Fairy
patch stack with `git apply --check` before mutation. It never follows a branch
or a moving release.

The parent repository records:

- repository and commit;
- ordered patch names and SHA-256 values;
- the combined patch-set digest;
- expected upstream license;
- CMake and MSVC minimums; and
- CUDA production-build requirements.

The MiniCPM manifest patch-set digest is replaced with the digest of the actual
ordered patch stack. Manifest canonical-digest tests must fail if the runtime
lock, patch stack, or model manifest disagree.

## Runtime shape

The source lives under:

```text
desktop/native/omni-runtime/
  CMakeLists.txt
  CMakePresets.json
  upstream.lock.json
  include/
  src/
  tests/
  patches/
  scripts/
```

`fairy-omni-runtime.exe` is a console subsystem executable. It has two modes:

1. `--self-test`: validate binary identity, manifest identity, model paths,
   compiled backend features, and the bounded model probe selected by the host;
2. `--stdio --media-pipe <name>`: serve one host-owned runtime connection.

There is no HTTP mode, WebSocket mode, listening TCP socket, plugin loader,
shell command, browser, Core credential, database connection, or arbitrary
filesystem API.

The runtime is not a Tauri command surface. The desktop or Realtime Worker
spawns it as a managed child with a cleared environment and inherited
kill-on-close Job Object. Phase 2 supplies the supervisor and protocol library;
Phase 3 connects that library to the backend resolver.

## Build profiles and truthfulness

Two explicit build profiles exist:

- `contract`: compiles the executable, control protocol, binary media parser,
  self-test identity, lifecycle, and deterministic fake inference adapter.
  It is for protocol tests only and reports `backend_ready: false`.
- `production-cuda`: links the pinned, patched upstream with CUDA and reports
  `backend_ready: true` only when the upstream revision, patch digest, CUDA
  feature, and model probe all pass.

A contract build can never make Local Beta ready. A CPU-only upstream build can
exercise source compatibility but also reports `backend_ready: false`. The
Phase 1 readiness runner accepts only a production CUDA report.

This separation lets CI and developer machines test the security-critical
runtime shell without downloading 6.3 GiB of weights or pretending that a
non-CUDA executable is production-capable.

## Upstream patch boundary

The Fairy patch stack is intentionally narrow:

1. add memory-backed audio and image inputs to the Omni duplex path;
2. add an output policy that suppresses diagnostic directories, token dumps,
   WAV files, timing files, and other raw-media artifacts;
3. expose a bounded text decision result without enabling upstream HTTP or TTS.

Fairy initializes upstream with:

- `media_type = 2`;
- `use_tts = false`;
- `duplex_mode = true`;
- `async = true`;
- the pinned LLM, VPM, and APM files;
- no TTS, Projector TTS, Token2Wav, reference voice, or Python service;
- memory-only output policy.

The final voice remains owned by the existing Fairy Voice Worker. Phase 2
returns text candidates only.

## Control protocol

Control uses the existing little-endian four-byte length prefix followed by
strict UTF-8 JSON. Maximum payload is 256 KiB. Empty, oversized, truncated,
unknown-field, unknown-command, duplicate-start, stale-identity, and
out-of-order frames fail closed.

Every session-bound command carries:

- `session_id`;
- `segment_id`;
- `context_epoch`;
- monotonic `sequence`.

Commands are:

- `hello`;
- `load`;
- `context_begin`;
- `context_rotate`;
- `media_commit`;
- `cancel_generation`;
- `stop`;
- `ping`.

Events are:

- `ready`;
- `load_progress`;
- `model_ready`;
- `context_ready`;
- `decision`;
- `resource_pressure`;
- `context_rotated`;
- `stopped`;
- `diagnostic`;
- `pong`.

`decision` is a candidate, not an action:

```json
{
  "type": "decision",
  "session_id": "…",
  "segment_id": "…",
  "context_epoch": 3,
  "sequence": 18,
  "decision": "speak",
  "text": "候选文本",
  "confidence": 0.84,
  "grounding": ["bounded public observation"]
}
```

The only decision values are `listen` and `speak`. The runtime cannot request a
tool, open a URL, write memory, switch providers, or claim an action.

## Binary media channel

Media uses one host-created local Windows named pipe. The runtime receives only
the pipe name. The host uses first-instance and reject-remote-client flags and
verifies the connected child PID. The runtime verifies the server PID against
its parent. No authentication secret is placed in the command line.

Each frame starts with the fixed 36-byte little-endian header:

```text
magic[4]       = "FOMI"
version:u16    = 1
kind:u16       = 1 mic PCM16, 2 application PCM16, 3 JPEG, 4 BGRA
session_epoch:u64
sequence:u64
timestamp_us:u64
payload_len:u32
```

Bounds:

- microphone: PCM16, 16 kHz, mono, exactly 20 ms (640 bytes);
- application audio: PCM16, 16 kHz, mono, at most one second;
- JPEG: at most 8 MiB;
- BGRA: dimensions declared by the current context and at most 32 MiB;
- total buffered media: 48 MiB;
- queue: latest video frame plus bounded audio rings.

Malformed magic, version, kind, epoch, sequence, timestamp, size, cadence, or
payload closes the media channel and emits a safe diagnostic. The parser checks
the size before allocation.

Media remains in owned memory buffers. It is never converted to a temporary
WAV, BMP, JPEG, token dump, or debug directory. Model GGUF files are the only
runtime filesystem reads after load.

## Context ownership

The runtime owns three bounded contexts:

- Duplex Conversation: microphone plus current selected-window frame;
- Structured Perception: selected-window frame plus separately authorized
  application audio;
- Assistance Summary: bounded text supplied by the governed host.

Each context has an explicit token/frame/audio budget. It cannot share an
unbounded KV cache. Rotation destroys the old epoch after carrying only the
bounded public summary supplied by the host. Raw media never crosses an epoch.

Phase 2 implements begin, rotate, reset, and teardown mechanics. Phase 4 owns
the policy that chooses rotation reasons and the Persona content inserted into
new contexts.

## Self-test contract

The runtime self-test response advances to schema 2:

```json
{
  "schema_version": 2,
  "runtime_compatibility": "fairy-omni-runtime-v1",
  "manifest_digest": "…",
  "model_version": "4.5-q4-502eec5",
  "predicted_model_peak_bytes": 9961472000,
  "upstream_runtime_revision": "74699a…",
  "patch_set_digest": "…",
  "build_profile": "production-cuda",
  "cuda_compiled": true,
  "backend_ready": true,
  "model_probe": "passed"
}
```

The desktop validates every field and denies unknown fields. A contract build,
CPU build, missing CUDA feature, wrong revision, wrong patch digest, missing
model, failed GGUF open, or failed LLM/VPM/APM probe returns a typed failure and
cannot become ready.

The self-test is offline, inherits no environment, writes no files, emits one
bounded JSON object to stdout, and sends diagnostics only to bounded stderr.

## Crash isolation and recovery

- The host keeps the runtime in Fairy's kill-on-close Job Object.
- Stdin EOF, control parse failure, media pipe failure, parent death, or
  explicit stop tears down inference and exits.
- Control stdout is reserved for protocol frames; logs go to bounded stderr.
- The supervisor limits startup, load, stop, and kill deadlines.
- One automatic restart is allowed only before a session produces a candidate.
- A crash after a candidate creates a new Backend Segment in Phase 3; it never
  silently resumes the old model context.
- Repeated crashes quarantine the runtime until an explicit verify action.

## Security gates

Automated source checks reject:

- HTTP/server targets linked into the runtime;
- Winsock, WinHTTP, libcurl, shell, PowerShell, or arbitrary process launch;
- raw media file extensions or output/debug directories in production paths;
- inherited provider/Core credential environment names;
- unrestricted filesystem traversal;
- control events that resemble tool execution.

The packaged executable is non-elevated and its capability does not grant
shell, network, dialog, or filesystem permissions.

## Testing and acceptance

Phase 2 automated coverage includes:

- source lock and ordered patch digest;
- clean apply against the exact upstream commit;
- C++ control-frame round trips and strict rejection cases;
- media-header serialization, size-before-allocation, epoch/sequence checks,
  audio cadence, latest-frame behavior, and bounded queues;
- LISTEN/SPEAK candidate normalization;
- context begin/rotate/teardown without raw-media carryover;
- contract-build self-test rejection by desktop readiness;
- production-report identity validation;
- EOF, cancellation, crash, timeout, output-limit, and quarantine behavior;
- static denial of network, temporary media files, TTS assets, and tool APIs.

The contract executable is built and exercised on Windows in Phase 2. If the
machine lacks the CUDA Toolkit or verified model files, production CUDA compile
and real LLM/VPM/APM inference remain explicit hardware gates; Local Beta stays
unavailable. Docker, release packaging, paid cloud, live microphone/screen
capture, and long-duration performance tests remain deferred.
