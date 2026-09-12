import json
import runpy
from pathlib import Path

import pytest
from fairy_core.providers import ProviderRegistry

from fairy_capabilities import composition


def test_failed_probe_exits_nonzero_without_exposing_private_error(monkeypatch, capsys):
    monkeypatch.setenv("FAIRY_PROVIDER_PROBE_MODEL", "fixture-unavailable")
    # Empty real registry rejects locally: no HTTP requests or credentials needed.
    monkeypatch.setattr(composition, "build_provider_registry", lambda: ProviderRegistry())
    script = Path(__file__).resolve().parents[2] / "scripts" / "provider_stream_probe.py"
    with pytest.raises(SystemExit) as captured:
        runpy.run_path(str(script), run_name="__main__")
    assert captured.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["result"] == "failed"
    assert report["error_type"] == "ProviderUnavailableError"
    assert "message" not in report
