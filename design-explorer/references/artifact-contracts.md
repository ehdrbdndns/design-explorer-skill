# Run and artifact contracts

Use this contract when creating, resuming, validating, or transitioning a design-explorer run. Run commands from the `design-explorer/` directory. Machine-readable JSON files are the sources of truth; Markdown files are user-facing views derived from them. Write user-facing views in the user's language, including Korean when the user speaks Korean.

## Run directory and states

The default run directory is `~/.codex/design-explorer/runs/<run-id>/`. Keep each exploration isolated in one run directory. `run.json` records the schema version, run ID, slug, timestamps, project path, current state, approved direction IDs, selected direction ID, and—after approved production integration—the integration approval timestamp.

Follow this state sequence without skipping or reversing steps:

`initialized` → `brief_ready` → `research_complete` → `directions_pending_approval` → `directions_approved` → `mockups_generated` → `implementation_selected` → `prototype_ready` → `integrated`

Create or resume the run with these commands:

```bash
python3 scripts/run_state.py init --slug <lowercase-hyphen-slug> --project-path <absolute-path>
python3 scripts/run_state.py status --run <run-dir>
```

After writing `brief.md`, transition to `brief_ready`:

```bash
python3 scripts/run_state.py transition --run <run-dir> --to brief_ready
```

Validate every artifact set before the state transition that consumes it:

```bash
python3 scripts/validate_run.py --run <run-dir> --phase research
python3 scripts/run_state.py transition --run <run-dir> --to research_complete
python3 scripts/validate_run.py --run <run-dir> --phase directions
python3 scripts/run_state.py transition --run <run-dir> --to directions_pending_approval
python3 scripts/run_state.py transition --run <run-dir> --to directions_approved --approved-direction <id>
python3 scripts/validate_run.py --run <run-dir> --phase mockups
python3 scripts/validate_run.py --run <run-dir> --phase implementation
```

Repeat `--approved-direction <id>` for every explicitly approved direction. Complete later transitions with recorded IDs and explicit production approval:

```bash
python3 scripts/run_state.py transition --run <run-dir> --to mockups_generated
python3 scripts/run_state.py transition --run <run-dir> --to implementation_selected --selected-direction <id>
python3 scripts/run_state.py transition --run <run-dir> --to prototype_ready
python3 scripts/run_state.py transition --run <run-dir> --to integrated --approve-integration
```

## Structured artifacts

### `brief.md`

Record the screen purpose, required content, target viewport, preservation constraints, supplied inputs, implementation context, and any sensitive-data handling decision.

### Research artifacts

`references.json` is a JSON array. Every reference requires:

- `id`
- `title`
- `source_url` as a direct HTTP(S) URL
- `source_type`
- `captured_at`
- `relevance`
- `observations`, containing non-empty `layout`, `typography`, `palette`, `density`, `imagery`, and `interaction`

`capture_path` is optional. Store only a sanitized local capture, never session data or credentials.

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
- `name`
- `concept`
- `ux_problem`
- `evidence_ids`, a non-empty list of IDs from `evidence.json`
- `evidence_application`
- `axes`, containing non-empty `layout`, `typography`, `palette`, `density`, `imagery`, and `interaction`
- `tradeoffs`
- `implementation_difficulty`
- `implementation_risks`

Every pair must differ on at least three axes. `directions.json` is the machine-readable source of truth; `mood-directions.md` is its user-facing view and must retain direction/evidence IDs, difficulty, risks, and trade-offs.

### Mockup artifact

`mockup-manifest.json` contains a `mockups` list. A successful mockup requires:

- `direction_id`
- `status` set to `success`
- `viewport`
- `prompt_digest`
- `output_ref`

Every approved direction needs one successful entry before `mockups_generated`. Pending or failed entries retain the direction ID, status, viewport, prompt digest, attempt count, and failure detail when available.

### Implementation artifact

`implementation.json` requires:

- `selected_direction_id`, matching the selected approved direction in `run.json`
- `mode`: `project` or `standalone`
- `preview_path`
- `verification`, containing a non-empty `rendered_viewports` list and `checks` whose `content`, `overflow`, and `accessibility` values are all `pass`

Validate this file before `prototype_ready`. The isolated preview remains separate from production until the user explicitly approves integration.

## Confidentiality

Credentials, cookies, API keys, and pairing tokens never belong in run artifacts. Do not copy browser storage, authorization headers, environment secrets, or raw sensitive screenshots into the run directory. Redact or omit sensitive material and record only the safe design observation needed for the exploration.
