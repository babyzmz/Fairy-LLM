from __future__ import annotations

import argparse
import json
from pathlib import Path

from fairy_cloud.openapi import build_openapi_document


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the canonical Fairy Cloud OpenAPI contract"
    )
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(build_openapi_document(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
