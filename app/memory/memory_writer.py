from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.memory.memory_schema import MemoryCandidate


class MemoryWriter:
    def extract_memory_candidates(self, task_context: dict[str, Any]) -> list[MemoryCandidate]:
        user_request = str(task_context.get("user_request", "") or "").strip()
        skill_name = str(task_context.get("skill_name", "") or "").strip()
        result = task_context.get("result") or {}
        success = bool(task_context.get("success", False))
        project = str(task_context.get("project", "") or "").strip()
        summary = str(task_context.get("summary", "") or "").strip()
        recommendation = str(task_context.get("recommendation", "") or "").strip()
        changed_files = list(task_context.get("changed_files") or [])
        commands_run = list(task_context.get("commands_run") or [])
        validations = list(task_context.get("validations") or [])

        candidates: list[MemoryCandidate] = []
        preference = self._extract_preference(user_request)
        if preference is not None:
            candidates.append(preference)

        if success and project and (changed_files or skill_name in {"agent_shell_skill", "document_editor_skill"}):
            module = Path(changed_files[0]).name if changed_files else skill_name
            detail_bits = []
            if summary:
                detail_bits.append(summary)
            if changed_files:
                detail_bits.append("changed=" + ", ".join(Path(path).name for path in changed_files[:3]))
            if commands_run:
                detail_bits.append(f"commands={len(commands_run)}")
            if validations:
                passed = sum(1 for item in validations if item.get("ok"))
                detail_bits.append(f"validation={passed}/{len(validations)}")
            project_summary = "; ".join(bit for bit in detail_bits if bit)[:420]
            candidates.append(
                MemoryCandidate(
                    type="project_state",
                    scope=project,
                    content=project_summary or f"{skill_name} completed successfully.",
                    confidence=0.82,
                    importance=0.78,
                    tags=[skill_name, "project"],
                    metadata={"module": module},
                )
            )

        if success:
            experience_summary = self._build_experience_summary(
                user_request=user_request,
                skill_name=skill_name,
                summary=summary,
                recommendation=recommendation,
                changed_files=changed_files,
                commands_run=commands_run,
                validations=validations,
            )
            candidates.append(
                MemoryCandidate(
                    type="experience",
                    scope=skill_name or "general",
                    content=experience_summary,
                    confidence=0.74,
                    importance=0.66 if skill_name == "web_research_skill" else 0.82,
                    tags=[skill_name or "general", "experience"],
                    metadata={"project": project, "result_structured": bool(result), "experience_scope": skill_name or "general"},
                )
            )

        return [candidate for candidate in candidates if candidate.content.strip()]

    def make_memory_id(self, prefix: str, content: str) -> str:
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}_{digest}"

    def _extract_preference(self, user_request: str) -> MemoryCandidate | None:
        text = user_request.strip()
        if not text:
            return None
        preference_patterns = (
            r"(不要[^。！？!?]{2,40})",
            r"(别[^。！？!?]{2,40})",
            r"(默认[^。！？!?]{2,40})",
            r"(以后[^。！？!?]{2,40})",
            r"(记住[^。！？!?]{2,40})",
            r"(请用[^。！？!?]{2,40})",
        )
        for pattern in preference_patterns:
            match = re.search(pattern, text)
            if match:
                return MemoryCandidate(
                    type="preference",
                    scope="user",
                    content=match.group(1).strip(),
                    confidence=0.72,
                    importance=0.84,
                    tags=["preference"],
                )
        return None

    def _build_experience_summary(
        self,
        *,
        user_request: str,
        skill_name: str,
        summary: str,
        recommendation: str,
        changed_files: list[str],
        commands_run: list[str],
        validations: list[dict[str, Any]],
    ) -> str:
        parts = [f"task={user_request[:120]}", f"skill={skill_name or 'unknown'}"]
        if summary:
            parts.append(f"summary={summary[:180]}")
        if changed_files:
            parts.append("files=" + ",".join(Path(path).name for path in changed_files[:3]))
        if commands_run:
            parts.append(f"commands={len(commands_run)}")
        if validations:
            passed = sum(1 for item in validations if item.get("ok"))
            parts.append(f"validation={passed}/{len(validations)}")
        if recommendation:
            parts.append(f"next={recommendation[:120]}")
        return " | ".join(parts)[:480]
