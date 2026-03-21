from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import requests

from app.ai.llm_client import LLMClient, Message


def stream_chat(
    llm: LLMClient,
    history: Iterable[Message],
    user_input: str,
    memory_hint: str = "",
    attachment_paths: Iterable[str | Path] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> Iterable[str]:
    local_text = llm.try_local_response(user_input)
    if local_text and not attachment_paths:
        yield local_text
        return

    if not llm._server_started:  # noqa: SLF001
        llm._ensure_server_running()  # noqa: SLF001
        llm._startup_error = None  # noqa: SLF001

    def do_request(
        force_safe_transcode: bool = False,
        history_override: Iterable[Message] | None = None,
        memory_hint_override: str | None = None,
    ) -> requests.Response:
        prepared = llm._prepare_request(  # noqa: SLF001
            history if history_override is None else history_override,
            user_input,
            memory_hint=memory_hint if memory_hint_override is None else memory_hint_override,
            attachment_paths=attachment_paths,
            force_safe_transcode=force_safe_transcode,
            status_callback=status_callback,
        )
        return llm._post_chat_completion_stream(  # noqa: SLF001
            prepared.messages,
            max_tokens=llm.config.vision_max_tokens if prepared.has_images else llm.config.text_max_tokens,
            temperature=0.7,
        )

    resp: requests.Response | None = None
    try:
        resp = do_request(force_safe_transcode=False)
        if not resp.ok:
            error_text = llm._format_http_error(resp)  # noqa: SLF001
            if "exceeds the available context size" in error_text:
                resp.close()
                resp = do_request(
                    force_safe_transcode=False,
                    history_override=[],
                    memory_hint_override="",
                )
                if not resp.ok:
                    error_text = llm._format_http_error(resp)  # noqa: SLF001
            if attachment_paths and "Failed to parse input at pos" in error_text:
                resp.close()
                resp = do_request(force_safe_transcode=True)
                if not resp.ok:
                    raise RuntimeError(llm._format_http_error(resp))  # noqa: SLF001
            else:
                raise RuntimeError(error_text)
        yield from llm._iter_stream_text(resp)  # noqa: SLF001
    finally:
        if resp is not None:
            resp.close()
