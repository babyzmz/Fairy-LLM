# Publication of remaining desktop recovery changes

## Scope

Preserve and commit the previously implemented Provider data-policy error category,
voice host diagnostic wording/probes, and optical/DPI brand-icon improvements.
No new release build, GPU session, database migration, privacy-policy downgrade or
user data export is part of this publication. The user explicitly confirmed that
the existing public `babyzmz/Fairy-LLM` repository should remain public.

## Submission audit

- Exact supplied OpenRouter credential scan across 102 outgoing revisions: no matches.
- Credential-pattern scan found only the existing literal private-key header test
  marker in `core/tests/test_memory_policy.py`, unchanged in these revisions.
- Outgoing object paths had no model runtime, node_modules, target, DPAPI, database
  or key-file candidates; no outgoing blob was larger than 10 MB.
- `CLAUDE.md`, `.fairy-svg-backups/`, ignored runtime/model/cache files remain local.
- Push the current `codex/fairy-stability-recovery` branch without rewriting history;
  do not silently merge into or force-push the default branch.

## Verification

- TypeScript passed. WorkspaceShell, brandIcons and SettingsApp: 53 Vitest tests passed.
- Core providers/preparation failure: 31 tests passed. OpenAI adapter: 19 passed.
- Native Windows icon loader DPI-size regression: one passed; Voice Worker unit
  regressions: 11 passed. Rust Clippy all targets passed.
- Brand generation `--check`: byte-for-byte matching assets.
- During finalization, the opt-in classifier probe was found to print a failed
  report but return exit code zero. A network-free entrypoint regression failed
  before the correction and passed after the script returned its report outcome.
  Combined probe/adapter regressions: 20 passed; affected Ruff checks passed. This
  prevents its Rust wrapper from reporting a false-positive live acceptance.
- Physical cross-monitor/titlebar visual checks and GPU Voice initialization are
  still unverified; the wording fix does not claim to repair GPU initialization.
- Previous full-suite exceptions remain in `composer-recovery.md`; this publication
  does not weaken or erase those failures. No live paid probe is rerun just to push.
