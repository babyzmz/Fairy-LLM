"""Local deterministic recovery gate. Never launches real providers or a release build."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]


def python_for(project):
    folder = "Scripts" if os.name == "nt" else "bin"
    program = "python.exe" if os.name == "nt" else "python"
    return str(ROOT / project / ".venv" / folder / program)


def steps():
    node = shutil.which("node") or "node"
    cargo = shutil.which("cargo") or "cargo"
    core = python_for("core")
    return [
        (
            "typescript",
            "desktop",
            [node, "node_modules/typescript/bin/tsc", "--noEmit"],
        ),
        ("vitest", "desktop", [node, "node_modules/vitest/vitest.mjs", "run"]),
        (
            "ruff",
            ".",
            [
                core,
                "-m",
                "ruff",
                "check",
                "core/src",
                "core/tests",
                "capabilities/src",
                "capabilities/tests",
                "cloud/src",
                "cloud/tests",
                "voice-worker/src",
                "voice-worker/tests",
                "scripts/recovery_gate.py",
                "scripts/recovery_database_probe.py",
            ],
        ),
        ("core", "core", [core, "-m", "pytest", "-q", "--tb=short"]),
        (
            "capabilities",
            "capabilities",
            [python_for("capabilities"), "-m", "pytest", "-q", "--tb=short"],
        ),
        (
            "cloud-local-contracts",
            "cloud",
            [
                python_for("cloud"),
                "-m",
                "pytest",
                "tests",
                "--ignore=tests/integration",
                "-q",
                "--tb=short",
            ],
        ),
        ("voice", "voice-worker", [core, "-m", "pytest", "tests", "-q", "--tb=short"]),
        (
            "rust",
            "desktop/src-tauri",
            [cargo, "test", "--locked", "--offline", "--workspace", "--all-targets",
             "--", "--test-threads=1"],
        ),
        (
            "clippy",
            "desktop/src-tauri",
            [
                cargo,
                "clippy",
                "--locked",
                "--offline",
                "--workspace",
                "--all-targets",
                "--",
                "-D",
                "warnings",
            ],
        ),
        (
            "playwright",
            "desktop",
            [node, "node_modules/@playwright/test/cli.js", "test", "--workers=2"],
        ),
    ]


EXTERNAL = (
    "real-provider-auto-manual",
    "real-wsl-sandbox",
    "real-postgresql-s3",
    "native-webview2-two-windows",
    "native-audio-gpu-five-minute-release",
    "native-glass-local-and-manual-remote-capture",
)


def report(destination, results):
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_kind": "local_deterministic_commands",
        "results": results,
        "external_gates": [
            {"name": name, "status": "not_verified"} for name in EXTERNAL
        ],
        "complete": False,  # Deterministic suites cannot certify the hardware journey.
    }
    (destination / "report.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    lines = [
        "# Fairy recovery gate",
        "",
        "Local deterministic evidence only; no hardware or live-provider claim.",
        "",
        "| Gate | Status | Seconds |",
        "|---|---|---:|",
    ]
    lines += [
        f"| {item['name']} | {item['status']} | {item.get('seconds', 0):.2f} |"
        for item in results
    ]
    lines += ["", "## Still requires separate evidence", ""] + [
        f"- {name}: not verified" for name in EXTERNAL
    ]
    (destination / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=[item[0] for item in steps()])
    parser.add_argument("--output", type=Path, default=ROOT / ".tmp" / "recovery-gate")
    args = parser.parse_args()
    selected = set(args.only or [item[0] for item in steps()])
    results = [{"name": name, "status": "not_run"} for name, _, _ in steps()]
    report(args.output, results)
    failed = False
    for result, (name, folder, command) in zip(results, steps(), strict=True):
        if name not in selected:
            continue
        print(f"Running {name}", flush=True)
        started = monotonic()
        try:
            environment = os.environ.copy()
            # Voice has no runtime dependencies; use the Core test runner without
            # installing a second environment or warming its optional GPU runtime.
            if name == "voice":
                environment["PYTHONPATH"] = str(ROOT / "voice-worker" / "src")
            completed = subprocess.run(
                command,
                cwd=ROOT / folder,
                check=False,
                env=environment,
            )
            result.update(
                status="passed" if completed.returncode == 0 else "failed",
                exit_code=completed.returncode,
            )
        except OSError:
            result.update(status="environment_unavailable")
        except KeyboardInterrupt:
            result.update(status="interrupted")
            raise
        finally:
            result["seconds"] = monotonic() - started
            report(args.output, results)
        failed |= result["status"] != "passed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
