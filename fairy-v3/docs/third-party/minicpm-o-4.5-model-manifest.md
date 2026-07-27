# MiniCPM-o 4.5 Beta Model Manifest Provenance

Reviewed: 2026-07-27

Fairy Phase 1 pins the official OpenBMB GGUF repository at:

- Repository: `openbmb/MiniCPM-o-4_5-gguf`
- Revision: `502eec5b03eaee9d0d2ce17a176e3490103c9a63`
- License: `Apache-2.0`
- Source: <https://huggingface.co/openbmb/MiniCPM-o-4_5-gguf/tree/502eec5b03eaee9d0d2ce17a176e3490103c9a63>

The bundled manifest records the byte size and SHA-256 LFS object ID returned
by the official Hugging Face model API with `blobs=true` for that exact
revision:

| Path | Bytes | SHA-256 |
| --- | ---: | --- |
| `MiniCPM-o-4_5-Q4_K_M.gguf` | 5,026,714,400 | `1237a97ee081b8abebc47aa7dad565701e8f5f904cdc92f6723ac4281bbc0932` |
| `vision/MiniCPM-o-4_5-vision-F16.gguf` | 1,095,113,184 | `1453678cc4e4fe18de241952962e234f265cb8dda780773526103ab8ba82f421` |
| `audio/MiniCPM-o-4_5-audio-F16.gguf` | 660,167,904 | `d5b188ac7feaf98e17175c3f9bd14bf269301bfd187439fdaa3e3a494fc32ef7` |

Total download size: 6,781,995,488 bytes.

The C++ runtime integration baseline is pinned separately:

- Repository: `tc-mb/llama.cpp-omni`
- Revision: `74699a53df6ca0f4947ff37066f851532c20b12d`
- Source: <https://github.com/tc-mb/llama.cpp-omni/commit/74699a53df6ca0f4947ff37066f851532c20b12d>

Phase 2 locks the source and development toolchain in
`desktop/native/omni-runtime/upstream.lock.json`. The source checkout is always
detached at the revision above, and the ordered patch-set digest is calculated
as SHA-256 over each UTF-8 repository-relative patch path, a NUL byte, the raw
patch bytes, and a trailing NUL byte. The reviewed three-patch Fairy set has
digest
`72b89b34a81b2a49abb5079bd411fc6676650e750873e2ec51cb796f49a597bd`.
It adds memory-backed duplex media, disables upstream filesystem/TTS output for
the embedded runtime, and fails closed when a decision exceeds its byte bound.
Verification always applies the ordered patch set to a fresh local clone of the
exact revision before any upstream profile is configured.

The development bootstrap pins the official CMake 4.4.0 Windows x64 ZIP from
<https://cmake.org/files/v4.4/cmake-4.4.0-windows-x86_64.zip> with SHA-256
`156d70eb7625a7b469444df7d0861d2af8d5d0a437fce32c350372b08f5620e8`.
The archive, extracted toolchain, source checkout, and build output are ignored
local inputs and are never committed.

TTS, Projector TTS, Token2Wav, reference voice assets, and CoreML artifacts are
not part of the Fairy Beta manifest.
