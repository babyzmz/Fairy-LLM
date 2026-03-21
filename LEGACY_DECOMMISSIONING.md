# Controlled Legacy Skill Decommissioning

## Overview

This document describes the controlled decommissioning of legacy skills in the Fairy assistant. The goal is to transition from legacy skills to the new lazy runtime without breaking existing dependencies or removing code.

## Key Principles

1. **Legacy code is kept physically** for fallback and debugging
2. **Legacy skills are removed from active routing** - they don't appear in router candidate lists
3. **Legacy behavioral prompts are disabled** - only minimal assistant identity remains
4. **New lazy runtime has strict deterministic mode** for realtime skills - prevents LLM expansion and narrative

## Architecture

### Decommissioning Phases

Each legacy skill progresses through phases:

- **ACTIVE**: Skill is actively used in routing (legacy state)
- **DEPRECATED**: Skill is deprecated but still available for fallback
- **ARCHIVED**: Skill is archived, not in routing
- **REMOVED**: Skill code is removed (future)

### Components

#### 1. LegacySkillDecommissioner

Manages which skills are being decommissioned and their status.

```python
# Check if a skill is legacy
if LegacySkillDecommissioner.is_legacy_skill("web_research_skill"):
    # Get decommissioning config
    config = LegacySkillDecommissioner.get_config("web_research_skill")

    # Check if should be excluded from routing
    if LegacySkillDecommissioner.should_exclude_from_routing("web_research_skill"):
        # Skip this skill in router
        pass

    # Get replacement skill
    replacement = LegacySkillDecommissioner.get_replacement_skill("web_research_skill")
    # replacement = "web-research"
```

#### 2. LegacyPromptDisabler

Disables legacy behavioral system prompts.

```python
# Filter a prompt to remove behavioral patterns
filtered_prompt = LegacyPromptDisabler.filter_prompt(
    original_prompt,
    skill_name="realtime-lookup"
)

# Create minimal system prompt with only core identity
minimal_prompt = LegacyPromptDisabler.create_minimal_system_prompt()
```

#### 3. StrictDeterministicMode

Applies strict deterministic response mode to realtime skills.

```python
# Check if strict mode should be used
if StrictDeterministicMode.should_use_strict_mode("realtime-lookup", "time"):
    # Apply strict mode to response
    response = StrictDeterministicMode.apply_strict_mode(response)
    # Sets: strict_mode=True, tool_lock=True, no_expansion=True, deterministic=True
```

## Integration Points

### 1. LazySkillRouter

The router filters out legacy skills from both heuristic scoring and LLM selection:

```python
# In _heuristic_score()
for meta in all_meta:
    if LegacySkillDecommissioner.should_exclude_from_routing(meta.name):
        logger.info("lazy_route_skip_legacy_skill skill=%s", meta.name)
        continue
    # Score this skill...

# In _llm_select()
for meta in all_meta:
    if LegacySkillDecommissioner.should_exclude_from_routing(meta.name):
        logger.info("lazy_route_llm_skip_legacy_skill skill=%s", meta.name)
        continue
    manifest_lines.append(f"- {meta.name}: {meta.description}")
```

### 2. FairyCore

FairyCore applies legacy prompt disabling and strict deterministic mode:

```python
# In _execute_direct_answer()
system_prompt = guidance
if memory_prompt.strip():
    system_prompt += f"\n\n{memory_prompt.strip()}"

# Apply legacy prompt disabler
system_prompt = LegacyPromptDisabler.filter_prompt(system_prompt, route_name)

# In handle_request() for lazy results
if lazy_result.tool_lock:
    logger.info("tool_lock_active skill=%s", lazy_result.skill_name)

    # Apply strict deterministic mode for realtime skills
    card_type = lazy_result.structured.get("card_type")
    if StrictDeterministicMode.should_use_strict_mode(lazy_result.skill_name, card_type):
        lazy_result.structured = StrictDeterministicMode.apply_strict_mode(
            lazy_result.structured or {}
        )
```

## Legacy Skills Being Decommissioned

### 1. web_research_skill
- **Phase**: DEPRECATED
- **Replacement**: web-research
- **Reason**: Replaced by web-research bundle in lazy runtime
- **Fallback**: Enabled
- **Debug**: Enabled

### 2. screen_understanding_skill
- **Phase**: DEPRECATED
- **Replacement**: screen-understanding
- **Reason**: Replaced by screen-understanding bundle in lazy runtime
- **Fallback**: Enabled
- **Debug**: Enabled

### 3. agent_shell_skill
- **Phase**: DEPRECATED
- **Replacement**: terminal-agent
- **Reason**: Replaced by terminal-agent bundle in lazy runtime
- **Fallback**: Enabled
- **Debug**: Enabled

### 4. news_intelligence_skill
- **Phase**: DEPRECATED
- **Replacement**: news-intelligence
- **Reason**: Replaced by news-intelligence bundle in lazy runtime
- **Fallback**: Enabled
- **Debug**: Enabled

### 5. document_editor_skill
- **Phase**: DEPRECATED
- **Replacement**: document-editing
- **Reason**: Replaced by document-editing bundle in lazy runtime
- **Fallback**: Enabled
- **Debug**: Enabled

## Behavioral Patterns Disabled

The LegacyPromptDisabler removes these patterns from system prompts:

### Suggestion-style patterns
- 主动推进 (actively advance)
- 主动提出 (actively propose)
- 主动建议 (actively suggest)
- 可以尝试 (can try)
- 建议 (suggest)
- 不妨 (might as well)

### Narrative expansion patterns
- 讲述 (narrate)
- 叙述 (describe)
- 故事 (story)
- 背景 (background)
- 上下文 (context)

### Educational expansion patterns
- 解释 (explain)
- 说明 (clarify)
- 教学 (teach)
- 学习 (learn)

### Cross-task contextual patterns
- 之前 (before)
- 上次 (last time)
- 历史 (history)
- 记得 (remember)
- 还记得 (still remember)

## Strict Deterministic Mode

Applied to realtime skills to prevent LLM expansion:

### Skills requiring strict mode
- realtime-lookup
- realtime_lookup_skill

### Card types requiring strict mode
- time
- crypto
- stock
- fx_rate
- fuel_price
- weather

### Flags set by strict mode
- `strict_mode: True` - Strict mode is active
- `tool_lock: True` - Response is tool-locked
- `no_expansion: True` - No LLM expansion allowed
- `deterministic: True` - Response is deterministic

## Data Flow

```
User Query
    ↓
LazySkillRouter.route()
    ├─ _heuristic_score()
    │   └─ Skip legacy skills (excluded from routing)
    ├─ _llm_select()
    │   └─ Skip legacy skills (not in manifest)
    └─ Return best non-legacy skill
    ↓
LazyDispatcher.dispatch()
    └─ Execute selected skill
    ↓
FairyCore.handle_request()
    ├─ Detect tool_lock flag
    ├─ Apply strict deterministic mode if needed
    ├─ Skip persona styling for tool-locked results
    └─ Return response
    ↓
UI Receives Response
    └─ Display factual data without alterations
```

## Logging

The system logs all decommissioning activities:

```
lazy_route_skip_legacy_skill skill=web_research_skill
lazy_route_llm_skip_legacy_skill skill=screen_understanding_skill
tool_lock_active skill=realtime-lookup reason=realtime_factual_data
strict_deterministic_mode_applied skill=realtime-lookup card_type=time
```

## Testing

Run the comprehensive test suite:

```bash
python tests/test_legacy_decommissioning.py
```

Expected output:
```
TEST 1: Decommissioning phases ✓ PASS
TEST 2: Legacy skill config ✓ PASS
TEST 3: Legacy skill decommissioner ✓ PASS
TEST 4: Legacy prompt disabler ✓ PASS
TEST 5: Strict deterministic mode ✓ PASS
TEST 6: Integration workflow ✓ PASS

ALL LEGACY DECOMMISSIONING TESTS PASSED
```

## Guarantees

With controlled decommissioning:

✓ Legacy skills are not in active routing
✓ Legacy code is kept for fallback and debugging
✓ Legacy behavioral prompts are disabled
✓ Realtime skills use strict deterministic mode
✓ No LLM expansion of factual data
✓ No fabricated explanations
✓ Factual accuracy guaranteed
✓ Backward compatibility maintained

## Migration Path

### Phase 1: Deprecation (Current)
- Legacy skills marked as DEPRECATED
- Excluded from active routing
- Fallback enabled for compatibility
- Behavioral prompts disabled

### Phase 2: Archival (Future)
- Legacy skills marked as ARCHIVED
- Fallback disabled
- Code kept for reference

### Phase 3: Removal (Future)
- Legacy skills marked as REMOVED
- Code deleted
- No fallback available

## Monitoring

Monitor these events to track decommissioning:

```python
# Router skipping legacy skills
logger.info("lazy_route_skip_legacy_skill skill=%s", skill_name)

# Tool lock being applied
logger.info("tool_lock_active skill=%s", skill_name)

# Strict deterministic mode being applied
logger.info("strict_deterministic_mode_applied skill=%s card_type=%s", skill_name, card_type)
```

## Troubleshooting

### Legacy skill still appearing in routing
- Check that `LegacySkillDecommissioner.should_exclude_from_routing()` returns True
- Verify skill is in `LEGACY_SKILLS_TO_DECOMMISSION` dict
- Check that phase is DEPRECATED or ARCHIVED

### Behavioral patterns still in prompts
- Verify `LegacyPromptDisabler.filter_prompt()` is called
- Check that skill_name matches realtime skill names
- Verify patterns are in `PATTERNS_TO_DISABLE`

### Strict mode not being applied
- Check that skill is in `STRICT_MODE_SKILLS`
- Verify card_type is in `STRICT_MODE_CARD_TYPES`
- Ensure `StrictDeterministicMode.apply_strict_mode()` is called

## Future Enhancements

- [ ] Add metrics tracking for decommissioning progress
- [ ] Implement gradual traffic shifting to new skills
- [ ] Add A/B testing for new vs legacy skills
- [ ] Support multi-language behavioral pattern disabling
- [ ] Add confidence scores to decommissioning decisions
- [ ] Implement automatic phase progression based on metrics

## Conclusion

The controlled decommissioning system ensures a smooth transition from legacy skills to the new lazy runtime while maintaining backward compatibility and preventing data corruption. Legacy code is preserved for fallback and debugging, while new skills are protected from LLM rewriting through tool lock and strict deterministic mode.
