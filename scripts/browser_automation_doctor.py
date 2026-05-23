from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.browser_automation import browser_automation


def main() -> int:
    report = browser_automation.availability_status(force_refresh=True)
    payload = report.to_dict()
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    payload["doctor"] = {
        "browser_package": diagnostics.get("browser_package", "unknown"),
        "browser_binary": diagnostics.get("browser_binary", "unknown"),
        "executable_path": payload.get("executable_path", ""),
        "browser_type": payload.get("browser_type", ""),
        "launch": diagnostics.get("launch", "not_attempted"),
        "open_page": diagnostics.get("open_page", "not_attempted"),
        "extract": diagnostics.get("extract", "not_attempted"),
        "interaction_smoke": diagnostics.get("interaction_smoke", "not_attempted"),
        "last_smoke_result": diagnostics.get("last_smoke_result", ""),
        "last_launch_error": diagnostics.get("last_launch_error", ""),
        "availability_level": payload.get("availability_level", "unavailable"),
        "failure_cause": diagnostics.get("failure_cause", payload.get("reason", "unknown_but_traced")),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
