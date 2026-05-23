# Debug Surface Regression Checklist

Use this checklist after any change to chat store, system panel, runtime voice debug, or resolution visibility.

## Must Be Visible

- `runtime_state` in the System Panel.
- `location_source` in the System Panel.
- `matched_rules` in the System Panel.
- `arbitration_correction_applied` in the System Panel.
- Voice state in the System Panel:
  - last voice
  - queue length
  - cooldown remaining
  - last runtime transition
- Timeline visibility:
  - `rule_preclassified`
  - `candidate_generated`
  - `arbitration_complete`
  - `arbitration_correction_applied`
  - `desktop_action_dispatch`
  - `desktop_action_result`

## Manual Scenarios

- `today weather` should show the detected current-city source and a weather trace.
- `new york time now` should show a time trace and a time card.
- `adelaide follow-up` should show follow-up override behavior.
- `chengdu -> show map` should show map follow-up behavior.
- Startup should show backend state, avatar state, and voice state moving together.

## Expected Outcome

- Debug surface should explain why a capability was selected.
- Voice debug should explain why a line played or was suppressed.
- The timeline should be sufficient to reconstruct the last resolution and execution path.
