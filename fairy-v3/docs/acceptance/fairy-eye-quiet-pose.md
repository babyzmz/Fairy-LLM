# Fairy Eye quiet pose — 2026-09-12

## User-observable contract

When the pointer moves away and the pet becomes quiet/sleeping, keep the centered,
round eye from the user's second screenshot. Do not lower or flatten the eye.
Quiet mode still stops its RAF; returning to an active state resumes at most one
animation loop. Neither the liquid-glass renderer nor native windows change.

## Root cause and scope

The existing quiet projection becomes `sleeping` in the motion snapshot and remains
`sleeping` through `petEyeState`. `fairyEye/runtime.ts` currently paints that state
as `translateY(42px) scale(.90,.35)`, reproducing the lowered, flattened first image.
Pointer gaze is bounded to 6/5 SVG pixels and cannot cause that deformation.

The SVG mount owns this presentation only. Keep the quiet state and scheduler
contract; reset its pose and gaze rather than mapping it to animated idle.
Two instances, quiet/active transitions, reduced motion and disposal must remain
independent. No tools, models, audio, settings or user data are affected.

## Verification

- Add a failing DOM regression for active gaze -> sleeping: centered round pose,
  zero gaze and no RAF; wake/resleep must not leak animation loops.
- Cover sleeping under Reduced Motion and another active eye simultaneously.
- Run TypeScript, targeted Fairy Eye tests, full Vitest, and browser visual checks.
- Native WebView2 pointer-away/return is a separate gate; browser geometry does not
  certify native hit-testing, DPI or placement. Avoid competing with user input.
- No existing assertions are weakened. Test-owned browsers/servers close together.

## Results

- Before the fix, both added DOM regressions failed with actual
  `translateY(42px) scale(.90,.35)` instead of a round centered pose.
- The renderer now uses the centered round transform and neutral motion sample
  while sleeping, and clears gaze immediately before stopping. Quiet/RAF and
  native policies are unchanged.
- `node node_modules/typescript/bin/tsc --noEmit`: passed.
- `node node_modules/vitest/vitest.mjs run src/fairyEye`: 12 passed.
- `node node_modules/@playwright/test/cli.js test e2e/fairy-eye-quiet.spec.ts --workers=2`:
  2 passed (10.7 s). Real Chromium SVG/CSS bounding boxes remain circular and
  centered within 0.1 CSS px through 20 cycles in each reduced-motion mode.
  `quiet-centered-eye.png` was visually inspected: round centered sclera/pupil,
  with no lowered ellipse. The Vite/browser lifecycle ended normally.
- Full Vitest initially had one unrelated task-page request-count failure
  (`App.test.tsx:234`, expected 1, received 2). No source or assertion there changed;
  its isolated 22 tests passed, then the full suite passed 112 files / 670 tests
  (21.63 s). The first failure is retained as an intermittent regression risk,
  not claimed fixed by this SVG change.
- Existing test file changed: `FairyEye.test.tsx` adds quiet-state geometry,
  gaze reset, RAF cleanup and neighboring-instance coverage; no original assertion
  was altered. The new browser test covers actual layout rather than source text.

Implementation and browser acceptance passed. Native WebView2 pointer-away/return
was not rerun; no user Fairy instance was restarted or operated in this pass.
