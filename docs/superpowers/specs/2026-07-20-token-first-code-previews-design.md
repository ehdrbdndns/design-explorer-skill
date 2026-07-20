# Token-First Code Previews for Design Explorer

## Goal

Make executable, project-native UI previews the default comparison artifact. When a real project exists, Design Explorer must inspect and reuse its design tokens, theme configuration, components, and frontend conventions before producing five materially distinct directions. AI image generation becomes an optional supporting step for visual assets, not the default way to express a UI direction.

## Chosen approach

Extend the existing schema-v2 workflow instead of introducing a schema-v3 migration. Keep the saved states and approval model, but interpret `mockups_generated` as “all approved directions have comparable rendered artifacts.” The normal artifact is a screenshot rendered from isolated code. A provider-generated UI image remains supported only as an explicitly approved exception.

This preserves existing runs, generation budgets, revision recovery, and integration gates while changing the agent's default behavior.

## Workflow

### 1. Inspect the implementation context before direction design

When a project is available, inspect:

- package manifests, framework, routes, and build commands;
- `tokens.css`, CSS custom properties, Tailwind or theme configuration, and typography definitions;
- reusable components and their supported variants;
- existing responsive, accessibility, dark-mode, and interaction conventions;
- protected production paths and unrelated dirty changes.

Record a concise design-system inventory in the run. It must identify the inspected token/theme sources and reusable component sources. Directions may propose different composition and hierarchy, but they must not invent replacement brand tokens when suitable project tokens exist.

### 2. Produce five distinct directions

Research and evidence review remain mandatory. Each direction still differs pairwise on at least three of layout, typography, palette, density, imagery, and interaction.

For project-backed exploration, the palette axis means different valid roles, emphasis, and combinations from the available system—not arbitrary new colors. Typography differences use existing families and tokens while changing scale, role, rhythm, and hierarchy. Any required token or component exception is disclosed with the existing baseline-exception mechanism before approval.

### 3. Build comparable isolated code previews

After explicit direction approval, create one isolated preview route, story, screen, or entry per approved direction. All directions use:

- the same brief content and target viewports;
- the project's stack and reusable components where available;
- the inspected token/theme sources rather than copied literal values;
- direction-specific layout, hierarchy, density, interaction, and component composition;
- no writes to protected production paths.

Render and interactively verify every direction at the shared target viewports. The comparison screenshot is a local rendered artifact, not an AI-generated substitute. Record the preview source files, route or entry, source digest, screenshot, token/theme sources, reused components, and per-viewport checks needed to reproduce each direction.

The existing `mockup-manifest.json` remains the comparison ledger. A normal code-backed entry succeeds with a local rendered PNG and no provider attempt. Provider accounting remains authoritative only for directions that actually call an external image provider.

### 4. Standalone fallback

If no suitable project exists, create one isolated Vite React TypeScript workspace with a shared local token layer. All five directions must consume that same token layer and component primitives. The token layer is derived from the brief, supplied brand material, and approved evidence; it is not presented as an existing product design system.

### 5. Optional image generation

Image generation is allowed only when:

- the user explicitly requests it; or
- an approved direction requires a photo, illustration, texture, or other visual asset that code and existing project assets cannot express.

Before any provider call, explain what will be generated and obtain explicit approval. Preserve the existing direction-specific `can-generate` and `authorize-generation` gates, privacy warning, budget, retry limit, and typed provider reference. Integrate the resulting asset into the code preview, then rerender the local comparison screenshot. A generated image alone does not satisfy the default code-preview requirement.

### 6. Selection, revision, and integration

Present the five rendered code previews with their evidence, token/component usage, trade-offs, and operable routes. The user may select a direction or request a bounded combination. A combination remains a new first-class direction ID, is rebuilt as an isolated code preview, and requires approval again.

After selection, refine the selected preview as the integration candidate. Production integration still requires explicit approval and must preserve unrelated changes.

## Compatibility and failure behavior

- Existing schema-v2 runs remain loadable.
- Existing provider-image runs continue to validate under their recorded contract.
- If declared token/theme sources are missing, unsafe, or changed after preview capture, validation fails closed and the preview must be rerendered.
- If a project lacks usable tokens or components, use the standalone fallback rather than silently inventing project-native values.
- If one direction cannot build or render, keep it failed and repair it; do not replace it with an unapproved generated UI image.
- External provider failure does not block a direction whose code preview works without that optional asset; record the failure and ask whether to retry, substitute a local asset, or proceed without it.

## Verification strategy

Use RED-GREEN skill tests and deterministic integration tests:

1. Add a baseline scenario with a project containing `tokens.css` and reusable components. The current skill should prefer provider mockups or defer code until after selection, demonstrating the missing behavior.
2. Add contract tests requiring token/component inspection, five isolated code previews before selection, and image generation as an explicit exception.
3. Add a disposable project fixture whose five direction previews reference real CSS variables and existing components while leaving production paths byte-identical.
4. Verify each direction has a distinct route or entry, shared viewports/content, source-bound screenshots, and at least three-axis diversity.
5. Add a no-project fixture that creates five previews from one standalone token layer.
6. Verify no provider authorization or attempt is recorded for the normal code-only path.
7. Verify optional visual-asset generation remains blocked until explicit approval and is rerendered into the code preview afterward.
8. Run the complete existing state, revision, publication, preview, confidentiality, and installation suites.
9. Forward-test the revised skill in fresh contexts on both project-backed and no-project requests.

## Non-goals

- Replacing the project's design system.
- Generating five production-ready implementations before the user chooses a direction.
- Removing image-generation support.
- Migrating existing runs to a new top-level run schema.
- Treating five token palettes on one layout as five directions.
