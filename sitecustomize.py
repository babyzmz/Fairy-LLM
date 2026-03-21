from __future__ import annotations

import sys
from pathlib import Path


def _inject_vendor_site_packages() -> None:
    vendor = Path(__file__).resolve().parent / ".vendor" / "site-packages"
    if not vendor.exists():
        return
    vendor_path = str(vendor)
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)


_inject_vendor_site_packages()
