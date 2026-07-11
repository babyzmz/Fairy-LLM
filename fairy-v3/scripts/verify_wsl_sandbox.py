from __future__ import annotations

import io
import json
import zipfile
from uuid import uuid4

from fairy_core.sandbox.models import SandboxNetworkPolicy, SandboxRequest
from fairy_core.sandbox.wsl import WslSandboxExecutor


def _scratch_archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(".fairy-smoke", b"")
    return output.getvalue()


def main() -> int:
    request = SandboxRequest.create(
        job_id=uuid4(),
        project_id=None,
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=None,
        scope_digest="a" * 64,
        workspace_generation=1,
        lease_fence=1,
        argv=("/usr/bin/python3", "-c", "print('fairy-sandbox-ok')"),
        cwd=".",
        environment={},
        timeout_seconds=30,
        output_limit_bytes=32_768,
        network_policy=SandboxNetworkPolicy.NONE,
        workspace_archive=_scratch_archive(),
    )
    result = WslSandboxExecutor().execute(request)
    if result.exit_code != 0 or result.stdout != b"fairy-sandbox-ok\n":
        raise RuntimeError("FairySandbox smoke result did not match")
    print(
        json.dumps(
            {
                "executor": result.executor,
                "executor_version": result.executor_version,
                "job_id": str(result.job_id),
                "status": result.status.value,
                "stdout_sha256": result.stdout_sha256,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
