# Mockups and implementation workflow

Use this workflow only after the user has reviewed the references, evidence, and directions.

## Generate comparable mockups

1. Read `run.json`. Image generation is allowed only when `state` is `directions_approved`, and only for IDs in `approved_direction_ids`.
2. Hold content and viewport constant across every approved direction. Translate each approved direction's six axes, evidence application, constraints, and full-screen composition into its prompt.
3. Create one pending entry per approved direction in `mockup-manifest.json` before calling the image tool. Record `direction_id`, `status`, `viewport`, `prompt_digest`, and attempt count. The initial batch must contain no more than five images.
4. If a user-provided or internal screenshot may contain personal, confidential, or proprietary information, warn the user before sending it to an external image-generation provider. Redact it or obtain informed confirmation; never put credentials, cookies, API keys, or pairing tokens in prompts or artifacts.
5. Generate one full-screen UI mockup per approved direction in a single host-supported batch when possible. Follow the host's image-generation response rule and end the turn immediately after emitting generated images. Do not add a textual summary or perform further manifest/state work in that turn.
6. On the next user turn, replace pending entries with the returned local output path or provider artifact hint in `output_ref`, plus `status`. Keep failure details. Permit one technical retry per failed direction; ask before additional variations or any larger image budget.
7. Validate mockups. Transition to `mockups_generated` only when every approved direction has one successful output. Ask the user to select one approved direction or an explicit combination before recording `implementation_selected`.

## Build an isolated preview

1. After selection, inspect the active repository's package manifests, routes, components, design tokens, scripts, and dirty state. Preserve unrelated user changes and use the project's existing stack and conventions.
2. Add an isolated route, screen, story, or component that cannot replace or silently affect the production screen. During exploration, never overwrite production files or production navigation.
3. If no suitable project exists, create a standalone Vite React TypeScript preview in an isolated directory. Keep its dependencies and entry point self-contained.
4. Reproduce the selected direction with real UI structure and the brief's required content. If the user selected a combination, record which approved direction supplies each combined property.
5. Render the preview at every target viewport and capture screenshots. Check content completeness, visual hierarchy, overflow, responsiveness, keyboard/focus behavior, semantic structure, contrast, and accessible names. Run relevant lint, typecheck, and tests for the active stack.
6. Write `implementation.json` with `selected_direction_id`, `mode`, `preview_path`, and `verification`. Record a non-empty `rendered_viewports` list and passing `content`, `overflow`, and `accessibility` checks. Validate it before transitioning to `prototype_ready`.
7. Present the verified isolated preview for review. Integrate into production only after explicit preview approval, then use the state command's explicit integration-approval flag.
