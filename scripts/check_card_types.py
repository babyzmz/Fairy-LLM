from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_CARD_RENDERER = ROOT / "fairy-desktop" / "src" / "features" / "chat" / "CardRenderer.tsx"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.response.card_layout_policy import ResponseCardLayoutPolicy
from app.response.card_schema_registry import CardSchemaRegistry


def parse_frontend_supported_types(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"SUPPORTED_CARD_TYPES\s*=\s*(\[[^\]]+\])", text, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find SUPPORTED_CARD_TYPES in {path}")
    values = ast.literal_eval(match.group(1))
    return {str(item).strip() for item in values}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check backend and frontend card type coverage.")
    parser.add_argument("--strict", action="store_true", help="Exit with status 1 when a mismatch is detected.")
    args = parser.parse_args()

    backend_types = set(CardSchemaRegistry().supported_types())
    layout_types = ResponseCardLayoutPolicy().declared_types()
    frontend_types = parse_frontend_supported_types(FRONTEND_CARD_RENDERER)

    missing_renderers = sorted(card_type for card_type in backend_types if card_type not in frontend_types)
    missing_layouts = sorted(card_type for card_type in backend_types if card_type not in layout_types)
    extra_frontend = sorted(card_type for card_type in frontend_types if card_type not in backend_types)

    print("[card-check] backend schema types :", ", ".join(sorted(backend_types)) or "(none)")
    print("[card-check] frontend renderers   :", ", ".join(sorted(frontend_types)) or "(none)")
    print("[card-check] layout declarations  :", ", ".join(sorted(layout_types)) or "(none)")

    if missing_renderers:
        print("[card-check][warn] missing frontend renderer for:", ", ".join(missing_renderers))
    if missing_layouts:
        print("[card-check][warn] missing explicit layout policy for:", ", ".join(missing_layouts))
    if extra_frontend:
        print("[card-check][info] frontend-only aliases:", ", ".join(extra_frontend))

    if args.strict and (missing_renderers or missing_layouts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
