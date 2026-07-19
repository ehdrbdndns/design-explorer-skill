# Mockups and implementation workflow

Use this workflow only after the user has reviewed the references, evidence, and directions.

## Generate comparable mockups

1. Read `run.json`. Image generation is allowed only when `state` is `directions_approved`, and only for IDs in `approved_direction_ids`.
2. Hold content and viewport constant across every approved direction. Translate each approved direction's six axes, evidence application, constraints, and full-screen composition into its prompt.
3. Create exactly one current pending entry per approved direction in `mockup-manifest.json` before calling the image tool. Record `direction_id`, `status`, `viewport` as positive `WIDTHxHEIGHT`, `prompt_digest` as `sha256:` plus 64 lowercase hexadecimal characters, and a positive integer `attempt_count`. Pending and failed entries retain these fields too. The list must remain within `run.json`'s `generation_budget` (default 5); attempts must remain within `max_attempts_per_direction` (default 2).
4. If a user-provided or internal screenshot may contain personal, confidential, or proprietary information, warn the user before sending it to an external image-generation provider. Redact it or obtain informed confirmation; never put credentials, cookies, API keys, or pairing tokens in prompts or artifacts.
5. Generate one full-screen UI mockup per approved direction in a single host-supported batch when possible. Follow the host's image-generation response rule and end the turn immediately after emitting generated images. Do not add a textual summary or perform further manifest/state work in that turn.
6. On the next user turn, replace pending entries with the returned safe relative local output path or conservative provider artifact hint in `output_ref`, plus `status`. Never store URL userinfo or credentials; provider hints receive the same secret scan as other JSON values. Keep failure details. Permit one technical retry per failed direction; ask before additional variations, more attempts, or a larger image budget. Record an approved expansion on the `directions_approved` transition with `--generation-budget`, `--max-attempts-per-direction`, and `--approve-budget-expansion`; the run records a valid RFC3339 approval timestamp.
7. Validate mockups. Transition to `mockups_generated` only when every approved direction has one successful output. Ask the user to select one direction or request a bounded variation/combination.

## Select or revise

- **Select:** Record the selected approved ID by transitioning to `implementation_selected`, then build its isolated preview.
- **Revise:** Only from `mockups_generated`, run `python3 scripts/run_state.py revise --run <run-dir> --reason "<user-request>"`. The command revalidates the current mockups before any archive or state mutation, then archives them as `mockup-manifest.revision-<n>.json`, records the audit reason/time/count, clears selection and approvals, and returns to `directions_pending_approval`.
- Append only the user-requested number of bounded derived directions to `directions.json`, after their sources. Give each a unique, first-class `id` and `kind: derived`; never reuse an arbitrary primary direction ID. Record unique, non-empty `derived_from_ids` containing only prior direction IDs. Record a non-empty `combined_properties` object whose keys are limited to the six axes and whose values name IDs in `derived_from_ids`. Each mapped derived axis must equal its named source's axis after trim/case normalization, and every source ID must contribute at least one axis. Retain the complete direction contract, including linked official evidence, `baseline_exceptions`, all six axes, risks, and trade-offs. A new direction must still pass the three-axis pairwise diversity rule.
- Update `mood-directions.md`, disclose evidence and baseline exceptions, validate directions, present the derived direction, and obtain explicit approval again. Do not generate its image before the new ID is recorded in `directions_approved`.
- Create a fresh `mockup-manifest.json` containing only the newly approved IDs. `revise` resets generation and attempt budgets to defaults and clears prior expansion approval. Generate at most the bounded variations the user authorized; record fresh explicit approval before any expansion.

## Build an isolated preview

1. After selection, inspect the active repository's package manifests, routes, components, design tokens, scripts, and dirty state. Preserve unrelated user changes and use the project's existing stack and conventions.
2. Add an isolated route, screen, story, or component that cannot replace or silently affect the production screen. During exploration, never overwrite production files or production navigation.
3. If no suitable project exists, create a standalone Vite React TypeScript preview in an isolated directory. Keep its dependencies and entry point self-contained.
4. Reproduce the selected first-class direction with real UI structure and the brief's required content. For a derived direction, follow its recorded `combined_properties` rather than recombining informally.
5. At every target viewport, capture a screenshot and compare the rendered screen against each required brief content item. CSS-hidden content passes only through an operable, tested disclosure path. Controls labeled `Edit`, `Change`, or `Apply` must function or must not be presented as a control. Check visual hierarchy, overflow, responsiveness, keyboard/focus behavior, semantic structure, contrast, and accessible names in the rendered and interactive state, not DOM presence. Run relevant lint, typecheck, and tests.
6. Write `implementation.json` with `selected_direction_id`, `mode`, `preview_path`, and `verification`. Record the rendered viewports and passing `content`, `overflow`, and `accessibility` checks. Do not record `content: pass` while required content is inaccessible. Validate before `prototype_ready`.
7. Present the verified isolated preview for review. Integrate into production only after explicit preview approval, then use the state command's explicit integration-approval flag.
