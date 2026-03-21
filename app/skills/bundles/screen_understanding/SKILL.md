---
name: screen-understanding
description: Captures and analyzes the user's current screen, active windows, and UI elements. Use when the user asks about what is on their screen or needs help with a visual application.
allowed-tools: capture_screen capture_active_window get_active_app get_accessibility_tree describe_visible_regions
---

# Screen Understanding Skill

## When to use
- The user asks "what am I looking at?", "what is on my screen?", or "read this error message".
- The user needs help navigating a desktop application they currently have open.
- The user asks you to extract text or data from an image or application window currently visible on their screen.

## When NOT to use
- The user is asking about a web page they want you to navigate to (use web-research).
- The user is asking to edit a local file (use document-editing).

## Core workflow
1. **Assess Context**: Determine if you need the whole screen or just the active window based on the user's request.
2. **Capture**: Use `capture_screen` or `capture_active_window` to get the visual context.
3. **Analyze**:
   - For general understanding, analyze the captured image.
   - For specific UI elements or text extraction, use `get_accessibility_tree` to get structured data about the UI.
   - Use `describe_visible_regions` if you need to locate specific buttons or areas.
4. **Respond**: Answer the user's question based on the visual and structural analysis.

## Tool usage policy
- `capture_active_window`: Prefer this over full screen capture if the user is asking about a specific app, to reduce noise and token usage.
- `capture_screen`: Use when the user asks about the overall desktop state or multiple windows.
- `get_accessibility_tree`: Crucial for accurately reading text, finding buttons, and understanding the structure of the UI without relying solely on computer vision.

## Failure handling
- **Screen capture fails**: Ensure the agent has the necessary OS permissions for screen recording. Inform the user if permissions are missing.
- **Accessibility tree is empty**: Some applications (e.g., games, certain electron apps) do not expose accessibility trees. Fall back to purely visual analysis of the screenshot.

## Output contract
- Describe what you see clearly and concisely.
- If the user asks for text extraction, provide the exact text found on the screen.
- If guiding the user, use clear directional language (e.g., "Click the blue 'Submit' button in the top right corner").

## Examples

**Task**: "What is the error message in this dialog box?"
**Action**:
1. Call `capture_active_window`.
2. Analyze the image to read the error text.
3. Tell the user the exact error message and suggest a solution.

**Task**: "Read the numbers from this spreadsheet I have open."
**Action**:
1. Call `get_active_app` to confirm it's a spreadsheet.
2. Call `get_accessibility_tree` to extract the structured grid data.
3. Present the extracted numbers to the user.
