# Qt Legacy Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the executable Qt shell and its active dependencies from the repository while preserving every non-Qt backend and Fairy V3 runtime.

**Architecture:** Treat Qt removal as an active-source boundary change. A small repository contract test prevents the launcher, UI package, PySide dependency, and migration-only scripts from returning; the implementation then deletes those surfaces and points the root README exclusively at Fairy V3/Tauri.

**Tech Stack:** Python 3.13, pytest, Git, Tauri 2, React/Vite, TypeScript, Vitest, Playwright

## Global Constraints

- Publish the removal as normal commits on `main`; do not rewrite Git history.
- Preserve `app/api/main.py` and all other non-Qt backend modules.
- Preserve `fairy-v3/` and `fairy-desktop/`.
- Preserve historical documents under `docs/history/` and `docs/platform_migration/`.
- Preserve the user's untracked root `CLAUDE.md`; never stage it.
- Do not merge or cherry-pick `codex/qt-decommission-prep`.
- Do not run Docker, release builds, or production image builds.
- Do not use subagents; execute this plan inline.
- Push only to the private `babyzmz/Fairy-LLM` repository.

---

### Task 1: Define the no-Qt repository boundary

**Files:**
- Create: `tests/test_no_qt_legacy.py`

**Interfaces:**
- Consumes: repository paths rooted at `Path(__file__).resolve().parents[1]`.
- Produces: three pytest contracts covering removed paths, active `PySide6` references, and README launch guidance.

- [ ] **Step 1: Add the failing boundary test**

```python
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
    assert [path.relative_to(ROOT).as_posix() for path in REMOVED_PATHS if path.exists()] == []


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
```

- [ ] **Step 2: Run the contract and confirm it fails against the legacy tree**

Run:

```powershell
python -m pytest tests/test_no_qt_legacy.py -q
```

Expected: three failures listing `main.py`, `app/assistant_mode.py`,
`app/ui`, active `PySide6` references, and the old README sentence.

---

### Task 2: Remove the Qt shell and make the boundary pass

**Files:**
- Delete: `main.py`
- Delete: `app/assistant_mode.py`
- Delete: all 43 tracked files under `app/ui/`
- Delete: `scripts/qt_module_classification.py`
- Delete: `scripts/qt_removal_regression.py`
- Delete: `tests/test_jobs_page_data.py`
- Delete: `tests/test_multi_endpoint_routing.py`
- Delete: `tests/test_system_notifications.py`
- Delete: `tests/test_windows_layered_window.py`
- Modify: `requirements.txt`
- Modify: `README.md`
- Test: `tests/test_no_qt_legacy.py`

**Interfaces:**
- Consumes: the failing contracts from Task 1.
- Produces: a repository whose only supported desktop entrypoint is `fairy-v3/desktop`; the non-Qt root API remains at `app/api/main.py`.

- [ ] **Step 1: Delete the tracked Qt runtime and migration-only files**

Use `apply_patch` delete operations for the exact paths above. Delete the full
tracked `app/ui/` package, not an archive or launcher stub.

- [ ] **Step 2: Remove the PySide dependency**

Change the first lines of `requirements.txt` from:

```text
PySide6>=6.5
fastapi>=0.115.0
```

to:

```text
fastapi>=0.115.0
```

- [ ] **Step 3: Replace the root README with active-runtime guidance**

Write a concise UTF-8 README containing:

````markdown
# Fairy LLM

Fairy is a local-first AI workspace. The supported desktop application is
Fairy V3, built with Tauri, React, and the modular Fairy Core.

## Active project

- Project root: `fairy-v3/`
- Desktop shell: `fairy-v3/desktop/`
- Local Core: `fairy-v3/core/`
- Capabilities: `fairy-v3/capabilities/`
- Optional cloud services: `fairy-v3/cloud/`

Development launch:

```powershell
cd fairy-v3/desktop
npm install
npm run tauri -- dev
```

The former Python/Qt desktop shell has been removed. Historical migration
records remain under `docs/history/` and `docs/platform_migration/`.
````

- [ ] **Step 4: Run the no-Qt boundary test**

Run:

```powershell
python -m pytest tests/test_no_qt_legacy.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Check for active Qt imports and stale deleted-module imports**

Run:

```powershell
git grep -n PySide6 -- app scripts tests requirements.txt
git grep -n -E "from app\\.ui|import app\\.ui|app\\.assistant_mode|from app\\.assistant_mode" -- app scripts tests
```

Expected: both commands return no matches.

- [ ] **Step 6: Commit the removal**

Stage only the paths in this task plus `tests/test_no_qt_legacy.py`.

```powershell
git commit -m "refactor: remove legacy Qt shell"
```

Expected: one removal commit; `CLAUDE.md` remains untracked.

---

### Task 3: Verify the remaining runtimes

**Files:**
- Verify: remaining root `tests/`
- Verify: `fairy-v3/desktop/`
- Remove after test: `fairy-v3/desktop/test-results/`

**Interfaces:**
- Consumes: the Qt-free tree from Task 2.
- Produces: evidence that the retained Python backend and Fairy V3 desktop still pass their applicable gates.

- [ ] **Step 1: Run the remaining root Python tests**

Run:

```powershell
python -m pytest tests -q
```

Expected: all collected non-Qt tests pass. If an environment-only optional
dependency prevents collection, install the locked/root requirement once,
rerun, and report the exact dependency.

- [ ] **Step 2: Run TypeScript and Vitest gates**

Run from `fairy-v3/desktop/`:

```powershell
npx --no-install tsc --noEmit
npx --no-install vitest run
```

Expected: TypeScript exits `0`; all Vitest files and tests pass.

- [ ] **Step 3: Run the complete Playwright gate**

Run from `fairy-v3/desktop/`:

```powershell
npx --no-install playwright test --workers=1
```

Expected: all functional and performance tests pass through the managed Vite
lifecycle.

- [ ] **Step 4: Clean generated test output and confirm process shutdown**

Resolve `fairy-v3/desktop/test-results` and verify it is inside
`fairy-v3/desktop` before removing it. Then inspect project-scoped `node`,
`python`, `fairy`, `cargo`, and `rustc` command lines.

Expected: no `test-results` directory and no Fairy project process remains.

---

### Task 4: Publish the Qt-free main branch

**Files:**
- Verify only: Git index, `origin/main`, GitHub repository metadata

**Interfaces:**
- Consumes: the green commits from Tasks 1-3.
- Produces: private `babyzmz/Fairy-LLM` `main` at the same SHA as local.

- [ ] **Step 1: Review the final local scope**

Run:

```powershell
git status -sb
git diff --check
git log -3 --oneline
```

Expected: `main` is ahead only by the design, plan, and Qt-removal commits;
`CLAUDE.md` is the sole unrelated untracked item.

- [ ] **Step 2: Push main**

Run:

```powershell
git push origin main
```

Expected: the push advances `origin/main` without force.

- [ ] **Step 3: Verify privacy and SHA equality**

Run:

```powershell
gh repo view babyzmz/Fairy-LLM --json nameWithOwner,visibility,isPrivate,defaultBranchRef,url
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Expected: visibility is `PRIVATE`, default branch is `main`, and local and
remote SHAs match.
