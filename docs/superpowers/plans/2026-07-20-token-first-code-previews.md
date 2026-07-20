# Token-First Code Previews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make five isolated, token-backed code previews the default Design Explorer comparison output while keeping provider image generation as an explicitly approved optional asset step.

**Architecture:** Keep run schema v2 and `mockup-manifest.json` schema 1. Extend each mockup entry with an optional `artifact_kind: code-preview` contract containing reproducible preview topology, token/component provenance, source digest, and per-viewport evidence. Legacy entries remain valid; new code-preview entries may succeed with `attempt_count: 0`, while any provider attempt still uses the existing authorization ledger.

**Tech Stack:** Python 3 standard library, `unittest`, Markdown Agent Skills, React/Vite fixture files.

## Global Constraints

- Existing schema-v2 runs and provider-image manifests remain loadable.
- Research, five-direction diversity, explicit direction approval, revision, generation budget, and integration approval gates remain unchanged.
- Project-backed exploration uses existing token/theme and component sources before inventing local replacements.
- No-project exploration uses one shared standalone token layer across all five directions.
- AI image generation is optional and never substitutes for the default code preview.
- Protected production paths and unrelated changes remain byte-identical during exploration.
- `SKILL.md` remains under 500 body words and 500 lines.

---

### Task 1: Lock the token-first skill contract

**Files:**
- Modify: `tests/skill-scenarios.md`
- Modify: `tests/test_skill_contract.py`
- Modify: `design-explorer/SKILL.md`
- Modify: `design-explorer/references/mockups-implementation.md`
- Modify: `design-explorer/references/artifact-contracts.md`
- Modify: `design-explorer/agents/openai.yaml`

**Interfaces:**
- Consumes: the approved design in `docs/superpowers/specs/2026-07-20-token-first-code-previews-design.md`.
- Produces: the exact prose contract that Tasks 2–4 enforce.

- [ ] **Step 1: Add a failing skill scenario and contract test**

Append Scenario 6 to `tests/skill-scenarios.md`: an existing React project exposes `src/tokens.css` and reusable controls, the user asks for five directions, and the scenario forbids provider calls. Require inspection of tokens/components followed by five isolated code previews.

Add this test to `tests/test_skill_contract.py`:

```python
def test_token_first_code_preview_contract_is_documented(self):
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    mockups = (SKILL_DIR / "references/mockups-implementation.md").read_text(
        encoding="utf-8"
    )
    contracts = (SKILL_DIR / "references/artifact-contracts.md").read_text(
        encoding="utf-8"
    )
    metadata = (SKILL_DIR / "agents/openai.yaml").read_text(encoding="utf-8")
    combined = "\n".join((skill, mockups, contracts, metadata)).lower()
    for phrase in (
        "token-first",
        "five isolated code previews",
        "existing design tokens",
        "reusable components",
        "shared standalone token layer",
        "image generation is optional",
        "generated image alone does not satisfy",
        "artifact_kind: code-preview",
    ):
        self.assertIn(phrase, combined)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPATH=tests python3 -m unittest \
  tests.test_skill_contract.SkillContractTests.test_token_first_code_preview_contract_is_documented -v
```

Expected: FAIL because the token-first phrases are absent.

- [ ] **Step 3: Rewrite the workflow minimally**

In `SKILL.md`, replace image-first steps 8–10 with this order:

```markdown
8. Read `references/mockups-implementation.md`; inspect project tokens, theme, components, routes, and build commands. Build five isolated code previews for the approved directions, or one standalone workspace with a shared token layer when no project exists.
9. Render the same viewports, record code-preview evidence, and ask the user to select or `revise`. Treat image generation as an optional approved asset step; a generated image alone is not a completed direction preview.
10. Refine the selected preview, validate runtime wiring and render evidence, then integrate only after explicit approval.
```

Update `mockups-implementation.md` with separate “Build code previews first” and “Optional generated assets” sections. Update `artifact-contracts.md` with the new entry fields defined in Task 2. Change the OpenAI default prompt to end with “before building five token-backed code previews.” Keep existing approval, authorization, privacy, and retry language.

- [ ] **Step 4: Run the focused contract suite and word limit**

Run:

```bash
PYTHONPATH=tests python3 -m unittest tests.test_skill_contract -v
wc -w design-explorer/SKILL.md
```

Expected: all contract tests PASS and body word count no greater than 500.

- [ ] **Step 5: Commit**

```bash
git add tests/skill-scenarios.md tests/test_skill_contract.py \
  design-explorer/SKILL.md design-explorer/references/mockups-implementation.md \
  design-explorer/references/artifact-contracts.md design-explorer/agents/openai.yaml
git commit -m "docs: make code previews token first"
```

---

### Task 2: Validate code-preview provenance and zero-provider success

**Files:**
- Modify: `tests/test_validate_run.py`
- Modify: `design-explorer/scripts/validate_run.py`

**Interfaces:**
- Consumes: existing `preview_files_digest(root: Path, paths: list[str]) -> str | None`, safe-path, PNG, viewport, and dependency-closure helpers.
- Produces: `code_preview_errors(run_dir: Path, run: dict, item: dict, index: int) -> list[str]` and the `artifact_kind: code-preview` entry contract.

The new entry shape is:

```json
{
  "direction_id": "d-0",
  "artifact_kind": "code-preview",
  "status": "success",
  "viewport": "390x844",
  "prompt_ref": "prompts/d-0.txt",
  "prompt_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "attempt_count": 0,
  "output_kind": "local",
  "output_ref": "evidence/d-0/390x844.png",
  "preview_mode": "project",
  "preview_path": "previews/d-0/Screen.tsx",
  "preview_files": ["previews/d-0/Screen.tsx", "src/tokens.css", "src/Button.tsx"],
  "preview_route": "/design-explorer/d-0",
  "token_sources": ["src/tokens.css"],
  "used_tokens": ["--color-surface", "--space-4"],
  "component_sources": ["src/Button.tsx"],
  "supporting_provider_refs": [],
  "source_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "viewport_checks": {
    "390x844": {
      "screenshot_ref": "evidence/d-0/390x844.png",
      "content": "pass",
      "overflow": "pass",
      "accessibility": "pass",
      "interaction": "pass"
    }
  }
}
```

- [ ] **Step 1: Add failing validator tests**

Add five methods named `test_code_preview_success_allows_zero_provider_attempts`, `test_code_preview_requires_complete_topology_and_all_viewports`, `test_code_preview_tokens_must_be_defined_and_used`, `test_code_preview_sources_are_digest_bound_and_contained`, and `test_legacy_provider_image_manifest_remains_valid` to `ValidateRunTests`. Use the existing `setUp`, `write`, `mockup_manifest`, `preview_digest`, and `write_png` helpers. Each method writes real temporary CSS and TSX files before calling `validator.validate_mockups(self.run)`. Assert an empty error list for the complete zero-attempt code preview and the existing provider fixture. For each invalid mutation, assert the exact error fragment for missing sources, undefined tokens, unused tokens, stale digest, missing viewport evidence, unsafe paths, or provider attempt/accounting mismatch.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=tests python3 -m unittest \
  tests.test_validate_run.ValidateRunTests.test_code_preview_success_allows_zero_provider_attempts \
  tests.test_validate_run.ValidateRunTests.test_code_preview_requires_complete_topology_and_all_viewports \
  tests.test_validate_run.ValidateRunTests.test_code_preview_tokens_must_be_defined_and_used \
  tests.test_validate_run.ValidateRunTests.test_code_preview_sources_are_digest_bound_and_contained -v
```

Expected: FAIL because successful zero-attempt code previews and provenance fields are unsupported.

- [ ] **Step 3: Implement minimal validation helpers**

Add these signatures near the existing preview helpers:

```python
CSS_CUSTOM_PROPERTY_PATTERN = re.compile(r"(?m)(--[A-Za-z0-9_-]+)\s*:")

def css_custom_properties(paths: list[Path]) -> set[str]:
    values: set[str] = set()
    for path in paths:
        try:
            values.update(CSS_CUSTOM_PROPERTY_PATTERN.findall(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            continue
    return values

def code_preview_errors(
    run_dir: Path, run: dict, item: dict, index: int
) -> list[str]:
    """Validate one reproducible project or standalone direction preview."""
```

Inside `code_preview_errors`, resolve the source root from `preview_mode`, normalize and contain every path, require token/component sources to be included in `preview_files`, require every `used_tokens` value to be defined by `token_sources` and referenced with CSS such as `var(--color-surface)` in the preview dependency set, validate each optional `supporting_provider_refs` value with `_valid_provider_output_ref`, require `source_digest == preview_files_digest(source_root, preview_files)`, require exact target viewport keys and complete exact-size PNGs, and require every check to equal `pass`.

In `_mockup_manifest_errors`, set `is_code_preview = item.get("artifact_kind") == "code-preview"`. Permit `attempt_count == 0` for a successful code preview, but keep zero invalid for provider-image success. Add `code_preview_errors(run_dir, run, item, index)` to the entry errors. Existing entries without `artifact_kind` retain legacy behavior.

- [ ] **Step 4: Run focused and full validator tests**

Run:

```bash
PYTHONPATH=tests python3 -m unittest tests.test_validate_run -v
```

Expected: all validator tests PASS, including legacy provider cases.

- [ ] **Step 5: Commit**

```bash
git add tests/test_validate_run.py design-explorer/scripts/validate_run.py
git commit -m "feat: validate token-backed code previews"
```

---

### Task 3: Prove five project and standalone previews without provider calls

**Files:**
- Modify: `tests/test_workflow_integration.py`
- Modify: `tests/test_run_state.py` only if state fixtures need the new successful zero-attempt entry.

**Interfaces:**
- Consumes: the Task 2 code-preview manifest contract and unchanged state transitions.
- Produces: reproducible end-to-end project and standalone fixtures that reach selection without `authorize_generation`.

- [ ] **Step 1: Add failing five-direction integration tests**

Add methods named `test_project_builds_five_token_backed_previews_without_provider`, `test_standalone_builds_five_previews_from_one_shared_token_layer`, and `test_optional_provider_asset_still_requires_authorization` to `WorkflowIntegrationTests`.

The project fixture creates `src/tokens.css`, `src/Button.tsx`, five isolated route entries, five direction components, and exact screenshots. Approve `d-0` through `d-4`, write one code-preview entry per direction, assert `generation_attempts_used == 0`, assert an initially empty `provider_calls` list remains empty, and assert the production file hash is unchanged.

The standalone fixture creates one Vite React workspace with `src/tokens.css`, shared primitives, and five direction components/routes that all reference the same variables. The optional-provider test begins with a complete pending code-preview entry, proves the existing `can-generate`/`authorize-generation` sequence is still required, records a strict provider reference in `supporting_provider_refs`, then records the rerendered local PNG as the final `output_ref`.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=tests python3 -m unittest \
  tests.test_workflow_integration.WorkflowIntegrationTests.test_project_builds_five_token_backed_previews_without_provider \
  tests.test_workflow_integration.WorkflowIntegrationTests.test_standalone_builds_five_previews_from_one_shared_token_layer \
  tests.test_workflow_integration.WorkflowIntegrationTests.test_optional_provider_asset_still_requires_authorization -v
```

Expected: FAIL until all fixture fields satisfy the Task 2 validator and the workflow reaches `mockups_generated` without generation authorization.

- [ ] **Step 3: Add fixture helpers and make the integration path GREEN**

Add `write_project_direction_previews(self, run_dir: Path, project: Path, direction_ids: list[str]) -> dict` and `write_standalone_direction_previews(self, run_dir: Path, direction_ids: list[str]) -> dict`. Each helper writes the token layer, reusable primitive, route/entry files, five direction components, and exact PNG evidence, then returns a complete manifest whose attempt total is zero. Neither helper calls `authorize_generation`; both reuse `write_png` and `preview_digest`.

- [ ] **Step 4: Run workflow and full state suites**

Run:

```bash
PYTHONPATH=tests python3 -m unittest \
  tests.test_workflow_integration tests.test_run_state tests.test_publication_gates -v
```

Expected: all tests PASS with no generation or revision regression.

- [ ] **Step 5: Commit**

```bash
git add tests/test_workflow_integration.py tests/test_run_state.py
git commit -m "test: prove token-first preview lifecycle"
```

---

### Task 4: Forward-test, install, and publish the revised skill

**Files:**
- Modify only if forward testing exposes a transferable gap: `design-explorer/SKILL.md`, its direct references, or the tests that reproduce that gap.
- Sync after verification: `~/.codex/skills/design-explorer/` and `~/.claude/skills/design-explorer/`.

**Interfaces:**
- Consumes: Tasks 1–3 and the complete existing suite.
- Produces: reviewed repository HEAD, matching Codex and Claude installations, and updated private GitHub `main`.

- [ ] **Step 1: Run the complete verification suite**

Run:

```bash
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile design-explorer/scripts/*.py
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py design-explorer
git diff --check
```

Expected: zero failures and `Skill is valid!`.

- [ ] **Step 2: Forward-test two fresh-context requests**

Project-backed prompt: request five checkout directions in a fixture containing real tokens/components and explicitly forbid provider calls. No-project prompt: request five mobile onboarding directions with no repository. Verify that the first produces five isolated token-backed previews and the second chooses one shared standalone token layer. Neither should call image generation without a separate explicit request.

- [ ] **Step 3: Address only reproduced gaps with RED-GREEN**

If a forward test fails, add a focused failing contract or integration test first, make the smallest skill/reference change, rerun the failed scenario, then rerun the complete suite. Do not add speculative guidance.

- [ ] **Step 4: Review and commit any final adjustments**

```bash
git status --short
git diff --check
git add design-explorer/SKILL.md design-explorer/references/mockups-implementation.md \
  design-explorer/references/artifact-contracts.md tests/test_skill_contract.py \
  tests/test_validate_run.py tests/test_workflow_integration.py
git commit -m "fix: clarify token-first preview workflow"
```

Skip the commit when the worktree is already clean.

- [ ] **Step 5: Safely synchronize both installations**

First verify neither installed copy diverged from the pre-change repository commit. Then:

```bash
rsync -a --delete --delete-excluded --exclude='__pycache__' --exclude='*.pyc' \
  design-explorer/ ~/.codex/skills/design-explorer/
rsync -a --delete --delete-excluded --exclude='__pycache__' --exclude='*.pyc' \
  design-explorer/ ~/.claude/skills/design-explorer/
diff -rq design-explorer ~/.codex/skills/design-explorer
diff -rq design-explorer ~/.claude/skills/design-explorer
```

Expected: both diffs are empty after generated caches are removed.

- [ ] **Step 6: Verify Claude invocation and push**

Run a one-turn tool-disabled `/design-explorer` Claude CLI smoke test, rerun `gh auth status`, then:

```bash
git push origin main
test "$(git rev-parse HEAD)" = "$(git ls-remote origin refs/heads/main | cut -f1)"
```

Expected: Claude reports that `design-explorer` loaded, push succeeds, and local/remote hashes match.
