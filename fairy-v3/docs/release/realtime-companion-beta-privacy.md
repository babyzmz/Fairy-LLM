# Realtime Companion Beta Privacy

## Default state

Realtime Companion Beta is disabled by default. Telemetry is disabled by
default. Opening Settings or the Companion does not start Voice, Realtime,
Omni, or model-download workers.

## Local processing

Local Beta processes microphone audio, optional selected-application audio,
and the user-selected window through the local Worker and Omni runtime. The
Omni runtime has no network listener, no Fairy Core credential, no database
access, and no tool authority.

Fairy does not write raw microphone audio, raw application audio, continuous
screen frames, interim captions, provider-hidden conversation items, prompts,
model KV cache, or credentials to disk. Queues are bounded and video uses a
latest-frame-wins policy.

Only stable public captions can be saved automatically. They remain in the
device-local Conversation transcript and are not placed in logs or the
Ledger.

## Cloud processing

Cloud realtime sends microphone audio and the selected window only after the
current Session is authorized. Optional application audio is a separate
permission and transport. The Companion shows the active Backend and upload
scope. Cloud use may create third-party model charges.

Fairy does not silently switch from Local to Cloud. A local failure pauses or
ends its Segment; Cloud continuation requires an explicit user action.

## Window privacy

Capture is bound to the window selected by the user. Window identity changes
advance the Window Epoch and invalidate late frames and results. Lock,
sensitive-window, privacy-pause, and terminal states stop new media delivery.

## Transcript, digest, and memory

- Stable public captions are the only automatic content-text persistence.
- Session digests use stable caption evidence from the same terminal Session.
- Long-term memory is created through policy-classified proposals.
- Inferred or sensitive proposals require explicit review in the main window.
- The Companion and desktop pet cannot accept or reject memory and do not own
  Core tool authority.
- Forget, retention, conflict, and revision behavior remain governed by Core.

## Diagnostics

Fairy diagnostics use bounded public codes and counters. They must not contain
caption text, prompts, audio, screenshots, raw window titles, game names,
questions, Assistance answers, credentials, provider payloads, or hidden
reasoning.

If a future user explicitly opts into Beta diagnostics, permitted fields are
limited to GPU class, total VRAM, active Backend, model/runtime digests,
latency quantiles, safe crash/OOM categories, Context Rotation counts, and
Cloud error categories. Opt-in does not authorize content upload.

## External actions

Realtime Worker and Omni runtime cannot directly modify files, control input,
send messages, make payments, or call external tools. Governed Assistance
routes through Fairy Core, ordinary capability policy, approval, Scope, and
the durable main chat.
