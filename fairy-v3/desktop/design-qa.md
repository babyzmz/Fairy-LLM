# Chat Line Sidebar Design QA

## Comparison target

- Source visual truth:
  `C:\Users\54487\AppData\Local\Temp\codex-clipboard-4654e20c-9ddd-4521-b76e-5851839e9837.png`
- Browser-rendered implementation:
  `test-results\chat-workspace-the-Line-Si-be18c-thout-taking-over-scrolling-functional\chat-line-sidebar.png`
- Focused implementation crop:
  `test-results\chat-workspace-the-Line-Si-be18c-thout-taking-over-scrolling-functional\chat-line-sidebar-focused.png`
- Combined comparison:
  `test-results\chat-line-sidebar-comparison.png`
- State: dark theme, first Turn exchange hovered, detached preview visible.
- CSS viewport: 880 × 680 at device scale factor 1.
- Source pixels: 466 × 154.
- Focused implementation pixels: 414 × 85.
- Full implementation pixels: 880 × 680.

The reference is a component crop rather than a full application viewport. The
focused implementation crop was aligned from the first line marker through the
right edge of the card and padded vertically beside the source without rescaling.
The full screenshot was reviewed separately to confirm integration with the
transcript, Composer, inspector, and right scrollbar.

## Full-view comparison evidence

The implementation keeps the Line Sidebar on the left edge of the transcript and
the Composer and inspector remain in their established Fairy layout. The detached
card overlays the transcript without changing its measure or creating document
overflow. The line stack remains the only persistent sidebar surface; the card is
visible only for the hovered exchange.

Primary interactions verified:

- pointer hover opens the exact exchange card;
- keyboard focus opens the same card;
- one Turn maps to one marker;
- early-message activation navigates to the request anchor;
- independent outline browsing pauses and resumes active-item following;
- 880 × 680 and 640 × 700 bounds remain inside the chat viewport;
- Reduced Motion removes continuing transition behavior.

The focused Playwright run reported no browser console errors. Motion's documented
Reduced Motion warning is expected in the dedicated accessibility scenario.

## Focused comparison evidence

The combined source-and-implementation image shows the same essential construction:
a narrow transparent stack of short horizontal markers, one emphasized current
marker, a separated dark rounded card, a stronger single-line request title, and a
lower-contrast response excerpt. Card separation, border restraint, corner radius,
and title/body hierarchy are visually consistent with the source.

No image or icon assets exist in this component. The marker is native interface
geometry in both products, so there is no raster asset substitution or quality
loss to assess.

## Required fidelity surfaces

- Fonts and typography: Fairy retains its application UI family and slightly
  smaller density. Request weight, one-line truncation, response contrast, line
  height, and three-line clamp match the reference hierarchy.
- Spacing and layout rhythm: marker spacing and marker-to-card gap match the source
  pattern. The 360-pixel Fairy card is intentionally narrower than the roughly
  400-pixel source card and remains responsive at 640 pixels.
- Colors and visual tokens: card and text values closely match the neutral source
  surface. The active marker uses Fairy cyan instead of source white as an
  intentional product token.
- Image quality and asset fidelity: not applicable; the reference contains no
  imagery, logos, illustrations, or non-standard icons.
- Copy and content: the title is the user request and the body is the paired Fairy
  response. The fixture is shorter than the source copy, while production content
  can occupy the verified three-line response clamp.

## Findings

- P0: none.
- P1: none.
- P2: none.
- P3: Fairy's card is denser and its active marker is cyan. Both are intentional
  adaptations to the existing Fairy design system and do not reduce clarity.

## Comparison history

### Pass 1

No actionable P0/P1/P2 mismatch was found. The focused evidence crop was tightened
to remove unrelated history-navigation pixels; no product CSS or component fix was
required after the comparison.

## Implementation checklist

- Preserve the 36-pixel transparent Line Sidebar.
- Preserve one detached card for the exact hovered or focused Turn.
- Preserve request-title and response-body hierarchy.
- Retain the 360-pixel cap, viewport clamp, Reduced Motion behavior, and native
  transcript scrollbar.

final result: passed
