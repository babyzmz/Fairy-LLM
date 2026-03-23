from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_UI = ROOT / "app" / "ui"


def main() -> None:
    report = {
        "legacy_entrypoints": [
            "main.py",
            "app/assistant_mode.py",
        ],
        "freeze_candidates": [
            "app/ui/desktop_pet.py",
            "app/ui/components/fairy_presence_window.py",
            "app/ui/components/fairy_presence_reply_bubble.py",
            "app/ui/pages/chat_page.py",
        ],
        "retain_temporarily": [
            "app/ui/training_data_workspace.py",
            "app/ui/app_mode_dialog.py",
        ],
        "delete_candidates_after_qt_removal": [
            "app/ui/components/chat",
            "app/ui/desktop_pet.py",
            "app/ui/components/fairy_presence_window.py",
        ],
        "qt_python_files": sorted(str(path.relative_to(ROOT)).replace("\\", "/") for path in APP_UI.rglob("*.py")),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
