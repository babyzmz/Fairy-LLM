# Multi-Endpoint Request Origin Tracking Architecture

## Overview

Fairy is a multi-endpoint agent system with independent interaction entry points. Each endpoint is a first-class conversational interface that must receive responses only for requests it originated.

### Entry Points

1. **Main Chat Window** (`FairyPresenceWindow`)
   - Primary chat interface
   - Origin: `"main_chat"`
   - Displays full conversation history

2. **Desktop Pet** (`DesktopPetWindow`)
   - Floating widget at bottom-right
   - Origin: `"desktop_pet"`
   - Displays quick responses and notifications

3. **Future: Hotkey Popup** (planned)
   - Quick access panel via hotkey
   - Origin: `"hotkey_popup"`

4. **Future: IDE Overlay** (planned)
   - Integration with IDE
   - Origin: `"ide_overlay"`

## Architecture

### Request Flow

```
UI Entry Point
    ↓
on_user_message(request_origin="main_chat")
    ↓
ChatWorker(request_origin, request_id)
    ↓
FairyCore.handle_request(request_origin, request_id)
    ↓
lazy_dispatcher.dispatch(request_origin, request_id)
    ↓
SkillResult.structured["request_origin"] = request_origin
    ↓
_finalize_task() emits final_response_ready with origin
    ↓
_on_assistant_reply() filters by origin
    ↓
UI Component (only renders matching origin)
```

### Key Components

#### 1. ChatWorker (app/assistant_mode.py)

Accepts and stores request metadata:

```python
worker = ChatWorker(
    llm=self.llm,
    voice=self.voice,
    user_text=user_text,
    attachment_paths=attachments,
    user_log_text=user_log_text,
    route_context=route_context,
    request_origin="main_chat",  # NEW
    request_id="req-001",         # NEW
)
```

Passes to FairyCore:

```python
result = core.handle_request(
    self.user_text,
    attachment_paths=self.attachment_paths,
    route_context=self.route_context,
    request_origin=self.request_origin,  # NEW
    request_id=self.request_id,          # NEW
)
```

#### 2. FairyCore (app/fairy_core.py)

Accepts and propagates origin:

```python
def handle_request(
    self,
    user_request: str,
    *,
    attachment_paths: Iterable[str] | None = None,
    route_context: RouteContext | None = None,
    request_origin: str = "main_chat",  # NEW
    request_id: str = "",               # NEW
) -> SkillResult:
```

Emits events with origin:

```python
self._emit_event("user_request_received", {
    "text": user_request,
    "origin": request_origin,      # NEW
    "request_id": request_id,      # NEW
})
```

Passes to lazy_dispatcher:

```python
lazy_result = self._lazy_dispatcher.dispatch(
    user_request,
    attachments=attachments,
    memory_prompt=combined_memory_prompt,
    route_context=route_context,
    legacy_skills=self.skills,
    request_origin=request_origin,  # NEW
    request_id=request_id,          # NEW
)
```

Ensures origin in result:

```python
lazy_result.structured.setdefault("request_origin", request_origin)
lazy_result.structured.setdefault("request_id", request_id)
```

#### 3. LazyDispatcher (app/lazy_runtime/lazy_dispatcher.py)

Already implements origin tracking:

```python
def dispatch(
    self,
    user_request: str,
    *,
    attachments: list[str] | None = None,
    memory_prompt: str = "",
    route_context: Any | None = None,
    legacy_skills: dict[str, Any] | None = None,
    request_origin: str | None = None,      # EXISTING
    request_id: str | None = None,          # EXISTING
) -> SkillResult | None:
```

Emits events with origin:

```python
self._emit_event("lazy_routing_started", {
    "user_request": user_request[:200],
    "request_origin": request_origin,  # EXISTING
    "request_id": request_id,          # EXISTING
})
```

Adds origin to result:

```python
result.structured["request_origin"] = request_origin
result.structured["request_id"] = request_id
```

#### 4. Response Finalization (app/fairy_core.py)

Emits final response with origin:

```python
def _finalize_task(self, task_id: str, user_request: str, result: SkillResult, *, task_category: str = "") -> None:
    self._emit_event("final_response_ready", {
        "response_text": result.response_text,
        "request_origin": result.structured.get("request_origin", "main_chat"),  # NEW
        "request_id": result.structured.get("request_id", ""),                   # NEW
    })
```

#### 5. UI Response Handler (app/assistant_mode.py)

Filters responses by origin:

```python
def _on_assistant_reply(self, payload: object) -> None:
    if self._shutting_down or not isinstance(payload, dict):
        return

    # Filter responses by origin - main_chat window only processes main_chat responses
    request_origin = payload.get("request_origin", "main_chat")
    if request_origin != "main_chat":
        logger.info("assistant_reply_filtered_by_origin origin=%s expected=main_chat", request_origin)
        return  # NEW: Ignore responses from other endpoints

    # Process response...
```

## Event Flow with Origin

### Events Emitted

1. **user_request_received**
   ```python
   {
       "text": "What is the weather?",
       "origin": "main_chat",
       "request_id": "req-001"
   }
   ```

2. **lazy_routing_started**
   ```python
   {
       "user_request": "What is the weather?",
       "request_origin": "main_chat",
       "request_id": "req-001"
   }
   ```

3. **lazy_routing_completed**
   ```python
   {
       "skill": "realtime-lookup",
       "confidence": 0.95,
       "reason": "weather query detected",
       "request_origin": "main_chat",
       "request_id": "req-001"
   }
   ```

4. **skill_result_ready**
   ```python
   {
       "skill_name": "realtime-lookup",
       "success": true,
       "request_origin": "main_chat"
   }
   ```

5. **final_response_ready**
   ```python
   {
       "response_text": "Current weather: 22°C, Sunny",
       "request_origin": "main_chat",
       "request_id": "req-001"
   }
   ```

## UI Component Filtering

### Main Chat Window (FairyPresenceWindow)

```python
def _on_assistant_reply(self, payload: object) -> None:
    request_origin = payload.get("request_origin", "main_chat")
    if request_origin != "main_chat":
        return  # Ignore desktop_pet responses

    # Process and display response
```

### Desktop Pet Window (DesktopPetWindow)

```python
def _on_assistant_reply(self, payload: object) -> None:
    request_origin = payload.get("request_origin", "main_chat")
    if request_origin != "desktop_pet":
        return  # Ignore main_chat responses

    # Process and display response
```

## Backward Compatibility

- Default `request_origin` is `"main_chat"` for existing code
- Default `request_id` is empty string
- Existing code that doesn't pass these parameters continues to work
- All UI components default to filtering for `"main_chat"` origin

## Testing

Run the multi-endpoint routing tests:

```bash
python tests/test_multi_endpoint_routing.py
```

Tests verify:
- Request origin tracking through pipeline
- FairyCore origin propagation
- Response origin filtering
- Origin in SkillResult
- Event emission with origin
- Multi-endpoint routing scenario
- UI origin filtering
- Backward compatibility

## Implementation Checklist

- [x] ChatWorker accepts request_origin and request_id
- [x] FairyCore.handle_request() accepts and propagates origin
- [x] lazy_dispatcher already implements origin tracking
- [x] SkillResult includes origin in structured data
- [x] Events include origin metadata
- [x] _finalize_task() emits origin in final_response_ready
- [x] _on_assistant_reply() filters by origin
- [x] Backward compatibility maintained
- [ ] DesktopPetWindow implements origin filtering
- [ ] Update UI entry points to pass request_origin
- [ ] Create integration tests for multi-endpoint scenarios

## Future Enhancements

1. **Session Isolation**: Each endpoint maintains separate session context
2. **Concurrent Requests**: Handle multiple concurrent requests from different endpoints
3. **Request Prioritization**: Prioritize responses based on endpoint
4. **Analytics**: Track request/response metrics per endpoint
5. **Endpoint-Specific Behaviors**: Different response formats for different endpoints

## Debugging

Enable debug logging to trace origin through pipeline:

```python
logger.info("user_request_received text=%s origin=%s request_id=%s",
            user_request.strip(), request_origin, request_id)
logger.info("assistant_reply_filtered_by_origin origin=%s expected=main_chat",
            request_origin)
```

Check structured data in SkillResult:

```python
result.structured["request_origin"]  # Should match request origin
result.structured["request_id"]      # Should match request ID
result.structured["pipeline"]        # "new" or "legacy"
```
