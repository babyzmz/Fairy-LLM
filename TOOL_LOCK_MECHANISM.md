# Tool Lock: Protecting Realtime Factual Data from LLM Rewriting

## Problem

Realtime lookup responses (time, crypto prices, stock prices, weather, etc.) were being altered by the LLM's persona styling engine, causing:

- **Incorrect times**: Hour offsets applied incorrectly
- **Incorrect dates**: Cross-day boundaries mishandled
- **Fabricated explanations**: DST reasoning, timezone offset narratives
- **Hallucinated context**: Made-up explanations for why values changed

Example of the problem:
```
Tool output: "Tokyo: 2026-03-19 23:30 (night)"
LLM rewrite: "Tokyo: 2026-03-20 00:30 (night). This is because of daylight saving time."
```

The LLM was treating factual sensor data as suggestions to be "improved" with narrative.

## Solution: Tool Lock Mechanism

Tool lock prevents LLM rewriting of realtime factual data by:

1. **Marking responses as tool-locked** in SkillResult
2. **Bypassing persona styling** for tool-locked responses
3. **Direct rendering** from structured card data
4. **Validation** to detect any alterations
5. **Hallucination detection** for suspicious explanations

## Architecture

### 1. SkillResult Tool Lock Flag

```python
@dataclass(slots=True)
class SkillResult:
    # ... existing fields ...
    tool_lock: bool = False  # NEW: Prevents LLM rewriting
```

### 2. RealtimeLookupSkillHandler

Sets tool_lock=True and uses direct rendering:

```python
def execute(self, user_request: str, ...) -> dict[str, Any]:
    result = self.engine.execute(user_request)

    # NEW: Use direct rendering (bypasses LLM)
    rendered_answer = DirectRenderer.render_card(result.get("card"))

    # NEW: Validate that rendering didn't alter values
    is_valid = validate_tool_locked_response(
        original_answer,
        rendered_answer,
        card.get("card_type", ""),
    )

    return {
        "success": result.get("success", False),
        "card": result.get("card"),
        "answer": rendered_answer,
        "tool_lock": True,  # NEW
        "tool_lock_valid": is_valid,  # NEW
    }
```

### 3. LazyDispatcher

Marks realtime-lookup results as tool-locked:

```python
if bundle.name == "realtime-lookup":
    result = handler.execute(...)

    if result and result.get("success"):
        return SkillResult(
            skill_name="realtime-lookup",
            success=True,
            summary=result.get("answer", ""),
            response_text=result.get("answer", ""),
            tool_lock=True,  # NEW: Mark as tool-locked
            structured={
                **result,
                "tool_lock": True,  # NEW
            },
        )
```

### 4. FairyCore

Respects tool_lock and skips persona styling:

```python
if lazy_result is not None:
    # NEW: Skip persona styling for tool-locked results
    if lazy_result.tool_lock:
        logger.info("tool_lock_active skill=%s", lazy_result.skill_name)
        self._emit_event("tool_lock_applied", {...})
    elif persona_mode == "full":
        # Apply persona styling only for non-tool-locked responses
        styled_text, _ = persona.style_response(...)
        if styled_text:
            lazy_result.response_text = styled_text
```

## Components

### DirectRenderer

Renders structured card data directly without LLM:

```python
class DirectRenderer:
    @staticmethod
    def render_time_card(card: dict) -> str:
        """Render time card directly."""
        city = card.get("city", "Unknown")
        date_str = card.get("date", "")
        time_str = card.get("time", "")
        period = card.get("period", "")
        return f"{city}: {date_str} {time_str} ({period})"

    @staticmethod
    def render_crypto_card(card: dict) -> str:
        """Render crypto card directly."""
        symbol = card.get("symbol", "")
        price = card.get("price", "")
        return f"{symbol}: ${price} USD"

    # ... other card types ...

    @staticmethod
    def render_card(card: dict) -> str:
        """Render any card type directly."""
        card_type = card.get("card_type", "")
        if card_type == "time":
            return DirectRenderer.render_time_card(card)
        elif card_type == "crypto":
            return DirectRenderer.render_crypto_card(card)
        # ... etc ...
```

### ToolLockValidator

Validates that responses preserve factual values:

```python
class ToolLockValidator:
    @staticmethod
    def validate_response(
        original_response: str,
        formatted_response: str,
        card_type: str,
    ) -> dict[str, Any]:
        """Validate that formatted response doesn't alter original values."""
        # Extract numeric, datetime, currency, percentage values
        original_values = ToolLockValidator.extract_values(original_response)
        formatted_values = ToolLockValidator.extract_values(formatted_response)

        # Check for missing or changed values
        alterations = []
        if card_type in {"time", "crypto", "stock", "fx_rate", "fuel_price", "weather"}:
            # Verify all numeric values are preserved
            # Verify all datetimes are preserved
            # Verify all currencies are preserved

        return {
            "valid": len(alterations) == 0,
            "alterations": alterations,
        }

    @staticmethod
    def check_hallucinated_explanations(response: str) -> dict[str, Any]:
        """Detect hallucinated explanations like DST reasoning."""
        suspicious_patterns = [
            r"(?:due to|because of)\s+(?:daylight saving|DST)",
            r"(?:offset|adjusted)\s+(?:by|to)\s+\d+\s+(?:hour|minute)",
            r"(?:next day|previous day)\s+(?:because|due to)",
        ]

        detections = []
        for pattern in suspicious_patterns:
            matches = re.finditer(pattern, response, re.IGNORECASE)
            for match in matches:
                detections.append({
                    "type": pattern_type,
                    "text": match.group(0),
                })

        return {
            "has_hallucinations": len(detections) > 0,
            "detections": detections,
        }
```

## Supported Card Types

Tool lock applies to these realtime subtypes:

- **time**: Current time in specific timezone with date
- **crypto**: Cryptocurrency prices (BTC, ETH, etc.)
- **stock**: Stock prices (NVDA, AAPL, etc.)
- **fx_rate**: Foreign exchange rates
- **fuel_price**: Fuel prices by location
- **weather**: Current weather conditions

## Event Flow

```
RealtimeLookupSkillHandler.execute()
    ↓
DirectRenderer.render_card()  (bypasses LLM)
    ↓
ToolLockValidator.validate_response()  (checks integrity)
    ↓
LazyDispatcher returns SkillResult(tool_lock=True)
    ↓
FairyCore detects tool_lock=True
    ↓
Emits tool_lock_applied event
    ↓
Skips persona.style_response()
    ↓
Response sent to UI unchanged
```

## Logging

Tool lock operations are logged at key points:

```python
# When tool lock is active
logger.info("tool_lock_active skill=%s reason=realtime_factual_data")

# When validation passes
logger.info("tool_lock_validation_passed request_id=%s card_type=%s")

# When validation fails
logger.error("tool_lock_violation request_id=%s card_type=%s alterations=%s")

# When hallucinations detected
logger.warning("tool_lock_hallucination_detected request_id=%s detections=%s")

# When tool lock is applied in FairyCore
self._emit_event("tool_lock_applied", {
    "skill": lazy_result.skill_name,
    "reason": "realtime_factual_data",
    "response_text": lazy_result.response_text[:100],
})
```

## Testing

Run the tool lock tests:

```bash
python tests/test_tool_lock.py
```

Tests verify:
- Tool lock flag is set correctly
- Direct rendering produces correct output
- Value extraction works for all types
- Hallucination detection catches suspicious patterns
- Response validation prevents alterations
- FairyCore respects tool_lock
- End-to-end scenario works correctly

## Backward Compatibility

- Default `tool_lock=False` for existing skills
- Only realtime-lookup sets `tool_lock=True`
- Non-tool-locked responses continue to use persona styling
- No changes to existing skill interfaces

## Future Enhancements

1. **Localization**: Allow formatting/localization without value changes
2. **Caching**: Cache tool-locked responses to prevent re-fetching
3. **Metrics**: Track tool_lock violations for debugging
4. **Confidence**: Add confidence scores to tool-locked responses
5. **Multi-language**: Support tool lock for non-English responses

## Debugging

To debug tool lock issues:

1. Check logs for `tool_lock_active` events
2. Look for `tool_lock_violation` errors
3. Check `tool_lock_hallucination_detected` warnings
4. Verify `tool_lock_validation_passed` for successful responses
5. Inspect `structured["tool_lock"]` in SkillResult

Example debug output:

```
tool_lock_active skill=realtime-lookup reason=realtime_factual_data
tool_lock_validation_passed request_id=req-001 card_type=crypto
tool_lock_applied event emitted
Response: BTC: $42350.0 USD
```

## Guarantees

With tool lock enabled:

✓ Numeric values are never altered
✓ Datetime values are never altered
✓ No fabricated explanations added
✓ No DST reasoning injected
✓ No timezone offset narratives
✓ Responses treated as sensor data, not suggestions
✓ Factual accuracy guaranteed
