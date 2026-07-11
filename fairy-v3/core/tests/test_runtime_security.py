from __future__ import annotations

from pathlib import Path

import pytest

from fairy_core.domain.errors import ScopeViolationError
from fairy_core.runtime.models import ExecutorRuntimeState, RuntimeStartResult
from fairy_core.security.path_guard import PathGuard


@pytest.mark.parametrize(
    "candidate",
    (
        r"\\.\PhysicalDrive0",
        r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\secret.txt",
        r"C:relative-drive-path.txt",
        r"src\payload.txt::$DATA",
        "src/file.txt\x00suffix",
    ),
)
def test_path_guard_rejects_windows_device_drive_ads_and_nul_forms(
    tmp_path: Path,
    candidate: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())

    with pytest.raises(ScopeViolationError) as captured:
        guard.validate_write(candidate)

    assert captured.value.code == "PATH_OUT_OF_SCOPE"


def test_path_guard_matches_forbidden_roots_case_insensitively_on_windows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    forbidden = root / ".git"
    forbidden.mkdir(parents=True)
    guard = PathGuard(
        project_root=root,
        allowed_roots=(root,),
        forbidden_roots=(forbidden,),
    )

    with pytest.raises(ScopeViolationError):
        guard.validate_write(".GIT/config")


@pytest.mark.parametrize(
    ("host", "port", "url"),
    (
        ("0.0.0.0", 43125, "http://0.0.0.0:43125/preview/"),
        ("127.0.0.1", 43125, "http://localhost:43125/preview/"),
        ("127.0.0.1", 43125, "http://127.0.0.1:43125/preview/?token=secret"),
        ("127.0.0.1", 43125, "http://user@127.0.0.1:43125/preview/"),
        ("127.0.0.1", 0, "http://127.0.0.1:0/preview/"),
    ),
)
def test_runtime_result_rejects_non_loopback_or_rebound_endpoints(
    host: str,
    port: int,
    url: str,
) -> None:
    with pytest.raises(ValueError):
        RuntimeStartResult(
            executor_handle="opaque-handle",
            host=host,
            port=port,
            url=url,
            state=ExecutorRuntimeState.RUNNING,
        )
