# Realtime Companion Beta Support Matrix

## Release scope

Fairy Realtime Companion Beta targets Windows 10/11 x64. The product has one
Fairy Persona and three activity profiles—Auto, Game, and Focus. Profiles are
policies, not separate assistants.

Local realtime is a hardware-qualified Beta. Cloud realtime is available on
devices that can configure a supported provider and explicitly authorize its
upload scope.

## Device and runtime matrix

| Device/runtime state | Local MiniCPM-o 4.5 Beta | Cloud realtime |
| --- | --- | --- |
| NVIDIA discrete GPU with at least 12 GB dedicated VRAM and every runtime readiness check passing | Available | Available when configured |
| Qualifying NVIDIA GPU with insufficient current VRAM budget | Temporarily unavailable | Available when configured |
| NVIDIA GPU below 12 GB dedicated VRAM | Not offered | Available when configured |
| AMD or Intel discrete GPU | Not offered in this Beta | Available when configured |
| No discrete GPU | Not offered | Available when configured |
| Model missing or not verified | Unavailable until managed install completes | Available when configured |
| Runtime missing, failed, incompatible, or quarantined | Unavailable until verification succeeds | Available when configured |

Local Beta also requires AVX2, an initialized CUDA driver, a CUDA/DXGI adapter
LUID match, verified model/runtime digests, a passing runtime self-test,
adequate disk space, and enough current GPU budget for the verified model peak
plus 512 MiB runtime headroom.
System memory of at least 32 GiB is recommended; below 24 GiB produces a risk
warning.

A 12 GB GPU does not guarantee availability; it only identifies the supported
hardware class. Games and creative tools may
consume too much of the current GPU memory budget. Fairy does not disguise
partial GPU offload or a slow CPU fallback as Local Realtime Beta.

## Backend selection

- Auto can choose Local only when every current readiness check passes.
- Auto can use Cloud only when Cloud fallback is enabled, a provider is
  configured, and the current Session permissions cover microphone and
  selected-window upload.
- A Session Segment uses exactly one Backend.
- Fairy never switches Local and Cloud silently. Continuing through Cloud
  after a local failure requires a user action and creates a new Segment.

## Supported Beta capabilities

The implemented contract covers:

- microphone conversation;
- selected-window visual understanding;
- optional selected-application audio;
- Fairy Voice or supported provider-native voice;
- stable public captions and main-chat transcript;
- barge-in, pause, resume, and explicit stop;
- Auto, Game, and Focus profile policy;
- governed Core Assistance for public research and project knowledge;
- terminal Session digests and proposal-governed memory.

Keyboard/mouse control, game injection, autonomous application operation,
silent high-risk actions, and MiniCPM-native TTS are not part of this Beta.

## Release certification

Packaging success is not release approval. Public Beta distribution requires a
passing four-hour eligible-hardware soak, native WebView2 recovery tests,
reference CUDA latency/queue measurements, Game Profile impact measurements,
installer lifecycle evidence, signing, and malware scanning.
