from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from app.ai.llm_client import LLMClient
from app.capabilities.browser_capability import BrowserCapability
from app.capabilities.command_capability import CommandCapability
from app.capabilities.document_capability import DocumentCapability
from app.config import agent_config
from app.models.skill_result import SkillResult
from app.models.tool_result import ToolResult
from app.prompts import build_json_helper_prompt
from app.skills.bundles.runtime_types import BundleRuntimeServices


logger = logging.getLogger(__name__)
EventCallback = Callable[[str, dict[str, Any]], None]
BUNDLE_NAME = "terminal-agent"


class AgentShellSkill:
    def __init__(
        self,
        llm: LLMClient,
        documents: DocumentCapability,
        browser: BrowserCapability,
        commands: CommandCapability,
        *,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.llm = llm
        self.documents = documents
        self.browser = browser
        self.commands = commands
        self.event_callback = event_callback

    def execute(self, user_request: str, allowed_tools: list[str], *, memory_context: str = "") -> SkillResult:
        tool_results: list[ToolResult] = []
        changed_files: list[str] = []
        commands_run: list[str] = []
        validations: list[dict[str, Any]] = []
        warnings: list[str] = []
        sources: list[dict[str, str]] = []
        observations: list[dict[str, str]] = []
        read_cache: dict[str, str] = {}
        inspected_paths: set[str] = set()
        root_path = str(Path.cwd())

        self._emit("agent_loop_started", {"task": user_request})

        root_listing = self.documents.list_dir(root_path, allowed_tools=allowed_tools)
        tool_results.append(ToolResult("list_dir", ok=True, data={"path": root_path}))
        observations.append({"kind": "repo_root", "content": self._summarize_listing(root_listing)})

        search_terms = self._extract_search_terms(user_request)
        for term in search_terms[:2]:
            search_payload = self.documents.search_files(root_path, term, allowed_tools=allowed_tools)
            tool_results.append(ToolResult("search_files", ok=True, data={"pattern": term, "count": len(search_payload.get('results', []))}))
            observations.append({"kind": "search_files", "content": f"pattern={term} -> {self._summarize_search(search_payload)}"})

        for step_index in range(agent_config.agent_max_steps):
            self._emit("agent_step", {"step": step_index + 1})
            action = self._choose_next_action(user_request, observations, changed_files, commands_run, validations, memory_context=memory_context)
            action_type = str(action.get("action", "finish")).strip()
            if not action_type:
                action_type = "finish"

            if action_type == "finish":
                break

            if action_type == "read_file":
                path = str(action.get("path", "")).strip()
                if not path or path in inspected_paths:
                    observations.append({"kind": "read_skip", "content": f"skip duplicate read: {path}"})
                    continue
                payload = self.documents.read_text_file(path, allowed_tools=allowed_tools)
                content = str(payload.get("content", ""))
                read_cache[path] = content
                inspected_paths.add(path)
                tool_results.append(ToolResult("read_text_file", ok=True, data={"path": path}))
                observations.append({"kind": "read_file", "content": f"{path}\n{content[:2500]}"})
                continue

            if action_type == "run_command":
                command = str(action.get("command", "")).strip()
                cwd = str(action.get("cwd", root_path)).strip() or root_path
                if not command:
                    continue
                payload = self.commands.run_command(command, allowed_tools=allowed_tools, cwd=cwd)
                commands_run.append(command)
                tool_results.append(ToolResult("run_command", ok=bool(payload.get("ok", False)), data=payload))
                observations.append(
                    {
                        "kind": "command",
                        "content": f"command={command}\nstdout:\n{str(payload.get('stdout', ''))[:1800]}\nstderr:\n{str(payload.get('stderr', ''))[:1200]}",
                    }
                )
                validations.append(
                    {
                        "command": command,
                        "cwd": cwd,
                        "returncode": int(payload.get("returncode", -1)),
                        "ok": bool(payload.get("ok", False)),
                    }
                )
                continue

            if action_type == "search_web":
                query = str(action.get("query", "")).strip()
                if not query:
                    continue
                payload = self.browser.search(query, allowed_tools=allowed_tools)
                results = list(payload.get("results", []))[:3]
                tool_results.append(ToolResult("search_web", ok=True, data={"query": query, "count": len(results)}))
                observations.append({"kind": "browser_search", "content": self._summarize_search_results(query, results)})
                continue

            if action_type == "open_url":
                url = str(action.get("url", "")).strip()
                if not url:
                    continue
                opened = self.browser.open(url, allowed_tools=allowed_tools)
                extracted = self.browser.extract(opened, allowed_tools=allowed_tools)
                snapshot = self.browser.snapshot(opened, allowed_tools=allowed_tools)
                tool_results.append(ToolResult("open_url", ok=True, data={"url": url}))
                tool_results.append(ToolResult("extract_page_text", ok=True, data={"url": url}))
                tool_results.append(ToolResult("snapshot_page", ok=True, data={"url": url}))
                sources.append({"title": str(extracted.get("title", "") or url), "url": str(extracted.get("url", url))})
                observations.append(
                    {
                        "kind": "browser_page",
                        "content": self._summarize_page(extracted, snapshot),
                    }
                )
                continue

            if action_type == "browser_interact":
                handle_id = str(action.get("handle_id", "")).strip()
                action_name = str(action.get("action_name", "")).strip() or "click"
                selector = str(action.get("selector", "")).strip()
                text_value = str(action.get("text", "")).strip()
                if not handle_id:
                    continue
                interacted = self.browser.interact(
                    handle_id,
                    allowed_tools=allowed_tools,
                    action=action_name,
                    selector=selector,
                    text=text_value,
                )
                tool_results.append(ToolResult("browser_interact", ok=True, data={"handle_id": handle_id, "action": action_name}))
                observations.append({"kind": "browser_interact", "content": self._summarize_page(interacted, interacted)})
                continue
            if action_type == "edit_file":
                path = str(action.get("path", "")).strip()
                instruction = str(action.get("instruction", user_request)).strip() or user_request
                output_mode = str(action.get("output_mode", agent_config.default_document_write_mode)).strip() or agent_config.default_document_write_mode
                if not path:
                    continue
                if path not in read_cache:
                    payload = self.documents.read_text_file(path, allowed_tools=allowed_tools)
                    read_cache[path] = str(payload.get("content", ""))
                    tool_results.append(ToolResult("read_text_file", ok=True, data={"path": path}))
                revision = self.documents.propose_edit(read_cache[path], instruction, allowed_tools=allowed_tools)
                applied = self.documents.apply_patch(path, revision, allowed_tools=allowed_tools, output_mode=output_mode)
                changed_path = str(applied.get("revised_path", "") or path)
                if changed_path and changed_path not in changed_files:
                    changed_files.append(changed_path)
                tool_results.append(ToolResult("propose_edit", ok=True, data={"path": path}))
                tool_results.append(ToolResult("apply_patch", ok=True, data=applied))
                observations.append(
                    {
                        "kind": "edit_file",
                        "content": f"edited={path}\nrevised_path={changed_path}\ndiff:\n{str(applied.get('diff', ''))[:2200]}",
                    }
                )
                continue

            warnings.append(f"Unsupported agent action: {action_type}")
            observations.append({"kind": "unsupported_action", "content": json.dumps(action, ensure_ascii=False)})

        synthesis = self._summarize_session(user_request, observations, changed_files, commands_run, validations, sources, memory_context=memory_context)
        structured = {
            "summary": synthesis.get("summary", ""),
            "changed_files": changed_files,
            "commands_run": commands_run,
            "validations": validations,
            "sources": sources,
            "observations": observations,
        }
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=True,
            summary=synthesis.get("summary", ""),
            structured=structured,
            recommendation=synthesis.get("recommendation", ""),
            sources=sources,
            tool_results=tool_results,
            warnings=warnings,
            response_text=synthesis.get("response_text", ""),
            changed_files=changed_files,
            commands_run=commands_run,
            validations=validations,
        )

    def _choose_next_action(
        self,
        user_request: str,
        observations: list[dict[str, str]],
        changed_files: list[str],
        commands_run: list[str],
        validations: list[dict[str, Any]],
        *,
        memory_context: str = "",
    ) -> dict[str, Any]:
        prompt = {
            "user_request": user_request,
            "changed_files": changed_files,
            "commands_run": commands_run,
            "validations": validations,
            "observations": observations[-8:],
            "available_actions": [
                {"action": "read_file", "path": "relative/or/absolute path"},
                {"action": "run_command", "command": "command text", "cwd": str(Path.cwd())},
                {"action": "search_web", "query": "web query"},
                {"action": "open_url", "url": "https://..."},
                {"action": "browser_interact", "handle_id": "page handle", "action_name": "click|fill|press|scroll", "selector": "css selector", "text": "optional text"},
                {"action": "edit_file", "path": "path", "instruction": "edit request", "output_mode": "overwrite|write_new_copy|preview_only"},
                {"action": "finish", "summary": "done summary"},
            ],
        }
        response = self.llm.execute_task(
            (
                "This is the agent shell planner layer. "
                "Choose exactly one next action in JSON. "
                "Prefer reading files before editing them. "
                "Prefer running commands when the user asks to test, validate, inspect repo, or check status. "
                "Prefer browser search/open when external information is required. "
                "Return strict JSON only."
                + (f"\n\n{memory_context}" if memory_context else "")
            ),
            json.dumps(prompt, ensure_ascii=False),
            max_tokens=220,
            temperature=0.1,
            instruction_label="Tool planner instructions",
        )
        parsed = self._parse_json_object(response.text)
        if parsed:
            return parsed
        return {"action": "finish"}

    def _summarize_session(
        self,
        user_request: str,
        observations: list[dict[str, str]],
        changed_files: list[str],
        commands_run: list[str],
        validations: list[dict[str, Any]],
        sources: list[dict[str, str]],
        *,
        memory_context: str = "",
    ) -> dict[str, str]:
        prompt = {
            "user_request": user_request,
            "observations": observations[-10:],
            "changed_files": changed_files,
            "commands_run": commands_run,
            "validations": validations,
            "sources": sources,
        }
        response = self.llm.execute_task(
            build_json_helper_prompt(
                keys=("summary", "recommendation", "response_text"),
                extra_rules=(
                    "Write concise Chinese.",
                    "response_text should be 3-5 natural sentences.",
                    "Mention changed files, commands run, and validation results when available.",
                ),
            )
            + (f"\n\n{memory_context}" if memory_context else ""),
            json.dumps(prompt, ensure_ascii=False),
            max_tokens=360,
            temperature=0.1,
            instruction_label="Tool helper instructions",
        )
        parsed = self._parse_json_object(response.text)
        if parsed:
            return {
                "summary": str(parsed.get("summary", "")).strip(),
                "recommendation": str(parsed.get("recommendation", "")).strip(),
                "response_text": str(parsed.get("response_text", "")).strip(),
            }

        summary_bits = ["请求已处理。"]
        if changed_files:
            summary_bits.append(f"已产生 {len(changed_files)} 个变更文件。")
        if commands_run:
            summary_bits.append(f"已执行 {len(commands_run)} 条命令。")
        if validations:
            passed = sum(1 for item in validations if item.get("ok"))
            summary_bits.append(f"验证通过 {passed}/{len(validations)}。")
        return {
            "summary": " ".join(summary_bits),
            "recommendation": "如需继续，我可以基于当前结果追加修改或进一步验证。",
            "response_text": " ".join(summary_bits + ["如需继续，我可以继续执行下一轮检查。"]),
        }

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.event_callback is not None:
            self.event_callback(event, payload)

    def _extract_search_terms(self, user_request: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_./-]+|[\u4e00-\u9fff]{2,}", user_request)
        filtered: list[str] = []
        for token in tokens:
            lowered = token.lower()
            if lowered in {"帮我", "当前", "这个", "那个", "修改", "实现", "看看", "项目", "代码", "仓库"}:
                continue
            if token not in filtered:
                filtered.append(token)
        return filtered[:4]

    def _summarize_listing(self, payload: dict[str, Any]) -> str:
        entries = payload.get("entries", []) if isinstance(payload.get("entries"), list) else []
        preview = []
        for item in entries[:16]:
            if not isinstance(item, dict):
                continue
            kind = "dir" if item.get("is_dir") else "file"
            preview.append(f"{kind}:{item.get('name', '')}")
        return ", ".join(preview)

    def _summarize_search(self, payload: dict[str, Any]) -> str:
        results = payload.get("results", []) if isinstance(payload.get("results"), list) else []
        preview = [str(item.get("path", "")) for item in results[:8] if isinstance(item, dict)]
        return "\n".join(preview)

    def _summarize_search_results(self, query: str, results: list[dict[str, Any]]) -> str:
        lines = [f"query={query}"]
        for item in results[:3]:
            lines.append(f"- {item.get('title', '')}: {item.get('url', '')}")
        return "\n".join(lines)

    def _summarize_page(self, extracted: dict[str, Any], snapshot: dict[str, Any]) -> str:
        lines = [
            f"title={extracted.get('title', '')}",
            f"url={extracted.get('url', '')}",
            f"meta={extracted.get('meta_description', '')}",
            f"headings={snapshot.get('headings', [])}",
            f"key_values={snapshot.get('key_values', [])}",
            f"preview={str(extracted.get('body_text', ''))[:1800]}",
        ]
        return "\n".join(lines)

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        text = text.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None


def run_bundle(
    *,
    user_request: str,
    allowed_tools: list[str],
    attachments: list[str],
    memory_context: str,
    bundle: object,
    prompt_context: object,
    services: BundleRuntimeServices,
    route_context: object | None,
    request_origin: str,
    request_id: str,
) -> SkillResult:
    del attachments, bundle, prompt_context, route_context, request_origin, request_id
    if services.documents is None or services.browser is None or services.commands is None:
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=False,
            summary="Terminal agent dependencies unavailable.",
            response_text="请求已接收，但当前终端代理能力暂不可用。",
            structured={"bundle_name": BUNDLE_NAME},
        )
    runner = AgentShellSkill(
        llm=services.llm,
        documents=services.documents,
        browser=services.browser,
        commands=services.commands,
        event_callback=services.event_callback,
    )
    return runner.execute(user_request, allowed_tools, memory_context=memory_context)


