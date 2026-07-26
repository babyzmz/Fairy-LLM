from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REMOVED_PATHS = (
    ROOT / "main.py",
    ROOT / "app" / "assistant_mode.py",
    ROOT / "app" / "ui",
    ROOT / "scripts" / "qt_module_classification.py",
    ROOT / "scripts" / "qt_removal_regression.py",
)
ACTIVE_SCAN_ROOTS = (
    ROOT / "app",
    ROOT / "scripts",
    ROOT / "tests",
)


def test_qt_runtime_paths_are_absent() -> None:
    assert [
        path.relative_to(ROOT).as_posix() for path in REMOVED_PATHS if path.exists()
    ] == []


def test_active_runtime_does_not_reference_pyside() -> None:
    needle = "PySide" + "6"
    offenders: list[str] = []
    for scan_root in ACTIVE_SCAN_ROOTS:
        for path in scan_root.rglob("*.py"):
            if path == Path(__file__):
                continue
            if needle in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(path.relative_to(ROOT).as_posix())
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    if needle in requirements:
        offenders.append("requirements.txt")
    assert offenders == []


def test_readme_exposes_only_the_active_desktop_entrypoint() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "fairy-v3/desktop" in readme
    assert "migration debugging" not in readme
    assert "`main.py` (Qt)" not in readme
