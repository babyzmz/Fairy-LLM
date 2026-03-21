---
name: terminal-agent
description: Executes shell commands, manages system operations, and interacts with the local environment. Use when the user asks to run scripts, install packages, or perform system administration tasks.
allowed-tools: read_file run_command search_web open_url browser_interact edit_file search_files
---

# Terminal Agent Skill

## When to use
- The user asks you to run a script (e.g., Python, Node.js, bash).
- The user asks you to install software or dependencies (e.g., pip install, npm install).
- The user asks you to perform system administration tasks (e.g., check disk space, manage processes).
- The user asks you to build, compile, or test a software project.

## When NOT to use
- The user is asking for general web research without needing to run local tools (use web-research).
- The user is asking to simply read or edit a document without executing it (use document-editing).

## Core workflow
1. **Understand Intent**: Determine exactly what command needs to be run to achieve the user's goal.
2. **Pre-flight Check**: If the command modifies the system or requires specific files, use `read_file` or `search_files` to verify the environment first.
3. **Execute**: Use `run_command` to execute the shell command.
4. **Analyze Output**: Carefully read the stdout and stderr from the command.
5. **Iterate/Fix**: If the command fails (non-zero exit code or error messages in stderr), analyze the error. You may need to use `search_web` to look up the error message, edit a configuration file using `edit_file`, and then retry the command.
6. **Report**: Inform the user of the final outcome, summarizing the command output.

## Tool usage policy
- `run_command`: The primary tool for this skill. Always ensure commands are safe and non-destructive unless explicitly requested by the user.
- `read_file` / `edit_file`: Use these to inspect or fix scripts/configs before or after running them.
- `search_web`: Crucial for debugging cryptic error messages encountered during execution.

## Failure handling
- **Command fails**: Do not just report the failure. Attempt to diagnose *why* it failed. Look up the error, check file permissions, or verify dependencies. Propose a fix and ask the user if you should proceed, or automatically retry if the fix is trivial.
- **Command hangs/times out**: The command might be waiting for interactive input. Ensure you are running commands in non-interactive modes (e.g., using `-y` flags).

## Output contract
- Clearly state what command was executed.
- Provide a concise summary of the result (success or failure).
- If an error occurred, explain the error in plain language and describe the steps taken to resolve it.

## Examples

**Task**: "Install the requests library for Python."
**Action**:
1. Call `run_command` with `pip install requests`.
2. Check output for success.
3. Tell the user the library was installed successfully.

**Task**: "Run my tests and fix any errors."
**Action**:
1. Call `run_command` with `pytest`.
2. If tests fail, read the error output.
3. Use `read_file` to look at the failing test file.
4. Use `edit_file` to fix the bug.
5. Call `run_command` with `pytest` again to verify.
6. Report the fixed tests to the user.
