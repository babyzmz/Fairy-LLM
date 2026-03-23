from __future__ import annotations

import re
import sys
from pathlib import Path


_TAG_PATTERN = re.compile(r"cp(\d{2,3})")


def _vendor_is_compatible(vendor: Path) -> bool:
    current_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    seen_binary_tag = False
    for path in vendor.rglob('*'):
        if path.suffix.lower() not in {'.pyd', '.so', '.dll'}:
            continue
        match = _TAG_PATTERN.search(path.name)
        if not match:
            continue
        seen_binary_tag = True
        if match.group(0) == current_tag:
            return True
    return not seen_binary_tag


def _inject_vendor_site_packages() -> None:
    vendor = Path(__file__).resolve().parent / '.vendor' / 'site-packages'
    if not vendor.exists() or not _vendor_is_compatible(vendor):
        return
    vendor_path = str(vendor)
    if vendor_path not in sys.path:
        # Keep the active venv/site-packages ahead of the vendored fallback.
        sys.path.append(vendor_path)


_inject_vendor_site_packages()
