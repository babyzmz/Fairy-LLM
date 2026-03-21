from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Iterable

from app.ai.llm_client import LLMClient
from app.config import agent_config
from app.mcp_client_layer import MCPClientLayer
from app.tool_registry import ToolRegistry


SUPPORTED_TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".py", ".yaml", ".yml"}


class DocumentCapability:
    def __init__(self, registry: ToolRegistry, mcp: MCPClientLayer, llm: LLMClient) -> None:
        self.registry = registry
        self.mcp = mcp
        self.llm = llm
        self.allowed_roots = tuple(path.resolve() for path in agent_config.allowed_roots)
        self._register_tools()

    def _register_tools(self) -> None:
        self.registry.register("list_dir", "List files in an allowed directory.", self._tool_list_dir)
        self.registry.register("search_files", "Search files within an allowed root.", self._tool_search_files)
        self.registry.register("read_text_file", "Read a supported text document.", self._tool_read_text_file)
        self.registry.register("write_text_file", "Write text to a file within an allowed root.", self._tool_write_text_file)
        self.registry.register("summarize_document", "Summarize a document.", self._tool_summarize_document)
        self.registry.register("extract_sections", "Extract headings or logical sections from a document.", self._tool_extract_sections)
        self.registry.register("propose_edit", "Propose a revised document based on an instruction.", self._tool_propose_edit)
        self.registry.register("apply_patch", "Apply a proposed revision to preview or create a new copy.", self._tool_apply_patch)

    def list_dir(self, path: str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("list_dir", allowed_tools=allowed_tools, path=path).data

    def search_files(self, path: str, pattern: str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("search_files", allowed_tools=allowed_tools, path=path, pattern=pattern).data

    def read_text_file(self, path: str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("read_text_file", allowed_tools=allowed_tools, path=path).data

    def summarize_document(self, content: str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("summarize_document", allowed_tools=allowed_tools, content=content).data

    def extract_sections(self, content: str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("extract_sections", allowed_tools=allowed_tools, content=content).data

    def propose_edit(self, content: str, instruction: str, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool(
            "propose_edit",
            allowed_tools=allowed_tools,
            content=content,
            instruction=instruction,
        ).data

    def apply_patch(
        self,
        path: str,
        revision: dict[str, Any],
        *,
        allowed_tools: Iterable[str],
        output_mode: str = "write_new_copy",
    ) -> dict[str, Any]:
        return self.mcp.call_tool(
            "apply_patch",
            allowed_tools=allowed_tools,
            path=path,
            revision=revision,
            output_mode=output_mode,
        ).data

    def _tool_list_dir(self, path: str) -> dict[str, Any]:
        target = self._resolve_allowed_path(path)
        entries = []
        for item in sorted(target.iterdir(), key=lambda entry: (entry.is_file(), entry.name.lower())):
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                }
            )
        return {"path": str(target), "entries": entries}

    def _tool_search_files(self, path: str, pattern: str) -> dict[str, Any]:
        root = self._resolve_allowed_path(path)
        lowered = pattern.lower().strip()
        results: list[dict[str, Any]] = []
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in SUPPORTED_TEXT_SUFFIXES:
                continue
            name_match = lowered in file_path.name.lower()
            content_match = False
            if not name_match:
                try:
                    content_match = lowered in file_path.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception:
                    content_match = False
            if name_match or content_match:
                results.append({"path": str(file_path), "name": file_path.name})
            if len(results) >= 30:
                break
        return {"results": results}

    def _tool_read_text_file(self, path: str) -> dict[str, Any]:
        target = self._resolve_allowed_path(path)
        if target.suffix.lower() not in SUPPORTED_TEXT_SUFFIXES:
            raise ValueError(f"Unsupported text file type: {target.suffix}")
        content = target.read_text(encoding="utf-8", errors="ignore")
        return {"path": str(target), "content": content}

    def _tool_write_text_file(self, path: str, content: str) -> dict[str, Any]:
        target = self._resolve_allowed_path(path, allow_missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target)}

    def _tool_summarize_document(self, content: str) -> dict[str, Any]:
        response = self.llm.execute_task(
            "You summarize documents. Return concise Chinese bullets with sections: 摘要 and 重点.",
            f"请总结下面的文档。\n\n{content[:12000]}",
            max_tokens=320,
            temperature=0.1,
        )
        return {"summary": response.text}

    def _tool_extract_sections(self, content: str) -> dict[str, Any]:
        sections: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("#", "##", "###")):
                sections.append(stripped.lstrip("#").strip())
            elif stripped.endswith(":") and len(stripped) < 80:
                sections.append(stripped[:-1].strip())
        if not sections:
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    sections = [str(key) for key in data.keys()]
            except Exception:
                pass
        return {"sections": sections[:20]}

    def _tool_propose_edit(self, content: str, instruction: str) -> dict[str, Any]:
        revised = self.llm.execute_task(
            (
                "You rewrite documents professionally. "
                "Keep meaning intact. Return only the revised document content. "
                "Do not add markdown fences or commentary."
            ),
            f"编辑要求：{instruction}\n\n原文如下：\n{content[:16000]}",
            max_tokens=1800,
            temperature=0.2,
        ).text
        diff = "\n".join(
            difflib.unified_diff(
                content.splitlines(),
                revised.splitlines(),
                fromfile="original",
                tofile="revised",
                lineterm="",
            )
        )
        return {"revised_content": revised, "diff": diff}

    def _tool_apply_patch(self, path: str, revision: dict[str, Any], output_mode: str = "write_new_copy") -> dict[str, Any]:
        target = self._resolve_allowed_path(path)
        revised_content = str(revision.get("revised_content", ""))
        diff = str(revision.get("diff", ""))
        if output_mode == "preview_only":
            return {
                "original_path": str(target),
                "revised_path": "",
                "diff": diff,
                "summary_of_changes": "Preview only; no file written.",
            }

        revised_path = target
        if output_mode != "overwrite":
            revised_path = target.with_name(f"{target.stem}{agent_config.revised_copy_suffix}{target.suffix}")
        revised_path.write_text(revised_content, encoding="utf-8")
        return {
            "original_path": str(target),
            "revised_path": str(revised_path),
            "diff": diff,
            "summary_of_changes": "Revised copy written." if output_mode != "overwrite" else "Original file overwritten.",
        }

    def _resolve_allowed_path(self, path: str, *, allow_missing: bool = False) -> Path:
        raw = Path(path).expanduser()
        candidate = (raw if raw.is_absolute() else (Path.cwd() / raw)).resolve()
        for root in self.allowed_roots:
            try:
                candidate.relative_to(root)
                if not allow_missing and not candidate.exists():
                    raise FileNotFoundError(candidate)
                return candidate
            except ValueError:
                continue
        raise PermissionError(f"Path not allowed: {candidate}")

