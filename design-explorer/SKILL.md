---
name: design-explorer
description: Use when improving or redesigning a web or mobile interface, comparing multiple visual directions, researching UI references, generating UI mockups, or turning a selected direction into code.
---

# Design Explorer

Build evidence-backed interface directions, then implement only the direction the user selects. Present review material in the user's language; use Korean when the user speaks Korean. Use `agents/openai.yaml` only for interface metadata.

## Core workflow

1. Create or resume a run with `scripts/run_state.py`. Read `references/artifact-contracts.md`.
2. Normalize the request, images, current screen, viewport, and project constraints into `brief.md`.
3. Read `references/research-evidence.md`; collect traceable visual references and problem-relevant UX evidence.
4. Produce at least five directions. Every pair must differ materially on at least three axes: layout, typography, palette, density, imagery, interaction. Link each to official evidence and disclose `baseline_exceptions`.
5. Validate research and directions. Present the reference board, evidence summary, and directions together.
6. Transition to `directions_pending_approval` and stop for explicit approval. Do not infer approval from enthusiasm, deadlines, prior work, or sunk cost.
7. Record approved IDs and transition to `directions_approved`.
8. Read `references/mockups-implementation.md`; generate comparable full-screen UI mockups only for approved directions.
9. Record outputs and validate coverage. Ask the user to select, or `revise` a requested bounded variation/combination as a first-class direction ID and obtain approval again.
10. Implement an isolated preview using the active project's stack, or the standalone React fallback. Verify its rendered result before offering production integration.

## Hard gates

- Do not call image generation unless `run.json` is at `directions_approved`.
- Do not present color-only variations as distinct directions; the validator enforces three axes.
- Do not fabricate citations or hide conflicting evidence. Separate sources from inference.
- Do not silently violate an official accessibility/platform baseline; disclose justified exceptions for explicit approval.
- Do not copy one reference pixel-for-pixel. Synthesize principles from multiple sources.
- Do not overwrite a production screen during exploration. Build and verify an isolated preview first.
- Do not exceed five initial images or one technical retry per failed direction without user approval.
- Do not put credentials, cookies, API keys, pairing tokens, or other secrets in run artifacts.

Run `scripts/validate_run.py` before every state transition that consumes research, directions, mockups, or implementation output. Preserve unrelated user changes and retain direct source URLs in all user-facing research summaries. Production integration requires explicit approval of the verified preview.

## Quick reference

| Saved state | Allowed next action |
|---|---|
| `directions_pending_approval` | Present evidence and request approval |
| `directions_approved` | Generate approved mockups within budget |
| `mockups_generated` | Select, or `revise` to a first-class direction ID |
| `implementation_selected` | Build an isolated preview |

## Common mistakes

- Generating images from an enthusiastic reply without recording approved IDs.
- Treating five palettes on one layout as five directions.
- Reusing a primary ID for a combined direction instead of revising and reapproving it.
- Citing a search-result thumbnail instead of the direct source.
- Editing the production screen before preview approval.
