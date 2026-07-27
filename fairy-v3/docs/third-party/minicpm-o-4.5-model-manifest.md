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

The Phase 1 patch-set digest is the SHA-256 of an empty patch set because the
runtime is not yet vendored or modified. Phase 2 must replace that digest when
the reviewed Fairy patch set is introduced.

TTS, Projector TTS, Token2Wav, reference voice assets, and CoreML artifacts are
not part of the Fairy Beta manifest.
