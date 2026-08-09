from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from fairy_core.evals.agent_workflow import (
    SCENARIOS,
    build_report,
    load_live_provider,
    parse_junit_cases,
    write_report,
)


def main() -> int:
    core_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="Run Fairy Agent Workflow developer evaluations")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=core_root.parent / "test-results" / "agent-eval",
    )
    parser.add_argument("--live-provider-report", type=Path)
    parser.add_argument("--list", action="store_true", help="List scenarios without running them")
    args = parser.parse_args()
    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario.name}: {scenario.title}")
        return 0

    node_ids = [node_id for scenario in SCENARIOS for node_id in scenario.tests]
    with tempfile.TemporaryDirectory(prefix="fairy-agent-eval-") as temporary:
        junit_path = Path(temporary) / "junit.xml"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *node_ids,
                f"--junitxml={junit_path}",
                "-o",
                "junit_family=legacy",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            cwd=core_root,
            check=False,
        )
        cases = parse_junit_cases(junit_path) if junit_path.exists() else {}
    live_provider = (
        load_live_provider(args.live_provider_report) if args.live_provider_report else None
    )
    report = build_report(
        cases,
        pytest_exit_code=completed.returncode,
        live_provider=live_provider,
    )
    json_path, markdown_path = write_report(report, args.output_dir.resolve())
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
