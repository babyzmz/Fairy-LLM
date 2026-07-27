# Fairy Third-Party Notices

This document identifies material third-party components used by the Fairy
Windows desktop release. Fairy's own source license is not granted or changed
by this notice.

The release bundle must retain the license files shipped inside each runtime.
Source links are provided for attribution and corresponding-license access.
Versions are pinned by `package-lock.json`, `Cargo.lock`, `uv.lock`, runtime
manifests, and build scripts.

## Desktop and WebView application

| Component | Version | License | Source |
| --- | --- | --- | --- |
| Tauri / `@tauri-apps/api` | 2.11 | Apache-2.0 OR MIT | <https://github.com/tauri-apps/tauri> |
| React / React DOM | 19.2.7 | MIT | <https://github.com/facebook/react> |
| TanStack Query | 5.101.2 | MIT | <https://github.com/TanStack/query> |
| TanStack Router | 1.170.17 | MIT | <https://github.com/TanStack/router> |
| Motion | 12.42.2 | MIT | <https://github.com/motiondivision/motion> |
| Lucide | 1.24.0 | ISC | <https://github.com/lucide-icons/lucide> |
| React Aria Components | 1.19.0 | Apache-2.0 | <https://github.com/adobe/react-spectrum> |
| pdf.js | 5.4.624 | Apache-2.0 | <https://github.com/mozilla/pdf.js> |
| Three.js | 0.185.1 | MIT | <https://github.com/mrdoob/three.js> |
| Zod | 4.3.6 | MIT | <https://github.com/colinhacks/zod> |
| React Markdown | 10.1.0 | MIT | <https://github.com/remarkjs/react-markdown> |
| remark-gfm | 4.0.1 | MIT | <https://github.com/remarkjs/remark-gfm> |

The Rust application also uses the crates declared in
`desktop/src-tauri/Cargo.toml` and resolved in `Cargo.lock`, including Tauri,
Windows bindings, serde, reqwest, rustls, image, SHA-2, UUID, zeroize, xcap,
and their transitive dependencies. Their license metadata and source
references remain available in the pinned Cargo package graph.

## Fairy Core sidecar

| Component | Pinned release | License |
| --- | --- | --- |
| Alembic | 1.18.5 | MIT |
| cryptography | 49.0.0 | Apache-2.0 OR BSD-3-Clause |
| HTTPX | 0.28.1 | BSD-3-Clause |
| Model Context Protocol Python SDK | 1.28.1 | MIT |
| Pydantic | 2.13.4 | MIT |
| PyYAML | 6.0.3 | MIT |
| SQLAlchemy | 2.0.51 | MIT |

The packaged Python interpreter and PyInstaller bootloader retain their own
notices in the generated Core distribution.

## Local realtime and capture runtimes

| Component | Pinned revision/version | License | Bundle status |
| --- | --- | --- | --- |
| `llama.cpp-omni` / ggml | `74699a53df6ca0f4947ff37066f851532c20b12d` | MIT | Omni runtime source baseline; license copied to `legal/llama.cpp-omni-LICENSE.txt` |
| Fairy Windows DXGI capture integration, derived from `window-capture` | repository lock | MIT | License copied to `legal/windows-capture-dda-LICENSE.txt` |
| MiniCPM-o 4.5 GGUF | `502eec5b03eaee9d0d2ce17a176e3490103c9a63` | Apache-2.0 | Downloaded separately; model bytes are not in the installer |

MiniCPM provenance, file sizes, and hashes are recorded in
`docs/third-party/minicpm-o-4.5-model-manifest.md`.

## Voice runtime

The optional governed Voice Worker build integrates CosyVoice 3 and its
Matcha-TTS dependency from the release operator's reviewed `third_party`
source tree. A release candidate may include that runtime only when its
upstream license files are copied into the generated Voice distribution and
the release composition gate finds them. The current repository intentionally
does not vendor that source tree or claim that absent bytes are bundled.

## MinGit

Fairy bundles the pinned Git for Windows MinGit runtime, currently
`2.55.0.windows.2`. Git is distributed under GPL-2.0-only, with component
licenses and notices retained under the runtime's `LICENSE.txt`,
`mingw64/share/licenses`, `usr/share/licenses`, and Git Credential Manager
documentation directories.

## No model-license substitution

This notice does not replace any upstream license. Users who install the
MiniCPM-o model receive the model repository's Apache-2.0 terms through the
managed model flow. Fairy does not relicense third-party models, runtimes, or
libraries.
