# Unified Skills and MCP Management Implementation Plan

## Task 1: Catalog and contracts

- Replace UI special cases with typed Core catalog recipes.
- Add Playwright, Context7, and GitHub MCP presets.
- Add import inspection/install/create contracts and generated client methods.
- Verify contract catalog and local/cloud parity.

## Task 2: Governed Skill import

- Add staging source adapters for Git, ZIP, and local folder.
- Add package inspection, metadata completion, quotas, reparse-point checks, and
  single-use inspection tokens.
- Reuse the existing atomic SkillManager installation/recovery boundary.
- Add Skill creation through generated package staging.

## Task 3: MCP preset installation

- Install presets through Core instead of React connection constants.
- Keep installed presets disabled until discovery and tool-policy acceptance.
- Model setup requirements and credential references without exposing secrets.

## Task 4: Symmetric Settings UI

- Give Skills and MCP separate `Installed` and `Store` views.
- Add source-specific Add menus and dialogs.
- Add search, empty/loading/error states, install progress, details, and explicit
  disabled reasons.
- Remove duplicate legacy extension surfaces and Context7 branching.

## Task 5: Real UI acceptance

- Install GSAP Core and GSAP ScrollTrigger from the Skill Store.
- Install Playwright MCP from the MCP Store, discover and accept its tools.
- Restart Core and verify persistence, enable/disable, remove, and failure paths.
- Run Core, TypeScript, Vitest, contract drift, and targeted Playwright checks.
