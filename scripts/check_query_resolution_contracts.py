from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.query_resolution import QueryResolver


def main() -> int:
    resolver = QueryResolver()
    issues = resolver.validate_contracts()
    coverage = resolver.coverage_report()
    print("Query Resolution Coverage:")
    print(json.dumps(coverage, ensure_ascii=False, indent=2))
    if issues:
        print("\nContract Issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("\nContract validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
