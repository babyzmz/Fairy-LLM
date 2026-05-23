from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.capabilities.document_capability import DocumentCapability
from app.config import agent_config
from app.models.skill_result import SkillResult
from app.models.tool_result import ToolResult
from app.skills.bundles.runtime_types import BundleRuntimeServices


logger = logging.getLogger(__name__)
BUNDLE_NAME = "document-editing"


class DocumentEditingRuntime:
    def __init__(self, documents: DocumentCapability, llm_helper: Any) -> None:
        self.documents = documents
        self.llm_helper = llm_helper

    def execute(
        self,
        user_request: str,
        allowed_tools: list[str],
        *,
        attachment_paths: list[str] | None = None,
        memory_context: str = "",
    ) -> SkillResult:
        tool_results: list[ToolResult] = []
        warnings: list[str] = []
        target_path = self._resolve_target_path(user_request, attachment_paths or [], allowed_tools, warnings, tool_results)
        if not target_path:
            return SkillResult(
                skill_name=BUNDLE_NAME,
                success=False,
                summary="未定位到目标文档。",
                warnings=warnings or ["需要提供文档路径，或者直接上传文档。"],
                tool_results=tool_results,
                response_text="请求已接收，但当前没有明确的目标文档。请提供文件路径，或者直接上传文档。",
            )

        logger.info("document_bundle_read path=%s", target_path)
        read_payload = self.documents.read_text_file(target_path, allowed_tools=allowed_tools)
        tool_results.append(ToolResult("read_text_file", ok=True, data={"path": target_path}))
        content = str(read_payload.get("content", ""))

        summary_payload = self.documents.summarize_document(content, allowed_tools=allowed_tools)
        sections_payload = self.documents.extract_sections(content, allowed_tools=allowed_tools)
        tool_results.append(ToolResult("summarize_document", ok=True, data=summary_payload))
        tool_results.append(ToolResult("extract_sections", ok=True, data=sections_payload))

        revise_requested = any(token in user_request for token in ("修改", "润色", "重写", "改写", "保存为新文件"))
        revised_path = ""
        proposed_changes = ""

        if revise_requested:
            revision_payload = self.documents.propose_edit(content, user_request, allowed_tools=allowed_tools)
            tool_results.append(ToolResult("propose_edit", ok=True, data={"diff": revision_payload.get("diff", "")}))
            applied = self.documents.apply_patch(
                target_path,
                revision_payload,
                allowed_tools=allowed_tools,
                output_mode=agent_config.default_document_write_mode,
            )
            tool_results.append(ToolResult("apply_patch", ok=True, data=applied))
            revised_path = str(applied.get("revised_path", ""))
            proposed_changes = str(applied.get("diff", ""))

        synthesis = self.llm_helper.summarize_document_result(
            user_request,
            target_path,
            str(summary_payload.get("summary", "")),
            list(sections_payload.get("sections", [])),
            revised_path,
            warnings,
            memory_context=memory_context,
        )
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=True,
            summary=str(synthesis.get("summary", "")),
            structured={
                "summary": str(synthesis.get("summary", "")),
                "key_points": list(sections_payload.get("sections", [])),
                "proposed_changes": proposed_changes,
                "revised_file_path": revised_path,
                "warnings": warnings,
            },
            recommendation=str(synthesis.get("recommendation", "")),
            tool_results=tool_results,
            warnings=warnings,
            response_text=str(synthesis.get("response_text", "")),
        )

    def _resolve_target_path(
        self,
        user_request: str,
        attachment_paths: list[str],
        allowed_tools: list[str],
        warnings: list[str],
        tool_results: list[ToolResult],
    ) -> str:
        if attachment_paths:
            return attachment_paths[0]

        path_matches = re.findall(r"([A-Za-z]:\\[^\s]+|[~./\w\-\\]+\.(?:txt|md|json|csv|py|ya?ml))", user_request)
        if path_matches:
            return path_matches[0]

        search_payload = self.documents.search_files(str(Path.cwd()), ".md", allowed_tools=allowed_tools)
        tool_results.append(ToolResult("search_files", ok=True, data=search_payload))
        results = list(search_payload.get("results", []))
        if len(results) == 1:
            return str(results[0].get("path", ""))

        warnings.append("未从请求里解析出明确路径。")
        return ""


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
    del bundle, prompt_context, route_context, request_origin, request_id
    documents = services.documents
    if documents is None:
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=False,
            summary="Document capability unavailable.",
            response_text="请求已接收，但当前文档能力暂不可用。",
            structured={"bundle_name": BUNDLE_NAME},
        )
    runtime = DocumentEditingRuntime(documents=documents, llm_helper=services.llm_helper or services.llm)
    return runtime.execute(
        user_request,
        allowed_tools,
        attachment_paths=list(attachments or []),
        memory_context=memory_context,
    )
