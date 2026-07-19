---
name: design-explorer
description: Use when redesigning web or mobile UI, comparing visual directions, researching references, generating mockups, or implementing a selected direction.
---

# Design Explorer

Build evidence-backed directions, then implement selection in user's language, including Korean. `agents/openai.yaml` is interface metadata only.

## Core workflow

1. Create or resume a run with `scripts/run_state.py`, recording target viewports, required content/interactions, and protected production paths. Read `references/artifact-contracts.md`.
2. Normalize the same requirements into `brief.md`; the machine-readable run fields are the verification source of truth.
3. Read `references/research-evidence.md`; collect traceable visual references and problem-relevant UX evidence.
4. Produce at least five directions. Every pair differs on at least three axes: layout, typography, palette, density, imagery, interaction. Link official evidence and disclose `baseline_exceptions`.
5. Validate research and directions. Present every direction in the response using the complete user-facing block in `references/research-evidence.md`; artifact links are not substitutes.
6. Transition to `directions_pending_approval` and stop for explicit approval. Never infer it.
7. Record approved IDs and transition to `directions_approved`.
8. Read `references/mockups-implementation.md`; run `can-generate` immediately before each provider call and generate comparable full-screen UI mockups only when it returns true.
9. Record outputs and validate coverage. Ask the user to select, or `revise` a bounded variation as a first-class direction ID and reapprove it.
10. Implement a structured-registry project preview or standalone fallback. Validate runtime-bound wiring and render-asset closure. Bind exact-size PNG evidence to current source bytes before integration.

## Hard gates

- Do not call image generation unless `scripts/run_state.py can-generate --run <run-dir>` exits 0 immediately before the call.
- Treat locked brief constraints, approved IDs, preview wiring, and `source_digest` as machine gates; never repair them by hand.
- Do not present color-only variations as distinct directions; the validator enforces three axes.
- Do not fabricate citations or hide conflicting evidence. Separate sources from inference.
- Do not silently violate an official accessibility/platform baseline; disclose justified exceptions for explicit approval.
- Do not copy one reference pixel-for-pixel. Synthesize principles from multiple sources.
- Do not overwrite a production screen during exploration. Build and verify an isolated preview first.
- Keep schema-v2 generation budgets: five images and two total attempts per direction by default. Expansion requires explicit recorded approval.
- Do not put credentials, cookies, API keys, pairing tokens, or other secrets in run artifacts.

Run `scripts/validate_run.py` before consuming transitions; `scripts/run_state.py` reruns validation before mutation. Preserve unrelated changes and direct source URLs. Production integration requires explicit approval of the verified preview.

## Quick reference

| Saved state | Allowed next action |
|---|---|
| `directions_pending_approval` | Present evidence and request approval |
| `directions_approved` | Generate approved mockups within budget |
| `mockups_generated` | Select, or `revise` to a first-class direction ID |
| `implementation_selected` | Build an isolated preview |

## Common mistakes

- Treating five palettes on one layout as five directions.
- Reusing a primary ID for a combined direction instead of revising and reapproving it.
- Citing a search-result thumbnail instead of the direct source.
- Editing the production screen before preview approval.
