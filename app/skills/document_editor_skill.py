from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.capabilities.document_capability import DocumentCapability
from app.config import agent_config
from app.models.skill_result import SkillResult
from app.models.tool_result import ToolResult
from app.skills.skill_spec import SkillSpec


logger = logging.getLogger(__name__)


class DocumentEditorSkill:
    SPEC = SkillSpec(
        name="document_editor_skill",
        description="Read, summarize, extract and revise local documents.",
        trigger_hints=("文件", "文档", "markdown", "总结", "修改", "保存为新文件"),
        allowed_tools=(
            "list_dir",
            "read_text_file",
            "search_files",
            "summarize_document",
            "extract_sections",
            "propose_edit",
            "apply_patch",
            "write_text_file",
        ),
        execution_steps=(
            "locate_file",
            "read",
            "summarize",
            "extract_sections",
            "propose_edit_if_needed",
            "write_revision_if_needed",
        ),
        output_schema=("summary", "key_points", "proposed_changes", "revised_file_path", "warnings"),
    )

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
                skill_name=self.SPEC.name,
                success=False,
                summary="未定位到目标文档。",
                warnings=warnings or ["需要提供文件路径，或直接上传文档。"],
                tool_results=tool_results,
                response_text="请求已接收。当前缺少目标文件。请提供路径，或直接上传文档。",
            )

        logger.info("document_read_path path=%s", target_path)
        read_payload = self.documents.read_text_file(target_path, allowed_tools=allowed_tools)
        tool_results.append(ToolResult("read_text_file", ok=True, data={"path": target_path}))
        content = str(read_payload.get("content", ""))

        summary_payload = self.documents.summarize_document(content, allowed_tools=allowed_tools)
        sections_payload = self.documents.extract_sections(content, allowed_tools=allowed_tools)
        tool_results.append(ToolResult("summarize_document", ok=True, data=summary_payload))
        tool_results.append(ToolResult("extract_sections", ok=True, data=sections_payload))

        revise_requested = any(token in user_request for token in ("修改", "改得", "专业", "润色", "重写", "保存为新文件"))
        revised_path = ""
        proposed_changes = ""

        if revise_requested:
            revision_payload = self.documents.propose_edit(content, user_request, allowed_tools=allowed_tools)
            tool_results.append(ToolResult("propose_edit", ok=True, data={"diff": revision_payload.get("diff", "")}))
            logger.info("proposed_patch_generated path=%s", target_path)
            applied = self.documents.apply_patch(
                target_path,
                revision_payload,
                allowed_tools=allowed_tools,
                output_mode=agent_config.default_document_write_mode,
            )
            tool_results.append(ToolResult("apply_patch", ok=True, data=applied))
            revised_path = str(applied.get("revised_path", ""))
            proposed_changes = str(applied.get("diff", ""))
            logger.info("revised_copy_written path=%s", revised_path)

        synthesis = self.llm_helper.summarize_document_result(
            user_request,
            target_path,
            summary_payload.get("summary", ""),
            list(sections_payload.get("sections", [])),
            revised_path,
            warnings,
            memory_context=memory_context,
        )
        return SkillResult(
            skill_name=self.SPEC.name,
            success=True,
            summary=synthesis.get("summary", ""),
            structured={
                "summary": synthesis.get("summary", ""),
                "key_points": sections_payload.get("sections", []),
                "proposed_changes": proposed_changes,
                "revised_file_path": revised_path,
                "warnings": warnings,
            },
            recommendation=synthesis.get("recommendation", ""),
            tool_results=tool_results,
            warnings=warnings,
            response_text=synthesis.get("response_text", ""),
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

        list_payload = self.documents.search_files(str(Path.cwd()), ".md", allowed_tools=allowed_tools)
        tool_results.append(ToolResult("search_files", ok=True, data=list_payload))
        results = list_payload.get("results", [])
        if len(results) == 1:
            return str(results[0].get("path", ""))

        warnings.append("未从请求中解析出明确路径。")
        return ""
