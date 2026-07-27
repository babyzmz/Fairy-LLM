from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"release document is missing: {relative_path}")
    return path.read_text(encoding="utf-8")


def require(text: str, fragments: tuple[str, ...], label: str) -> None:
    for fragment in fragments:
        if fragment not in text:
            raise AssertionError(f"{label} is missing required text: {fragment}")


def main() -> None:
    notice = read("THIRD_PARTY_NOTICES.md")
    support = read("docs/release/realtime-companion-beta-support.md")
    privacy = read("docs/release/realtime-companion-beta-privacy.md")
    troubleshooting = read("docs/release/realtime-companion-beta-troubleshooting.md")
    readme = read("README.md")

    require(
        notice,
        (
            "Tauri",
            "React",
            "TanStack",
            "Motion",
            "Lucide",
            "pdf.js",
            "Three.js",
            "Zod",
            "Fairy Core sidecar",
            "llama.cpp-omni",
            "MiniCPM-o 4.5",
            "Voice runtime",
            "MinGit",
        ),
        "third-party notice",
    )
    require(
        support,
        (
            "Windows 10/11 x64",
            "at least 16 GiB physical VRAM",
            "does not guarantee availability",
            "never switches Local and Cloud silently",
            "Packaging success is not release approval",
        ),
        "support matrix",
    )
    require(
        privacy,
        (
            "Telemetry is disabled by",
            "selected window",
            "does not write raw microphone audio",
            "Only stable public captions",
            "must not contain",
            "Realtime Worker and Omni runtime cannot directly",
        ),
        "privacy disclosure",
    )
    require(
        troubleshooting,
        (
            "`vram_below16gb`",
            "`runtime_quarantined`",
            "does not loop, switch to CPU, or silently switch to Cloud",
            "Do not include content captured or spoken",
        ),
        "troubleshooting guide",
    )
    require(
        readme,
        (
            "Windows x64 MSI",
            "external cabinet",
            "THIRD_PARTY_NOTICES.md",
            "realtime-companion-beta-support.md",
            "realtime-companion-beta-privacy.md",
        ),
        "README release section",
    )

    if re.search(r"\bNSIS\b", readme, re.IGNORECASE):
        raise AssertionError("README must not claim an NSIS bundle")

    for relative_path in (
        "desktop/src-tauri/resources/legal/llama.cpp-omni-LICENSE.txt",
        "desktop/src-tauri/resources/legal/windows-capture-dda-LICENSE.txt",
    ):
        license_text = read(relative_path)
        if "Permission is hereby granted" not in license_text:
            raise AssertionError(f"license text is incomplete: {relative_path}")

    forbidden = (
        "api_key",
        "api-key",
        "bearer ",
        "C:\\Users\\",
        "/home/",
        "full transcript example",
    )
    combined = "\n".join((notice, support, privacy, troubleshooting))
    lowered = combined.lower()
    for fragment in forbidden:
        if fragment.lower() in lowered:
            raise AssertionError(f"release documents contain forbidden text: {fragment}")

    print("Realtime Companion Beta release documents passed.")


if __name__ == "__main__":
    main()
