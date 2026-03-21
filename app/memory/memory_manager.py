from __future__ import annotations

import json
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import Any

from app.config import memory_config
from app.rag.rag_schema import RAGSettings
from app.storage.repositories.settings_repo import SettingsRepo
from app.memory.memory_injection_policy import classify_task_category, policy_for_task_category
from app.memory.memory_prompt_builder import summarize_memories_for_prompt
from app.memory.memory_pruner import MemoryPruner
from app.memory.memory_retriever import MemoryRetriever
from app.memory.memory_schema import MemoryRetrievalBundle
from app.memory.memory_store import StructuredMemoryStore
from app.memory.memory_vector_store import HashEmbeddingModel, VectorMemoryStore
from app.memory.memory_writer import MemoryWriter


logger = logging.getLogger(__name__)


class MemoryManager:
    def __init__(self) -> None:
        self.settings_repo = SettingsRepo()
        self.store = StructuredMemoryStore(memory_config.db_path)
        self.vector_store = VectorMemoryStore(
            memory_config.db_path,
            embedding_model=HashEmbeddingModel(memory_config.semantic_dimension),
        )
        self.retriever = MemoryRetriever(self.store, self.vector_store)
        self.writer = MemoryWriter()
        self.pruner = MemoryPruner(self.store, self.vector_store, max_semantic_items=memory_config.max_semantic_items)
        logger.info("legacy_memory_mode=%s", self._legacy_mode())

    def new_task_id(self) -> str:
        return f"task_{uuid.uuid4().hex[:12]}"

    def classify_task(self, user_request: str, attachments: list[str] | None = None, *, chosen_skill: str = "") -> str:
        return classify_task_category(user_request, attachments, chosen_skill=chosen_skill)

    def prepare_memory_context(
        self,
        *,
        user_request: str,
        project: str,
        task_id: str | None,
        attachments: list[str] | None = None,
        chosen_skill: str = "",
    ) -> tuple[MemoryRetrievalBundle, str, dict[str, int], str]:
        category = self.classify_task(user_request, attachments, chosen_skill=chosen_skill)
        if self._legacy_mode() == "disabled":
            bundle = MemoryRetrievalBundle()
            return bundle, "", bundle.counts(), category
        policy = policy_for_task_category(category)
        bundle = self.retriever.retrieve(
            query=user_request,
            project=project,
            task_id=task_id,
            task_category=category,
            policy=policy,
        )
        prompt_block = summarize_memories_for_prompt(bundle, policy)
        return bundle, prompt_block, bundle.counts(), category

    def build_memory_prompt_block(
        self,
        *,
        user_request: str,
        project: str,
        task_id: str | None,
        attachments: list[str] | None = None,
        chosen_skill: str = "",
    ) -> tuple[str, dict[str, int], str]:
        _, prompt_block, counts, category = self.prepare_memory_context(
            user_request=user_request,
            project=project,
            task_id=task_id,
            attachments=attachments,
            chosen_skill=chosen_skill,
        )
        return prompt_block, counts, category

    def start_task(self, task_id: str, goal: str) -> None:
        if not self._legacy_writable():
            return
        self.store.create_or_update_task_memory(task_id, goal, current_step="received", done_steps=[], status="running")

    def update_task(
        self,
        task_id: str,
        goal: str,
        *,
        current_step: str = "",
        done_steps: list[str] | None = None,
        blocked_reason: str = "",
        last_url: str = "",
        status: str = "running",
    ) -> None:
        if not self._legacy_writable():
            return
        self.store.create_or_update_task_memory(
            task_id,
            goal,
            current_step=current_step,
            done_steps=done_steps,
            blocked_reason=blocked_reason,
            last_url=last_url,
            status=status,
        )

    def finish_task(self, task_context: dict[str, Any]) -> dict[str, Any]:
        if not self._legacy_writable():
            return {"persisted": {"profile": 0, "project": 0, "semantic": 0, "structured": 0}, "pruned": {"expired_deleted": 0, "semantic_trimmed": 0}}
        task_id = str(task_context.get("task_id", "") or "")
        user_request = str(task_context.get("user_request", "") or "")
        success = bool(task_context.get("success", False))
        current_step = str(task_context.get("skill_name", "") or "")
        blocked_reason = "" if success else str(task_context.get("summary", "") or task_context.get("recommendation", "") or "")
        last_url = self._extract_last_url(task_context)
        done_steps = ["routed", current_step or "unknown", "completed" if success else "failed"]
        if task_id:
            self.update_task(
                task_id,
                user_request,
                current_step=current_step,
                done_steps=done_steps,
                blocked_reason=blocked_reason,
                last_url=last_url,
                status="completed" if success else "failed",
            )

        if current_step == "memory_debug":
            return {"persisted": {"profile": 0, "project": 0, "semantic": 0, "structured": 0}, "pruned": {"expired_deleted": 0, "semantic_trimmed": 0}}

        candidates = self.writer.extract_memory_candidates(task_context)
        persisted = {"profile": 0, "project": 0, "semantic": 0, "structured": 0}
        for candidate in candidates:
            if candidate.type == "preference":
                memory_id = self.writer.make_memory_id("pref", candidate.content)
                self.store.upsert_structured_memory(
                    memory_id,
                    candidate.type,
                    candidate.scope,
                    candidate.content,
                    candidate.confidence,
                    candidate.importance,
                    candidate.tags,
                    candidate.expires_at,
                )
                self.store.upsert_profile("user_profile", memory_id, candidate.content)
                persisted["profile"] += 1
                persisted["structured"] += 1
                continue

            if candidate.type == "project_state":
                memory_id = self.writer.make_memory_id("proj", candidate.content)
                self.store.upsert_project_memory(
                    memory_id,
                    str(task_context.get("project", "") or ""),
                    str(candidate.metadata.get("module", "general")),
                    candidate.content,
                    candidate.importance,
                )
                persisted["project"] += 1
                continue

            if candidate.type == "experience":
                memory_id = self.writer.make_memory_id("exp", candidate.content)
                scope = str(task_context.get("task_category") or candidate.metadata.get("experience_scope") or candidate.metadata.get("project") or candidate.scope or "general")
                self.vector_store.add_experience(
                    memory_id,
                    scope=scope,
                    content=candidate.content,
                    importance=candidate.importance,
                    confidence=candidate.confidence,
                    tags=candidate.tags,
                    metadata=candidate.metadata,
                )
                persisted["semantic"] += 1
                continue

            memory_id = self.writer.make_memory_id("mem", candidate.content)
            self.store.upsert_structured_memory(
                memory_id,
                candidate.type,
                candidate.scope,
                candidate.content,
                candidate.confidence,
                candidate.importance,
                candidate.tags,
                candidate.expires_at,
            )
            persisted["structured"] += 1

        prune_stats = self.pruner.prune()
        return {
            "persisted": persisted,
            "pruned": prune_stats,
        }

    def handle_debug_command(self, user_request: str) -> dict[str, Any] | None:
        text = user_request.strip()
        if not text.lower().startswith("fairy memory"):
            return None
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            return {
                "ok": True,
                "response_text": "可用命令：fairy memory list | fairy memory show <id> | fairy memory delete <id> | fairy memory semantic_search <query>",
            }
        command = parts[2].lower()
        if command == "list":
            structured = self.store.list_memory_rows(limit=20)
            semantic = self.vector_store.list_recent(limit=10)
            lines = ["Structured memory:"]
            for item in structured[:10]:
                lines.append(f"- {item.get('id', '')} [{item.get('type', '')}] {item.get('content', '')[:120]}")
            lines.append("Semantic memory:")
            for item in semantic[:10]:
                lines.append(f"- {item.get('id', '')} [experience] {str(item.get('content', ''))[:120]}")
            return {"ok": True, "response_text": "\n".join(lines)}
        if command == "show" and len(parts) >= 4:
            item = self.store.show_memory(parts[3].strip())
            if item is None:
                return {"ok": False, "response_text": "未找到对应 memory id。"}
            return {"ok": True, "response_text": json.dumps(item, ensure_ascii=False, indent=2)}
        if command == "delete" and len(parts) >= 4:
            if not self._legacy_writable():
                return {"ok": False, "response_text": "legacy memory 当前处于只读或禁用状态，无法删除。"}
            deleted = self.store.delete_memory(parts[3].strip())
            return {"ok": deleted, "response_text": "已删除。" if deleted else "未找到对应 memory id。"}
        if command == "semantic_search" and len(parts) >= 4:
            hits = self.vector_store.search(parts[3].strip(), limit=8)
            if not hits:
                return {"ok": True, "response_text": "没有命中相关 experience memory。"}
            lines = ["Semantic hits:"]
            for hit in hits:
                lines.append(f"- {hit.id} score={hit.score:.3f} {hit.content[:160]}")
            return {"ok": True, "response_text": "\n".join(lines)}
        return {"ok": False, "response_text": "无法识别的 memory 命令。"}

    def _bootstrap_environment(self) -> None:
        self.store.upsert_profile("environment_profile", "cwd", str(Path.cwd()))
        self.store.upsert_profile("environment_profile", "os", platform.platform())
        self.store.upsert_profile("environment_profile", "python", platform.python_version())
        timezone_name = os.getenv("TZ") or os.getenv("TIMEZONE") or "Australia/Sydney"
        self.store.upsert_profile("environment_profile", "timezone", timezone_name)

    def _migrate_legacy_memory(self) -> None:
        migrated_flag = next((item for item in self.store.list_profile("environment_profile") if item.get("key") == "legacy_memory_migrated"), None)
        if migrated_flag is not None:
            return
        legacy_file = memory_config.legacy_memory_file
        if legacy_file.exists():
            try:
                records = json.loads(legacy_file.read_text(encoding="utf-8"))
            except Exception:
                records = []
            for item in list(records)[-20:]:
                user_text = str(item.get("user_text", "") or "").strip()
                if not user_text:
                    continue
                if user_text.endswith(("。", "！", "？")) and len(user_text) <= 120:
                    pref = self.writer._extract_preference(user_text)
                    if pref is None:
                        continue
                    memory_id = self.writer.make_memory_id("pref", pref.content)
                    self.store.upsert_structured_memory(memory_id, pref.type, pref.scope, pref.content, pref.confidence, pref.importance, pref.tags)
                    self.store.upsert_profile("user_profile", memory_id, pref.content)
        self.store.upsert_profile("environment_profile", "legacy_memory_migrated", "1")

    def _legacy_mode(self) -> str:
        settings = RAGSettings.from_dict(self.settings_repo.get_json("rag_settings"))
        return settings.legacy_memory_mode.strip().lower() or "read_only"

    def _legacy_writable(self) -> bool:
        return self._legacy_mode() not in {"read_only", "disabled"}

    def _extract_last_url(self, task_context: dict[str, Any]) -> str:
        structured = task_context.get("structured") or {}
        if isinstance(structured, dict):
            sources = structured.get("sources") or task_context.get("sources") or []
            if isinstance(sources, list):
                for item in reversed(sources):
                    if isinstance(item, dict) and item.get("url"):
                        return str(item["url"])
        return ""
