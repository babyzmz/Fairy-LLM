---
name: document-editing
description: Reads, writes, summarizes, and edits local text and document files. Use when the user asks to modify a file, summarize a document, or write content to disk.
allowed-tools: list_dir search_files read_text_file write_text_file summarize_document extract_sections propose_edit apply_patch
---

# Document Editing Skill

## When to use
- The user asks you to read, summarize, or analyze a local file.
- The user asks you to create a new file or write content to disk.
- The user asks you to modify, refactor, or fix an existing file.
- The user asks you to search for files in a directory.

## When NOT to use
- The user is asking to run code or execute terminal commands (use terminal-agent instead).
- The user is asking to search the web (use web-research instead).

## Core workflow
1. **Locate**: If the file path is not provided, use `list_dir` or `search_files` to find the target document.
2. **Read**: Use `read_text_file` to load the content into your context. For very large documents, use `summarize_document` or `extract_sections` to avoid context overflow.
3. **Analyze/Plan**: Understand the user's requested changes. Plan the edits carefully.
4. **Execute**:
   - For new files or complete rewrites: Use `write_text_file`.
   - For targeted changes in existing files: Use `propose_edit` to generate a patch, then `apply_patch` to apply it.
5. **Verify**: Briefly read the modified section to ensure the edit was applied correctly.

## Tool usage policy
- `read_text_file`: Always read the file before attempting to edit it. Do not guess the contents.
- `write_text_file`: Use only for creating new files or when rewriting the *entire* file is necessary.
- `propose_edit` & `apply_patch`: Prefer this combination for modifying existing files to minimize token usage and avoid accidental deletions.
- `summarize_document`: Use when the user asks for a summary of a large file that might exceed your context window.

## Failure handling
- **File not found**: Ask the user for the correct path or use `search_files` to look for it.
- **Patch fails to apply**: The file may have changed, or your proposed edit context was incorrect. Re-read the file and generate a new patch with more surrounding context.
- **Permission denied**: Inform the user that you lack the necessary permissions to read or write the file.

## Output contract
- Confirm to the user which files were read or modified.
- If summarizing, provide a clear, structured summary.
- If editing, briefly explain what changes were made. Do not output the entire file content to the user unless explicitly requested.

## Examples

**Task**: "Summarize the README.md file in the current directory."
**Action**:
1. Call `read_text_file` on "README.md".
2. Analyze the content.
3. Provide a concise summary to the user.

**Task**: "Change the title in index.html to 'My New App'."
**Action**:
1. Call `read_text_file` on "index.html".
2. Locate the `<title>` tag.
3. Call `propose_edit` to change the title, then `apply_patch`.
4. Inform the user the title was updated.
