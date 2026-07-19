# Run and artifact contracts

Use this contract when creating, resuming, validating, or transitioning a design-explorer run. Run commands from the `design-explorer/` directory. Machine-readable JSON files are the sources of truth; Markdown files are user-facing views derived from them. Write user-facing views in the user's language, including Korean when the user speaks Korean.

## Run directory and states

The default run directory is `~/.codex/design-explorer/runs/<run-id>/`. Keep each exploration isolated in one run directory. New manifests use schema version 2. `run.json` records identity/state fields plus normalized, unique `target_viewports`, `required_content`, `required_interactions`, and safe project-relative `production_paths`. `project_path` is expanded and normalized to an absolute path at initialization. It also records approved/selected direction IDs, `revision_count`, generation limits, approval timestamps, and revision audit fields. Unsupported schemas must be migrated or reinitialized; do not edit a version number to simulate migration.

Follow this primary state sequence without skipping steps:

`initialized` → `brief_ready` → `research_complete` → `directions_pending_approval` → `directions_approved` → `mockups_generated` → `implementation_selected` → `prototype_ready` → `integrated`

The only deliberate revision edge is `mockups_generated` → `directions_pending_approval`, through the `revise` operation documented below.

Create or resume the run with these commands:

```bash
python3 scripts/run_state.py init --slug checkout --project-path /abs/project \
  --viewport 390x844 --viewport 1440x900 \
  --required-content "Order summary" --required-content "Total" \
  --required-interaction "Edit order" --production-path src/App.tsx
python3 scripts/run_state.py status --run <run-dir>
```

Repeat each flag as needed. Viewports use positive `WIDTHxHEIGHT` dimensions no greater than 10000. `brief_ready` requires at least one viewport and content item. UI runs should record every required interaction; a truly static brief may leave the list empty only when `brief.md` contains the exact line `Interactive requirements: none`.

The `brief_ready` transition atomically writes `brief_constraints`, `brief_constraints_digest` (SHA-256 of canonical sorted-key JSON), and RFC3339 `brief_locked_at`. Every later load and transition requires the current project path and four requirement lists to equal that snapshot and verifies its digest. This detects accidental single-field mutation inside the writable trust root; it does not protect against an attacker rewriting both the snapshot and digest.

After writing `brief.md`, transition to `brief_ready`:

```bash
python3 scripts/run_state.py transition --run <run-dir> --to brief_ready
```

Validate research and directions before their consuming transitions:

```bash
python3 scripts/validate_run.py --run <run-dir> --phase research
python3 scripts/run_state.py transition --run <run-dir> --to research_complete
python3 scripts/validate_run.py --run <run-dir> --phase directions
python3 scripts/run_state.py transition --run <run-dir> --to directions_pending_approval
python3 scripts/run_state.py transition --run <run-dir> --to directions_approved --approved-direction <id>
python3 scripts/run_state.py can-generate --run <run-dir>
```

Repeat `--approved-direction <id>` for every explicitly approved direction. At and after `directions_approved`, every load checks that approved IDs are non-empty, unique, present in the currently valid directions artifact, and within `generation_budget`. Run `can-generate --run <run-dir>` immediately before every provider call; invalid, tampered, or wrong-state runs fail closed with exit 1/`false`. After image output is recorded, execute the remaining steps in this order:

The approval count cannot exceed `generation_budget`. A user-approved expansion is explicit and auditable. Whenever either limit exceeds its default, `budget_expansion_approved_at` is required and must be a valid RFC3339 timestamp; the field is forbidden when neither limit is expanded:

```bash
python3 scripts/run_state.py transition --run <run-dir> --to directions_approved \
  --approved-direction <id> --generation-budget <n> \
  --max-attempts-per-direction <n> --approve-budget-expansion
```

Omit expansion flags for the default five-image/two-attempt limits. Every consuming transition invokes the relevant validator itself, including approval after review, selection after mockup generation, and integration after preview review. A validation failure leaves `run.json` unchanged; manual validator commands are useful diagnostics, not a substitute for the transition gate.

Every caller-supplied `now` value must be RFC3339. Initialization and transitions validate the completed manifest before writing it; revision validates its timestamp and completed manifest before any archive or manifest write.

```bash
python3 scripts/validate_run.py --run <run-dir> --phase mockups
python3 scripts/run_state.py transition --run <run-dir> --to mockups_generated
python3 scripts/run_state.py transition --run <run-dir> --to implementation_selected --selected-direction <id>
```

Build the isolated preview and write `implementation.json`, then validate it and stop at `prototype_ready`:

```bash
python3 scripts/validate_run.py --run <run-dir> --phase implementation
python3 scripts/run_state.py transition --run <run-dir> --to prototype_ready
```

Present and render the verified preview for the user. Stop and wait for explicit user integration approval; do not infer approval and do not run the integration command in the same sequence.

Only after the user explicitly approves production integration, run this separate post-approval command:

```bash
python3 scripts/run_state.py transition --run <run-dir> --to integrated --approve-integration
```

When the user requests a bounded variation or combination instead of selecting, do not run the selection transition. From `mockups_generated`, run:

```bash
python3 scripts/run_state.py revise --run <run-dir> --reason "<user-request>"
```

`revise` requires a non-empty reason and validates the current mockup manifest before archiving or mutating anything. It archives `mockup-manifest.json` as `mockup-manifest.revision-<n>.json`, increments `revision_count`, records `last_revision_reason` and `last_revision_at`, clears approved/selected IDs, resets the generation/attempt budgets to defaults, clears the expansion timestamp, and returns to `directions_pending_approval`. Append the derived direction, validate and present it, obtain explicit approval again, and then create a new manifest containing only newly approved IDs.

## Structured artifacts

### `brief.md`

Record the screen purpose, required content/interactions, target viewports, preservation constraints, supplied inputs, implementation context, and any sensitive-data handling decision. Keep these aligned with the corresponding `run.json` lists.

### Research artifacts

`references.json` is a JSON array. Every reference requires:

- `id`
- `title`
- `source_url` as a direct HTTP(S) URL
- `source_type`
- `captured_at`
- `relevance`
- `observations`, containing non-empty `layout`, `typography`, `palette`, `density`, `imagery`, and `interaction`

`capture_path` is optional and must be a safe relative artifact path with no absolute path, traversal, backslash escape, URL, or credentials. Store only a sanitized local capture, never session data or credentials. Source URLs must be public HTTP(S): no controls, URL userinfo, deterministic special-use/local/wildcard suffixes, or literal non-public IP hosts. The fetch/browser step must separately verify the final redirect URL and resolved destination because validation performs no live DNS.

`evidence.json` is a JSON array. Every evidence item requires:

- `id`
- `problem`
- `title`
- `publisher_or_author`
- `source_url` as a direct HTTP(S) URL
- `source_type`: `official`, `research`, or `observed`
- `summary`
- `application`
- `limitations`

`published_or_updated_at` is optional when unavailable. Do not invent it.

`references.json` and `evidence.json` are the machine-readable sources of truth. `reference-board.md` and `design-evidence.md` are user-facing views that retain IDs and direct URLs. Separate official guidance, research, observed patterns, and agent inference in `design-evidence.md`.

### Direction artifacts

`directions.json` is a JSON array with at least five items. Every direction requires:

- `id`
- `kind`: exactly `primary` or `derived`
- `name`
- `concept`
- `ux_problem`
- `evidence_ids`, a non-empty list of IDs from `evidence.json`
- `evidence_application`
- `baseline_exceptions`, a list that is empty normally; each exception requires a non-empty `constraint` and `justification`
- `axes`, containing non-empty `layout`, `typography`, `palette`, `density`, `imagery`, and `interaction`
- `tradeoffs`
- `implementation_difficulty`
- `implementation_risks`

Every direction must link at least one `official` evidence item. Official accessibility and platform guidance is the common baseline; exceptions document constrained deviations, not permission to omit official evidence. Every pair must differ on at least three axes.

- A `primary` direction omits `derived_from_ids` and `combined_properties`.
- A `derived` direction is appended after its sources. It requires a non-empty, unique `derived_from_ids` list of non-empty strings referring only to previously declared direction IDs. This ordering makes self-reference, forward-reference, dangling-reference, and cycles invalid.
- A `derived` direction also requires a non-empty `combined_properties` object. Keys are limited to the six design axes (`layout`, `typography`, `palette`, `density`, `imagery`, `interaction`); every value is one of that direction's prior `derived_from_ids`. After trimming and case-folding, the derived axis value must equal that named source's same axis value. Every source ID must contribute at least one mapped axis.

Never repurpose a primary direction ID. Append every revised variation or combination as a new first-class `derived` direction after its sources.

`directions.json` is the machine-readable source of truth. `mood-directions.md` is its user-facing view and must retain direction/evidence IDs, difficulty, risks, trade-offs, and every baseline exception. Approving a direction explicitly approves its disclosed exceptions.

### Mockup artifact

`mockup-manifest.json` contains a `mockups` list. A successful mockup requires:

- `direction_id`
- `status` set to `success`
- `viewport` as positive `WIDTHxHEIGHT`
- `prompt_digest` as `sha256:` followed by 64 lowercase hexadecimal characters
- `output_ref`
- positive integer `attempt_count`

The list cannot exceed `generation_budget`. It contains exactly one current entry per approved direction, no unapproved or duplicate IDs, and only `pending`, `success`, or `failed` status. Every entry, including pending/failed, requires the formatted viewport, digest, and an attempt count no greater than the authorized `max_attempts_per_direction`. Every approved direction needs one successful entry before `mockups_generated`. `output_ref`, when present, is a safe relative path or conservative provider artifact hint without userinfo or secrets. Pending or failed entries retain failure detail when available.

### Implementation artifact

`implementation.json` requires:

- `selected_direction_id`, matching the selected approved direction in `run.json`
- `mode`: `project` or `standalone`
- `preview_path`, included in non-empty `preview_files`; all are safe relative existing files
- `preview_route`, a normalized absolute URL path without traversal, query, or fragment
- `verification.rendered_viewports`, unique and exactly equal to `run.json.target_viewports`
- aggregate `verification.checks` whose `content`, `overflow`, and `accessibility` values are all `pass`
- `verification.viewport_checks`, keyed exactly by every target viewport

Each viewport record has `content`, `overflow`, `accessibility`, and `interaction` set to `pass`; exact-key `required_content` and `required_interactions` maps with every value `pass`; a safe run-relative `screenshot_ref`; and `source_digest`. The digest is SHA-256 over canonical sorted preview file paths and bytes, with length framing, and must match current files. The screenshot is stored inside the run directory. Validation checks complete PNG chunk structure: signature, first 13-byte IHDR and its dimensions, bounded chunks/file, CRC for every chunk, at least one IDAT, exactly one terminal zero-length IEND, and no truncation or trailing bytes. It does not decode pixels.

`preview_route` normalization uses strict UTF-8 percent decoding at every bounded decoding pass; malformed byte sequences fail instead of being replaced.

In project mode, `project_path` is an existing absolute directory. `implementation.json` also records safe included `route_registry_path` and `route_consumer_path`. The registry must map the exact `preview_route` to exactly `component_path` and a nonblank `shell_id`; that component is an included existing preview file. The consumer must have real static imports of both registry and component after comments are removed. Preview files exist beneath the project, every `production_paths` entry exists and cannot contain or equal a preview file, and a detached TSX file does not pass.

Validation walks the recursive local dependency closure from preview, consumer, and component roots across literal JS/TS imports and exports plus CSS imports and URLs. Every resolved file must remain contained and appear in `preview_files`, so `source_digest` binds the full declared closure. Runtime-computed imports cannot be proven by this static contract; the implementer must include them explicitly and verify the runtime path.

In standalone mode, production paths are empty and all files live beneath the run directory. `preview_files` includes valid `package.json` whose scripts execute `vite` and `vite build`, with nonblank `react`/`react-dom` and `vite`/`typescript`/`@vitejs/plugin-react` versions; `index.html` structurally loading `/src/main.tsx`; `vite.config.ts` importing `defineConfig` and the React plugin; `tsconfig.json` with JSX/module settings; `src/main.tsx` importing and mounting React App with `createRoot`; and an exported JSX `src/App.tsx`. Comments, quoted decoys, echo scripts, and inert placeholders do not satisfy this topology. Aggregate checks never substitute for per-viewport evidence. Tests use an in-process localhost HTTP server backed by the structured registry for route resolution, including a 404 probe. That proves deterministic topology and offline route resolution, not browser pixel rendering; screenshot evidence remains a separate renderer responsibility.

Validate this file before `prototype_ready`. The isolated preview remains separate from production until the user explicitly approves integration.

## Confidentiality

Credentials, cookies, API keys, and pairing tokens never belong in run artifacts. Validation recursively rejects secret-like JSON keys and realistic high-confidence Bearer, Slack, GitHub, Stripe, Google, AWS, OpenAI, and private-key values in research, direction, mockup, provider-hint, and implementation artifacts. Placeholders such as `Bearer <token>` and normal authentication UX prose are allowed. Do not copy browser storage, authorization headers, environment secrets, or raw sensitive screenshots into the run directory. Redact or omit sensitive material and record only the safe design observation needed for the exploration.
