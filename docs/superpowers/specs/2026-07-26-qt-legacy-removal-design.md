# Qt Legacy Removal Design

## Goal

Remove the executable Qt desktop shell from the active repository tree while
keeping the non-Qt backend and Fairy V3 runtime intact. Publish the removal as a
normal commit on `main`; do not rewrite Git history.

## Remove

- Root `main.py`, the deprecated Qt launcher.
- `app/assistant_mode.py`.
- The complete `app/ui/` Qt package.
- Qt-only tests that import those deleted modules:
  - `tests/test_jobs_page_data.py`
  - `tests/test_multi_endpoint_routing.py`
  - `tests/test_system_notifications.py`
  - `tests/test_windows_layered_window.py`
- Migration-only Qt inventory and regression scripts:
  - `scripts/qt_module_classification.py`
  - `scripts/qt_removal_regression.py`
- The root `PySide6` dependency from `requirements.txt`.

## Preserve

- `app/api/main.py` and all other non-Qt backend modules.
- `fairy-v3/`, including its Tauri desktop, Core, capabilities, cloud, and
  worker packages.
- `fairy-desktop/`; it is a web/Tauri migration artifact, not Qt source.
- Historical migration documents under `docs/history/` and
  `docs/platform_migration/`. They may describe Qt as historical context but
  must not advertise a runnable Qt entrypoint.
- The user's untracked root `CLAUDE.md`.

The existing `codex/qt-decommission-prep` commit is reference material only. It
will not be merged or cherry-picked because it includes unrelated backend
changes and replaces `main.py` with another legacy stub instead of deleting it.

## Documentation

Update the root README so the active launch path points only to Fairy V3/Tauri.
Remove the statement that root `main.py` remains available for migration
debugging. Historical documents may retain past-tense references.

## Verification

- `main.py`, `app/assistant_mode.py`, and `app/ui/` do not exist.
- No tracked active source, dependency file, or active test imports `PySide6`;
  historical documentation is exempt.
- No remaining test imports a deleted Qt module.
- Run the remaining applicable root Python tests without Docker or release
  builds.
- Run Fairy V3 TypeScript, Vitest, and Playwright regression gates.
- Confirm test services exit and generated test results are removed.
- Confirm the only unrelated working-tree item remains the untracked
  `CLAUDE.md`.

## Delivery

Commit the removal conventionally, push `main` to the private
`babyzmz/Fairy-LLM` repository, and verify the remote `main` SHA matches local.
