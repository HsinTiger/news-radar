# Meta three-card design and publishing contract

Status: active implementation contract, 2026-07-29.

## Reader journey

Every Facebook, Instagram, and Threads post must render and publish exactly
three cards in this order:

1. `cover` — existing robot/owl mascot plus a current, source-bounded hook.
2. `evidence` — one claim, a named primary source, and one bounded number or
   concrete proof block.
3. `action` — two or three reader checks/actions plus one specific, answerable
   question.

The three cards must be understandable without opening the caption. The
caption remains platform-native and must not be a verbatim transcript of the
cards.

## Platform layouts

| Platform | Canvas | Reading behavior |
| --- | --- | --- |
| Facebook | 1080 x 1080 | Square composition; mascot and hook share the frame. |
| Instagram | 1080 x 1350 | 4:5 portrait; large hook and thumb-friendly evidence hierarchy. |
| Threads | 1080 x 1350 | 4:5 portrait; same evidence sequence, Threads-native caption. |

All layouts keep a 6.3% horizontal safe area. Important text is not placed in
the bottom brand band. Body copy is never baked below 34 px at 1080 px width.

## Visual system

- Preserve the existing robot and owl assets; do not redraw or restyle the
  characters.
- Use paper cream, near-black, stone grey, and one sienna accent.
- Use one visual job per card and a visible `01/03`, `02/03`, `03/03` sequence.
- Prefer Traditional-Chinese system fonts when optional bundled fonts are not
  present. Tofu boxes are a render failure, not an acceptable fallback.
- The evidence card uses a dark proof panel; the action card uses one high
  contrast question panel. Decorative elements must not compete with the
  evidence.

## Fail-closed gates

Publishing is blocked when any of these is true:

- structured carousel content is missing or incomplete;
- built card count is not exactly three;
- card order is not `cover,evidence,action`;
- rendered path count or uploaded URL count is not exactly three;
- any platform carousel API call fails;
- the publisher receives any payload other than exactly three images.

No Meta path may fall back to a single image, text-only post, or a smaller
carousel. A failed tuple remains eligible for idempotent retry.

Reel generation is render/QC-only while this contract is active. Live Reel
publishing would create a non-carousel feed post and is therefore retired at
both workflow-input and script-entry levels.

## Evidence and learning

Delivery proof requires three uploaded URLs, a non-empty platform post ID, API
readback, and canonical format `carousel`. Reach is a separate claim. Capture
1h, 24h, and 168h views/reach, interactions, saves/shares, profile visits, and
follower delta before changing cadence or layout again.
