from __future__ import annotations

import atexit
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

import requests

from app.ai.llm_client_file_processor import FileProcessor
from app.ai.llm_client_server_manager import ServerManager
from app.ai.llm_client_transport import ChatTransport
from app.config import llm_config
from app.prompts import build_core_system_prompt, build_secondary_instruction_block
from app.providers.provider_router import ProviderRouter
from app.providers.provider_schema import ProviderHTTPResponse


Message = Dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    text: str
    raw: Dict[str, Any] | None = None


@dataclass(slots=True)
class PreparedRequest:
    messages: List[Message]
    has_images: bool
    used_web: bool = False
    web_page_count: int = 0


class LLMClient:
    def __init__(self, config: Any = llm_config) -> None:
        self.config = config
        self.file_processor = FileProcessor(config)
        self.server_manager = ServerManager(config)
        self.transport = ChatTransport(
            config,
            self._build_headers,
            self._ensure_utf8_response,
        )
        self.provider_router = ProviderRouter()
        atexit.register(self.shutdown)

    @property
    def _server_started(self) -> bool:
        return self.server_manager.server_started

    @_server_started.setter
    def _server_started(self, value: bool) -> None:
        self.server_manager.server_started = value

    @property
    def _startup_error(self) -> str | None:
        return self.server_manager.startup_error

    @_startup_error.setter
    def _startup_error(self, value: str | None) -> None:
        self.server_manager.startup_error = value

    def try_start_server(self) -> None:
        self.server_manager.try_start_server()

    def start_server_background(self) -> None:
        if self.config.runtime_mode != "local_server" or not self.config.auto_start_server:
            return
        self.server_manager.start_background()

    def _build_headers(self) -> Dict[str, str]:
        return self.server_manager.build_headers()

    def _ensure_utf8_response(self, resp: requests.Response) -> requests.Response:
        return self.server_manager.ensure_utf8_response(resp)

    def _format_http_error(self, resp: Any) -> str:
        if isinstance(resp, ProviderHTTPResponse):
            return resp.error_message or resp.text or "Cloud provider request failed."
        return self.server_manager.format_http_error(resp)

    def _ensure_server_running(self) -> None:
        self.server_manager.ensure_server_running()

    def shutdown(self) -> None:
        self.server_manager.shutdown()

    def get_runtime_status(self) -> str:
        base = self.server_manager.get_runtime_status()
        decision = self.provider_router.current_route_decision()
        status = f"{base} | mode={decision.active_mode} | provider={decision.provider_id or 'local_server'}"
        if decision.model:
            status += f" | model={decision.model}"
        if decision.route_type:
            status += f" | route={decision.route_type}"
        if decision.fallback_used:
            status += f" | fallback={decision.fallback_reason}"
        return status

    def get_runtime_snapshot(self) -> Dict[str, Any]:
        snapshot = self.server_manager.get_runtime_snapshot()
        snapshot.update(self.provider_router.runtime_snapshot())
        return snapshot

    def _build_system_prompt(self) -> str:
        now = datetime.now()
        active_mode = self.provider_router.mode_manager.refresh()
        return build_core_system_prompt(now=now, active_mode=active_mode)

    def _build_runtime_system_prompt(
        self,
        additional_instructions: str = "",
        *,
        instruction_label: str = "Task instructions",
    ) -> str:
        prompt = self._build_system_prompt()
        overlay = self.provider_router.build_game_overlay()
        if overlay:
            prompt += "\n\n" + build_secondary_instruction_block("Mode overlay", overlay)
        if additional_instructions.strip():
            prompt += "\n\n" + build_secondary_instruction_block(instruction_label, additional_instructions)
        return prompt

    def try_local_response(self, user_input: str) -> str | None:
        text = user_input.strip()
        if not text:
            return None
        normalized = re.sub(r'\s+', '', text.lower())
        now = datetime.now()

        time_patterns = ('当前时间', '现在几点', '几点了', 'time', 'what time', 'current time')
        date_patterns = ('今天几号', '今天日期', '今天星期几', 'what date', 'what day', 'today date')

        if any(pattern in normalized for pattern in time_patterns):
            return f'请求已接收。当前本地时间是 {now.strftime("%Y-%m-%d %H:%M:%S")}。'
        if any(pattern in normalized for pattern in date_patterns):
            weekday_names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
            weekday = weekday_names[now.weekday()]
            return f'请求已接收。今天是 {now.strftime("%Y-%m-%d")}，{weekday}。'
        return None

    def _clip_memory_hint(self, memory_hint: str) -> str:
        cleaned = memory_hint.strip()
        limit = self.config.max_memory_hint_chars
        if not cleaned or len(cleaned) <= limit:
            return cleaned
        head = int(limit * 0.6)
        tail = max(120, limit - head - 20)
        return f'{cleaned[:head].rstrip()}\n...[truncated]...\n{cleaned[-tail:].lstrip()}'

    def _estimate_content_tokens(self, content: Any) -> int:
        if isinstance(content, str):
            return max(1, math.ceil(len(content) / max(self.config.approx_chars_per_token, 1.0)))
        if isinstance(content, list):
            total = 0
            for block in content:
                if not isinstance(block, dict):
                    total += self._estimate_content_tokens(str(block))
                    continue
                if block.get('type') == 'text':
                    total += self._estimate_content_tokens(block.get('text', ''))
                elif block.get('type') == 'image_url':
                    total += self.config.image_token_cost
                else:
                    total += self._estimate_content_tokens(str(block))
            return total
        return self._estimate_content_tokens(str(content))

    def _estimate_message_tokens(self, message: Message) -> int:
        return 8 + self._estimate_content_tokens(message.get('content', ''))

    def _input_budget_tokens(self, output_tokens: int) -> int:
        return max(512, self.config.ctx_size - output_tokens - self.config.context_margin_tokens)

    def _trim_system_prompt(self, system_message: Message, allowed_tokens: int) -> Message:
        content = str(system_message.get('content', '')).strip()
        current_tokens = self._estimate_content_tokens(content)
        if current_tokens <= allowed_tokens:
            return system_message

        allowed_chars = max(
            self.config.min_system_prompt_chars,
            int(allowed_tokens * max(self.config.approx_chars_per_token, 1.0)),
        )
        if len(content) <= allowed_chars:
            return system_message

        head = int(allowed_chars * 0.7)
        tail = max(180, allowed_chars - head - 20)
        clipped = f'{content[:head].rstrip()}\n...[truncated system context]...\n{content[-tail:].lstrip()}'
        return {'role': 'system', 'content': clipped}

    def _prune_messages_for_budget(self, messages: List[Message], output_tokens: int) -> List[Message]:
        if len(messages) <= 2:
            return messages

        budget = self._input_budget_tokens(output_tokens)
        system_message = dict(messages[0])
        user_message = messages[-1]
        history = [dict(item) for item in messages[1:-1]]

        if self.config.max_history_messages > 0 and len(history) > self.config.max_history_messages:
            history = history[-self.config.max_history_messages :]

        candidate = [system_message, *history, user_message]
        while history and sum(self._estimate_message_tokens(item) for item in candidate) > budget:
            history.pop(0)
            candidate = [system_message, *history, user_message]

        current_tokens = sum(self._estimate_message_tokens(item) for item in candidate)
        if current_tokens > budget:
            other_tokens = sum(self._estimate_message_tokens(item) for item in [*history, user_message])
            system_message = self._trim_system_prompt(system_message, max(64, budget - other_tokens - 8))
            candidate = [system_message, *history, user_message]

        return candidate

    def _build_hidden_analysis_prompt(self, has_images: bool) -> str:
        if has_images:
            return (
                'You are an internal vision analysis pass. '
                'Return short factual bullet notes only. '
                'Do not answer the user directly.'
            )
        return (
            'You are an internal reasoning pass. '
            'Return short factual notes only. '
            'Do not answer the user directly.'
        )

    def _compose_messages(
        self,
        system_prompt: str,
        history: Iterable[Message],
        user_content: str | list[dict[str, Any]],
        hidden_notes: str = '',
    ) -> List[Message]:
        merged_system_prompt = system_prompt.strip()
        if hidden_notes.strip():
            merged_system_prompt += '\n\n[Hidden analysis notes for internal use only]\n' + hidden_notes.strip()

        messages: List[Message] = [{'role': 'system', 'content': merged_system_prompt}]
        for item in history:
            if item.get('role') == 'system':
                extra = str(item.get('content', '')).strip()
                if extra:
                    messages[0]['content'] += f'\n\n[Additional system context]\n{extra}'
                continue
            messages.append(dict(item))
        messages.append({'role': 'user', 'content': user_content})
        return messages

    def _messages_have_images(self, messages: Iterable[Message]) -> bool:
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    return True
        return False

    def _local_post_chat_completion(self, messages: List[Message], max_tokens: int, temperature: float) -> requests.Response:
        self._ensure_server_running()
        return self.transport.post_chat_completion(messages, max_tokens=max_tokens, temperature=temperature)

    def _local_post_chat_completion_stream(self, messages: List[Message], max_tokens: int, temperature: float) -> requests.Response:
        self._ensure_server_running()
        return self.transport.post_chat_completion_stream(messages, max_tokens=max_tokens, temperature=temperature)

    def _post_chat_completion(self, messages: List[Message], max_tokens: int, temperature: float) -> Any:
        return self.provider_router.complete(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
            has_images=self._messages_have_images(messages),
            local_call=lambda: self._local_post_chat_completion(messages, max_tokens, temperature),
        )

    def _post_chat_completion_stream(self, messages: List[Message], max_tokens: int, temperature: float) -> Any:
        return self.provider_router.complete(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            has_images=self._messages_have_images(messages),
            local_call=lambda: self._local_post_chat_completion_stream(messages, max_tokens, temperature),
        )

    def _iter_stream_text(self, resp: Any) -> Iterable[str]:
        if isinstance(resp, ProviderHTTPResponse):
            yield from resp.iter_text()
            return
        yield from self.transport.iter_stream_text(resp)

    def _generate_hidden_notes(
        self,
        history: Iterable[Message],
        user_content: str | list[dict[str, Any]],
        has_images: bool,
        force_safe_transcode: bool,
    ) -> str:
        if not self.config.hidden_deliberation:
            return ''

        analysis_messages = self._compose_messages(
            self._build_hidden_analysis_prompt(has_images),
            history,
            user_content,
        )
        analysis_tokens = self.config.hidden_vision_analysis_tokens if has_images else self.config.hidden_text_analysis_tokens
        analysis_messages = self._prune_messages_for_budget(analysis_messages, analysis_tokens)
        resp = self._post_chat_completion(analysis_messages, max_tokens=analysis_tokens, temperature=0.2)
        if not resp.ok:
            error_text = self._format_http_error(resp)
            if has_images and (not force_safe_transcode) and 'Failed to parse input at pos' in error_text:
                return ''
            return ''
        try:
            data = resp.json()
            return str(data['choices'][0]['message']['content']).strip()
        except Exception:
            return ''

    def _prepare_request(
        self,
        history: Iterable[Message],
        user_input: str,
        memory_hint: str = '',
        attachment_paths: Iterable[str | Path] | None = None,
        force_safe_transcode: bool = False,
        status_callback: Callable[[str], None] | None = None,
    ) -> PreparedRequest:
        system_prompt = self._build_runtime_system_prompt()
        clipped_memory = self._clip_memory_hint(memory_hint)
        if clipped_memory:
            system_prompt += f'\n\n[Known user context]\n{clipped_memory}'

        user_content, _, has_images = self._build_user_message_content(
            user_input,
            attachment_paths,
            force_safe_transcode=force_safe_transcode,
        )
        hidden_notes = self._generate_hidden_notes(history, user_content, has_images, force_safe_transcode)
        messages = self._compose_messages(system_prompt, history, user_content, hidden_notes)
        output_tokens = self.config.vision_max_tokens if has_images else self.config.text_max_tokens
        messages = self._prune_messages_for_budget(messages, output_tokens)
        return PreparedRequest(messages=messages, has_images=has_images)

    def _safe_read_text(self, path: Path) -> str:
        return self.file_processor.safe_read_text(path)

    def _extract_pdf_text(self, path: Path) -> str:
        return self.file_processor.extract_pdf_text(path)

    def _extract_docx_text(self, path: Path) -> str:
        return self.file_processor.extract_docx_text(path)

    def _extract_xlsx_text(self, path: Path) -> str:
        return self.file_processor.extract_xlsx_text(path)

    def _extract_file_text(self, path: Path) -> str:
        return self.file_processor.extract_file_text(path)

    def _make_image_block(self, path: Path, force_safe_transcode: bool = False) -> dict[str, Any]:
        return self.file_processor.make_image_block(path, force_safe_transcode=force_safe_transcode)

    def _normalize_attachments(self, attachment_paths: Iterable[str | Path] | None) -> list[Path]:
        return self.file_processor.normalize_attachments(attachment_paths)

    def _build_user_message_content(
        self,
        user_input: str,
        attachment_paths: Iterable[str | Path] | None,
        force_safe_transcode: bool = False,
    ) -> tuple[str | list[dict[str, Any]], list[str], bool]:
        return self.file_processor.build_user_message_content(
            user_input,
            attachment_paths,
            force_safe_transcode=force_safe_transcode,
        )

    def chat(
        self,
        history: Iterable[Message],
        user_input: str,
        memory_hint: str = '',
        attachment_paths: Iterable[str | Path] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        local_text = self.try_local_response(user_input)
        if local_text and not attachment_paths:
            return LLMResponse(text=local_text, raw={'source': 'local_fast_path'})

        if self.provider_router.mode_manager.refresh() != "game_mode" and not self._server_started:
            try:
                self._ensure_server_running()
                self._startup_error = None
            except Exception as exc:
                error_msg = self._startup_error or str(exc)
                return LLMResponse(
                    text=(
                        '模型服务未就绪。\n'
                        f'当前模式: {self.config.runtime_mode}\n'
                        f'服务地址: {self.config.api_base}\n'
                        f'启动错误: {error_msg}'
                    ),
                    raw=None,
                )

        def do_request(
            *,
            force_safe_transcode: bool = False,
            history_override: Iterable[Message] | None = None,
            memory_hint_override: str | None = None,
        ) -> requests.Response:
            prepared = self._prepare_request(
                history if history_override is None else history_override,
                user_input,
                memory_hint=memory_hint if memory_hint_override is None else memory_hint_override,
                attachment_paths=attachment_paths,
                force_safe_transcode=force_safe_transcode,
                status_callback=status_callback,
            )
            max_tokens = self.config.vision_max_tokens if prepared.has_images else self.config.text_max_tokens
            return self._post_chat_completion(prepared.messages, max_tokens=max_tokens, temperature=0.7)

        try:
            resp = do_request(force_safe_transcode=False)
            if not resp.ok:
                error_text = self._format_http_error(resp)
                if 'exceeds the available context size' in error_text:
                    resp = do_request(force_safe_transcode=False, history_override=[], memory_hint_override='')
                    if resp.ok:
                        data = resp.json()
                        return LLMResponse(text=str(data['choices'][0]['message']['content']).strip(), raw=data)
                    error_text = self._format_http_error(resp)
                if attachment_paths and 'Failed to parse input at pos' in error_text:
                    resp = do_request(force_safe_transcode=True)
                    if not resp.ok:
                        return LLMResponse(text=f'本地模型请求失败: {self._format_http_error(resp)}', raw=None)
                else:
                    return LLMResponse(text=f'本地模型请求失败: {error_text}', raw=None)
            data = resp.json()
            text = str(data['choices'][0]['message']['content']).strip()
            return LLMResponse(text=text, raw=data)
        except Exception as exc:
            return LLMResponse(text=f'本地模型请求失败: {exc}', raw=None)

    def stream_task(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        attachment_paths: Iterable[str | Path] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        instruction_label: str = "Task instructions",
    ) -> Iterable[str]:
        if self.provider_router.mode_manager.refresh() != "game_mode" and not self._server_started:
            self._ensure_server_running()
            self._startup_error = None

        token_budget = max_tokens or self.config.text_max_tokens

        def do_request(force_safe_transcode: bool = False) -> Any:
            user_content, _, has_images = self._build_user_message_content(
                user_prompt,
                attachment_paths,
                force_safe_transcode=force_safe_transcode,
            )
            image_token_budget = max_tokens or self.config.vision_max_tokens
            system_content = self._build_runtime_system_prompt(
                system_prompt,
                instruction_label=instruction_label,
            )
            messages: List[Message] = [
                {'role': 'system', 'content': system_content},
                {'role': 'user', 'content': user_content},
            ]
            messages = self._prune_messages_for_budget(
                messages,
                image_token_budget if has_images else token_budget,
            )
            return self._post_chat_completion_stream(
                messages,
                max_tokens=image_token_budget if has_images else token_budget,
                temperature=temperature,
            )

        resp: Any | None = None
        try:
            resp = do_request(force_safe_transcode=False)
            if not resp.ok:
                error_text = self._format_http_error(resp)
                if attachment_paths and 'Failed to parse input at pos' in error_text:
                    resp.close()
                    resp = do_request(force_safe_transcode=True)
                    if not resp.ok:
                        raise RuntimeError(self._format_http_error(resp))
                else:
                    raise RuntimeError(error_text)
            yield from self._iter_stream_text(resp)
        finally:
            if resp is not None:
                resp.close()

    def execute_task(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        attachment_paths: Iterable[str | Path] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        instruction_label: str = "Task instructions",
    ) -> LLMResponse:
        if self.provider_router.mode_manager.refresh() != "game_mode" and not self._server_started:
            try:
                self._ensure_server_running()
                self._startup_error = None
            except Exception as exc:
                error_msg = self._startup_error or str(exc)
                return LLMResponse(text=f'模型服务未就绪: {error_msg}', raw=None)

        token_budget = max_tokens or self.config.text_max_tokens

        def do_request(force_safe_transcode: bool = False) -> requests.Response:
            user_content, _, has_images = self._build_user_message_content(
                user_prompt,
                attachment_paths,
                force_safe_transcode=force_safe_transcode,
            )
            image_token_budget = max_tokens or self.config.vision_max_tokens
            system_content = self._build_runtime_system_prompt(
                system_prompt,
                instruction_label=instruction_label,
            )
            messages: List[Message] = [
                {'role': 'system', 'content': system_content},
                {'role': 'user', 'content': user_content},
            ]
            messages = self._prune_messages_for_budget(
                messages,
                image_token_budget if has_images else token_budget,
            )
            return self._post_chat_completion(
                messages,
                max_tokens=image_token_budget if has_images else token_budget,
                temperature=temperature,
            )

        try:
            resp = do_request(force_safe_transcode=False)
            if not resp.ok:
                error_text = self._format_http_error(resp)
                if attachment_paths and 'Failed to parse input at pos' in error_text:
                    resp = do_request(force_safe_transcode=True)
                    if not resp.ok:
                        return LLMResponse(text=f'任务执行失败: {self._format_http_error(resp)}', raw=None)
                else:
                    return LLMResponse(text=f'任务执行失败: {error_text}', raw=None)
            data = resp.json()
            return LLMResponse(text=str(data['choices'][0]['message']['content']).strip(), raw=data)
        except Exception as exc:
            return LLMResponse(text=f'任务执行失败: {exc}', raw=None)
