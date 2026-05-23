#!/usr/bin/env bash
# Companion doctrine enforcement. See docs/architecture/companion_doctrine.md.
# Exit non-zero on any violation. Safe to call from CI / pre-commit.

set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

if grep -rEn 'execute_task|LLMClient|provider_router|provider_complete' app/companion/ 2>/dev/null; then
  echo "[doctrine] Rule I violated: LLM call detected in app/companion/"
  fail=1
fi

if grep -E '^def |^class ' app/companion/prompt_addendum.py >/dev/null 2>&1; then
  echo "[doctrine] Rule II violated: prompt_addendum.py contains function/class (must be static constant)"
  fail=1
fi

if grep -rEn 'from app\.memory|import app\.memory' app/companion/ 2>/dev/null; then
  echo "[doctrine] Rule IV violated: app/companion/ imports from app/memory/ (must stay physically isolated)"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "[doctrine] clean"
fi
exit "$fail"
