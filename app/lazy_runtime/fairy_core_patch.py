"""
Fairy Core Integration Patch
=============================

This file documents the EXACT changes needed in fairy_core.py to wire in the
new lazy skill pipeline.  It is NOT imported at runtime — it serves as a
reference and can be applied manually or via a script.

The changes are minimal and non-destructive:
  1. Add imports (3 lines)
  2. Add lazy dispatcher init in __init__ (5 lines)
  3. Add dual-path dispatch in handle_request (15 lines)

All changes are guarded by the feature flag, so the legacy pipeline is
completely untouched when use_lazy_skills is False.
"""

# ===========================================================================
# CHANGE 1: Add imports at the top of fairy_core.py (after existing imports)
# ===========================================================================
IMPORT_PATCH = '''
# --- Lazy Skill Runtime (feature-flagged) ---
from app.lazy_runtime.feature_flags import lazy_skills_flags
from app.lazy_runtime.lazy_dispatcher import LazyDispatcher
'''

# ===========================================================================
# CHANGE 2: Add lazy dispatcher init at the end of FairyCore.__init__
#            (after self.router = SkillRouter(...) block)
# ===========================================================================
INIT_PATCH = '''
        # --- Lazy Skill Runtime (feature-flagged) ---
        self._lazy_dispatcher: LazyDispatcher | None = None
        if lazy_skills_flags.use_lazy_skills:
            try:
                self._lazy_dispatcher = LazyDispatcher(
                    self.llm,
                    self.registry,
                    event_callback=self._emit_event,
                )
                logger.info("lazy_dispatcher_initialized")
            except Exception:
                logger.exception("lazy_dispatcher_init_failed — falling back to legacy pipeline")
                self._lazy_dispatcher = None
'''

# ===========================================================================
# CHANGE 3: Add dual-path dispatch in handle_request
#            Insert AFTER the game_mode check (line ~889) and BEFORE the
#            legacy routing block (self.llm_helper.set_memory_context...)
#
#            The exact insertion point is between:
#              return result  # (end of game_mode block)
#            and:
#              self.llm_helper.set_memory_context(combined_memory_prompt)
# ===========================================================================
HANDLE_REQUEST_PATCH = '''
        # --- Lazy Skill Runtime dual-path dispatch (feature-flagged) ---
            if lazy_skills_flags.use_lazy_skills and self._lazy_dispatcher is not None:
                self._emit_event("lazy_pipeline_entered", {"pipeline": "new"})
                try:
                    lazy_result = self._lazy_dispatcher.dispatch(
                        user_request,
                        attachments=attachments,
                        memory_prompt=combined_memory_prompt,
                        route_context=route_context,
                        legacy_skills=self.skills,
                    )
                    if lazy_result is not None:
                        # New pipeline succeeded — apply persona styling and finalize
                        lazy_result.structured.setdefault("pipeline", "new")
                        if persona_mode == "full":
                            persona = self._get_persona_engine()
                            persona_task_type = persona.classify_task_type(
                                user_request,
                                chosen_skill=lazy_result.skill_name,
                                task_category=task_category,
                            )
                            styled_text, guard_report = persona.style_response(
                                lazy_result.response_text or lazy_result.summary,
                                task_type=persona_task_type,
                                user_input=user_request,
                                user_profile=memory_bundle.profile,
                                recent_summary=combined_memory_prompt,
                            )
                            if styled_text:
                                lazy_result.response_text = styled_text
                        self._finalize_task(task_id, user_request, lazy_result, task_category=task_category)
                        return lazy_result
                    else:
                        # New pipeline returned None — fall through to legacy
                        logger.info("lazy_pipeline_fallback_to_legacy reason=dispatch_returned_none")
                        self._emit_event("lazy_pipeline_fallback", {"reason": "dispatch_returned_none", "pipeline": "legacy"})
                except Exception:
                    logger.exception("lazy_pipeline_exception — falling back to legacy")
                    self._emit_event("lazy_pipeline_fallback", {"reason": "exception", "pipeline": "legacy"})

            # --- Legacy pipeline continues below (unchanged) ---
'''
