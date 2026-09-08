from __future__ import annotations

import json
from pathlib import Path

import pytest

from fairy_capabilities.stdio import build_composed_local_dispatcher


@pytest.mark.parametrize("broken", [False, True])
def test_main_closes_composed_resources_when_the_input_stream_ends(monkeypatch, broken):
    from fairy_capabilities import stdio

    closed = []

    class Dispatcher:
        def close(self):
            closed.append(True)

    def stream(*args):
        if broken:
            raise BrokenPipeError("client disconnected")

    monkeypatch.setattr(stdio, "build_composed_local_dispatcher", lambda _: Dispatcher())
    monkeypatch.setattr(stdio, "process_stream", stream)
    if broken:
        with pytest.raises(BrokenPipeError):
            stdio.main()
    else:
        stdio.main()
    assert closed == [True]


def test_composed_stdio_injects_public_provider_metadata_only(tmp_path: Path) -> None:
    secret = "stdio-test-secret"
    environment = {
        "FAIRY_PROVIDER_PROFILES_JSON": json.dumps(
            [
                {
                    "id": "openrouter-free",
                    "display_name": "OpenRouter Free",
                    "kind": "openai_compatible",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model_id": "cohere/north-mini-code:free",
                    "capabilities": ["text", "tools"],
                    "credential_ref": "openrouter",
                    "fallback_profile_id": None,
                    "timeout_seconds": 60,
                    "enabled": True,
                }
            ]
        ),
        "FAIRY_PROVIDER_SECRET_REFS_JSON": json.dumps(
            {"openrouter": "FAIRY_PROVIDER_SECRET_OPENROUTER"}
        ),
        "FAIRY_PROVIDER_SECRET_OPENROUTER": secret,
    }
    dispatcher = build_composed_local_dispatcher(tmp_path, environment=environment)
    try:
        response = dispatcher.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "providers.list",
                "params": {},
            }
        )
    finally:
        dispatcher.close()

    encoded = json.dumps(response, sort_keys=True)
    assert response["result"]["items"][0]["id"] == "openrouter-free"
    assert response["result"]["items"][0]["credential_configured"] is True
    assert secret not in encoded
    assert "credential_ref" not in encoded
