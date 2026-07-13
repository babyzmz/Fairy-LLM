from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

from fairy_core.domain.execution import RuntimeKind
from fairy_core.runtime.models import DynamicRuntimeStart
from fairy_core.runtime.supervisor import WslDynamicRuntimeExecutor

DEPENDENCY_KEY = "b" * 64


def _workspace_archive() -> bytes:
    source = (
        "const http=require('http');"
        "const port=Number(process.env.PORT);"
        "http.createServer((_request,response)=>{"
        "response.writeHead(200,{'content-type':'text/plain'});"
        "response.end('fairy-scratch-preview-ok');"
        "}).listen(port,'127.0.0.1');"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.js", source)
    return output.getvalue()


def main() -> int:
    content = _workspace_archive()
    request = DynamicRuntimeStart(
        project_id=None,
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        runtime_id=uuid4(),
        preview_id=uuid4(),
        project_root=Path.cwd(),
        execution_target="local",
        kind=RuntimeKind.WSL_PROJECT,
        adapter="node_http",
        scope_digest="a" * 64,
        workspace_generation=1,
        lease_fence=1,
        argv=("node", "index.js"),
        cwd=".",
        readiness_path="/",
        startup_timeout_seconds=30,
        dependency_key=DEPENDENCY_KEY,
        workspace_archive=content,
        archive_sha256=hashlib.sha256(content).hexdigest(),
    )
    executor = WslDynamicRuntimeExecutor()
    started = executor.start_dynamic(request)
    try:
        probed = executor.probe(started.executor_handle)
    finally:
        stopped = executor.stop(started.executor_handle)
    if probed.state.value != "running" or not stopped.stopped:
        raise RuntimeError("projectless scratch Runtime lifecycle did not complete")
    print(
        json.dumps(
            {
                "project_id": None,
                "workspace_id": str(request.workspace_id),
                "state": started.state.value,
                "probe": probed.state.value,
                "url": started.url,
                "stopped": stopped.stopped,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
