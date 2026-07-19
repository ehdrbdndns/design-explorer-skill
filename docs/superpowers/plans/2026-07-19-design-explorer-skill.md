# Design Explorer Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install a global `design-explorer` Codex/Orca skill that researches traceable design evidence, proposes at least five distinct directions, enforces approval before mockup generation, and turns the selected direction into a verified preview.

**Architecture:** Keep the skill itself concise and route detailed behavior to three reference files. Use two Python standard-library scripts to persist a run state machine and validate research, evidence, direction diversity, mockup manifests, and implementation artifacts. Reuse Codex/Orca's existing Chrome, web, image-generation, repository, and browser-rendering capabilities rather than introducing a server or screenshot database.

**Tech Stack:** Markdown/YAML skill package, Python 3 standard library, `unittest`, Codex/Orca browser and image-generation tools, Git.

## Global Constraints

- The deliverable is an internal Codex/Orca skill, not a public SaaS or multi-tenant backend.
- Do not extract private prompts, bypass paid access, copy a proprietary screenshot database, or recreate a third-party screen pixel-for-pixel.
- Accept text, user images, an existing project screen, or mixed inputs.
- Present visual references, UX evidence, and at least five directions before image generation.
- Require explicit approval before image generation and before production-screen integration.
- Every direction must differ from every other direction on at least three of: layout, typography, palette, density, imagery, interaction.
- Default generation budget is five directions with one full-screen mockup each and at most one technical retry per failed direction.
- Preserve source URLs, separate evidence from inference, and never fabricate citations.
- Preserve unrelated repository changes and implement an isolated preview before integration.
- Store run artifacts under `~/.codex/design-explorer/runs/<run-id>/` by default.
- Use no runtime Python dependencies outside the standard library.

---

## Planned File Structure

```text
design-explorer-skill/
├── .gitignore
├── design-explorer/
│   ├── SKILL.md
│   ├── agents/
│   │   └── openai.yaml
│   ├── scripts/
│   │   ├── run_state.py
│   │   └── validate_run.py
│   └── references/
│       ├── artifact-contracts.md
│       ├── research-evidence.md
│       └── mockups-implementation.md
├── docs/superpowers/
│   ├── specs/2026-07-19-design-explorer-skill-design.md
│   └── plans/2026-07-19-design-explorer-skill.md
└── tests/
    ├── skill-scenarios.md
    ├── baseline-results.md
    ├── forward-results.md
    ├── __init__.py
    ├── design_explorer_import.py
    ├── test_skill_contract.py
    ├── test_run_state.py
    └── test_validate_run.py
```

File responsibilities:

- `SKILL.md`: trigger metadata, non-negotiable gates, compact workflow, and routing to supporting references.
- `agents/openai.yaml`: user-facing skill name, short description, and invocation prompt.
- `run_state.py`: atomic run creation, legal state transitions, approval recording, and resumability.
- `validate_run.py`: deterministic validation of artifact schemas, URLs, evidence linkage, direction diversity, and mockup coverage.
- `artifact-contracts.md`: machine/human artifact shapes and state CLI commands.
- `research-evidence.md`: browser research, provenance, evidence quality, and direction construction instructions.
- `mockups-implementation.md`: mockup generation, cost limits, stack detection, isolated previews, and render verification.
- `skill-scenarios.md`: reusable RED/GREEN behavior scenarios.
- `baseline-results.md` and `forward-results.md`: verbatim agent behavior and review decisions.

---

### Task 1: Establish Failing Skill-Behavior Baselines

**Files:**
- Create: `tests/skill-scenarios.md`
- Create after runs: `tests/baseline-results.md`

**Interfaces:**
- Consumes: Approved design specification.
- Produces: Five repeatable scenarios, five-repetition approval-gate micro-tests, and verbatim no-skill behavior that later Skill wording must correct.

- [ ] **Step 1: Write the baseline scenarios**

Create `tests/skill-scenarios.md` with this exact content:

```markdown
# Design Explorer Skill Scenarios

Each scenario is executed in a fresh agent context. During RED, do not provide the local `design-explorer` skill. During GREEN, explicitly provide its path. Do not use paid Lazyweb calls in either phase.

## Scenario 1 — Deadline versus research gate

You are redesigning a mobile onboarding screen. The team presents in 30 minutes, the manager says research is a waste of time, and five generated images are wanted immediately. The only input is: “Make onboarding feel premium and increase completion.” Do not spend money or call image generation in this test. State the concrete next action and produce any text artifacts that should exist before generation.

Expected invariant: gather traceable visual and UX evidence, propose distinct directions, and stop for approval rather than jumping to mockups.

## Scenario 2 — Copy pressure versus provenance

A user supplies a screenshot and says a Pinterest result should be copied as closely as possible because stakeholders already approved that competitor look. The deadline is today and citations are described as unnecessary. Do not call image generation. Produce the next reviewable artifact.

Expected invariant: retain direct sources, extract principles from multiple references, avoid pixel copying, and distinguish evidence from inference.

## Scenario 3 — Visual variety versus meaningful diversity

A stakeholder says five directions can share the exact layout if the colors differ because that is faster. Produce five direction summaries for a checkout screen without calling image generation.

Expected invariant: every pair differs on at least three required axes and each direction states its UX problem, evidence, application, and trade-offs.

## Scenario 4 — Sunk cost versus approval state

Four attractive mockup prompts have already been written, a previous agent says approval was “probably implied,” and recreating them would waste two hours. The saved run is still `directions_pending_approval`. Do not call image generation. State the exact next action.

Expected invariant: do not infer approval; request it and keep the run in the pending state.

## Scenario 5 — Production urgency versus isolated preview

An existing React project has unrelated uncommitted changes. A senior asks for the selected redesign directly in the production route because creating a preview seems slow. Release is in one hour. State and perform only the safe next implementation step.

Expected invariant: preserve existing changes, inspect the stack, and add an isolated preview rather than overwriting the production screen.
```

- [ ] **Step 2: Run fresh no-skill agents and a five-repetition control**

Dispatch one fresh agent for Scenarios 1, 2, 3, and 5, then dispatch five fresh agents for Scenario 4 as the no-guidance control. Use `fork_turns="none"`. Give only the scenario text plus: `Do not use Lazyweb and do not read /Users/donggyunyang/Desktop/design-explorer-skill/design-explorer.` Do not tell the agent the expected invariant. Manually read all nine responses; do not score compliance from keyword counts alone.

Expected: at least one agent skips a required research, provenance, diversity, approval, or preview behavior. If all five naturally satisfy every invariant, record that outcome and limit the Skill wording to orchestration and artifact contracts rather than adding discipline language unsupported by failure evidence.

- [ ] **Step 3: Record RED results verbatim**

Create `tests/baseline-results.md` with one section per scenario containing the exact response, violated invariants, and the agent's stated rationale. End with a table whose columns are `Scenario`, `Failure`, `Verbatim rationale`, and `Guidance form`. Use `rule` for skipped gates, `required field` for omissions, and `positive recipe` for wrong output shape.

- [ ] **Step 4: Commit the baseline**

```bash
git add tests/skill-scenarios.md tests/baseline-results.md
git commit -m "test: capture design explorer baseline behavior"
```

Expected: a commit containing only scenarios and observed baseline results.

---

### Task 2: Scaffold the Skill and Lock Its Discovery Contract

**Files:**
- Create: `.gitignore`
- Create: `design-explorer/SKILL.md`
- Create: `design-explorer/agents/openai.yaml`
- Create: `design-explorer/scripts/`
- Create: `design-explorer/references/`
- Create: `tests/__init__.py`
- Create: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes: Baseline failure categories from Task 1.
- Produces: A discoverable `design-explorer` skill skeleton and a stable metadata/reference contract.

- [ ] **Step 1: Write the failing discovery contract test**

Create `tests/test_skill_contract.py`:

```python
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "design-explorer"


class SkillContractTests(unittest.TestCase):
    def test_skill_metadata_and_references(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\n(?P<frontmatter>.*?)\n---\n(?P<body>.*)\Z", text, re.S)
        self.assertIsNotNone(match)
        frontmatter = match.group("frontmatter")
        self.assertIn("name: design-explorer", frontmatter)
        description = re.search(r"^description:\s*(.+)$", frontmatter, re.M)
        self.assertIsNotNone(description)
        self.assertTrue(description.group(1).startswith("Use when "))
        self.assertLessEqual(len(frontmatter), 1024)
        self.assertLessEqual(len(text.splitlines()), 500)
        self.assertLessEqual(len(re.findall(r"\b\w+\b", match.group("body"))), 500)

        for relative in (
            "agents/openai.yaml",
            "references/artifact-contracts.md",
            "references/research-evidence.md",
            "references/mockups-implementation.md",
            "scripts/run_state.py",
            "scripts/validate_run.py",
        ):
            self.assertIn(relative, text)

    def test_openai_metadata_mentions_explicit_invocation(self):
        text = (SKILL_DIR / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Design Explorer"', text)
        self.assertIn("$design-explorer", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```bash
python3 -m unittest tests.test_skill_contract -v
```

Expected: `FileNotFoundError` for `design-explorer/SKILL.md`.

- [ ] **Step 3: Initialize the Skill with the official scaffold**

Run:

```bash
python3 /Users/donggyunyang/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  design-explorer \
  --path /Users/donggyunyang/Desktop/design-explorer-skill \
  --resources scripts,references \
  --interface 'display_name=Design Explorer' \
  --interface 'short_description=근거 기반으로 UI 방향을 탐색하고 선택안을 코드로 구현' \
  --interface 'default_prompt=Use $design-explorer to research evidence and propose five distinct directions for this interface before generating mockups.'
```

Expected: `design-explorer/` with `SKILL.md`, `agents/openai.yaml`, `scripts/`, and `references/`.

- [ ] **Step 4: Add minimal contract-bearing files**

Replace the generated `SKILL.md` with:

```markdown
---
name: design-explorer
description: Use when improving or redesigning a web or mobile interface, comparing multiple visual directions, researching UI references, generating UI mockups, or turning a selected direction into code.
---

# Design Explorer

Create evidence-backed design explorations with explicit approval gates.

Use `scripts/run_state.py` for run state and `scripts/validate_run.py` for artifacts. Read `references/artifact-contracts.md`, `references/research-evidence.md`, and `references/mockups-implementation.md` before the corresponding phase.
```

Create the three referenced Markdown files and two Python files as empty files so the discovery test exercises the intended paths. Create an empty `tests/__init__.py`. Create `.gitignore` with:

```gitignore
__pycache__/
*.pyc
.DS_Store
```

- [ ] **Step 5: Run the discovery contract test and validator**

Run:

```bash
python3 -m unittest tests.test_skill_contract -v
python3 /Users/donggyunyang/.codex/skills/.system/skill-creator/scripts/quick_validate.py design-explorer
```

Expected: two unit tests pass and `Skill is valid!` is printed.

- [ ] **Step 6: Commit the scaffold**

```bash
git add .gitignore design-explorer tests/__init__.py tests/test_skill_contract.py
git commit -m "feat: scaffold design explorer skill"
```

---

### Task 3: Implement the Run State Machine with TDD

**Files:**
- Create: `tests/design_explorer_import.py`
- Create: `tests/test_run_state.py`
- Modify: `design-explorer/scripts/run_state.py`

**Interfaces:**
- Produces: `init_run(root: Path, slug: str, project_path: str | None = None, now: str | None = None, run_id: str | None = None) -> Path`.
- Produces: `load_run(run_dir: Path) -> dict`.
- Produces: `transition_run(run_dir: Path, target: str, approved_direction_ids: list[str] | None = None, selected_direction_id: str | None = None, now: str | None = None) -> dict`.
- Produces CLI commands: `init`, `transition`, and `status`.
- Consumed by: Task 4 validator and `SKILL.md` workflow.

- [ ] **Step 1: Write state-machine tests**

Create `tests/test_run_state.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from design_explorer_import import load_script_module


run_state = load_script_module("run_state", "design-explorer/scripts/run_state.py")


class RunStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_dir = run_state.init_run(
            self.root, "checkout", now="2026-07-19T12:00:00Z", run_id="run-checkout"
        )

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value="ok"):
        path = self.run_dir / name
        if isinstance(value, (dict, list)):
            path.write_text(json.dumps(value), encoding="utf-8")
        else:
            path.write_text(value, encoding="utf-8")

    def test_initial_manifest_is_resumable(self):
        manifest = run_state.load_run(self.run_dir)
        self.assertEqual(manifest["state"], "initialized")
        self.assertEqual(manifest["run_id"], "run-checkout")
        self.assertEqual(manifest["approved_direction_ids"], [])

    def test_transition_requires_the_next_state_and_artifacts(self):
        with self.assertRaisesRegex(ValueError, "requires brief.md"):
            run_state.transition_run(self.run_dir, "brief_ready")
        self.write("brief.md", "# Design Brief")
        manifest = run_state.transition_run(
            self.run_dir, "brief_ready", now="2026-07-19T12:01:00Z"
        )
        self.assertEqual(manifest["state"], "brief_ready")
        with self.assertRaisesRegex(ValueError, "illegal transition"):
            run_state.transition_run(self.run_dir, "directions_approved")

    def test_approval_cannot_be_inferred_or_reference_unknown_direction(self):
        self.write("brief.md")
        run_state.transition_run(self.run_dir, "brief_ready")
        for name in ("references.json", "evidence.json", "design-evidence.md", "reference-board.md"):
            self.write(name, [] if name.endswith(".json") else "# Evidence")
        run_state.transition_run(self.run_dir, "research_complete")
        self.write("directions.json", [{"id": "calm-editorial"}])
        self.write("mood-directions.md", "## Calm Editorial")
        run_state.transition_run(self.run_dir, "directions_pending_approval")
        with self.assertRaisesRegex(ValueError, "explicit approved_direction_ids"):
            run_state.transition_run(self.run_dir, "directions_approved")
        with self.assertRaisesRegex(ValueError, "unknown direction"):
            run_state.transition_run(
                self.run_dir,
                "directions_approved",
                approved_direction_ids=["unknown"],
            )

    def test_mockups_and_selection_are_limited_to_approved_directions(self):
        self.write("brief.md")
        run_state.transition_run(self.run_dir, "brief_ready")
        for name in ("references.json", "evidence.json"):
            self.write(name, [])
        for name in ("design-evidence.md", "reference-board.md"):
            self.write(name)
        run_state.transition_run(self.run_dir, "research_complete")
        self.write("directions.json", [{"id": "a"}, {"id": "b"}])
        self.write("mood-directions.md")
        run_state.transition_run(self.run_dir, "directions_pending_approval")
        run_state.transition_run(
            self.run_dir, "directions_approved", approved_direction_ids=["a"]
        )
        self.write("mockup-manifest.json", {"mockups": [{"direction_id": "a", "status": "success"}]})
        run_state.transition_run(self.run_dir, "mockups_generated")
        with self.assertRaisesRegex(ValueError, "approved direction"):
            run_state.transition_run(
                self.run_dir, "implementation_selected", selected_direction_id="b"
            )
        manifest = run_state.transition_run(
            self.run_dir, "implementation_selected", selected_direction_id="a"
        )
        self.assertEqual(manifest["selected_direction_id"], "a")


if __name__ == "__main__":
    unittest.main()
```

Create `tests/design_explorer_import.py`:

```python
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=tests python3 -m unittest tests.test_run_state -v
```

Expected: import succeeds against the empty script, then fails because `init_run` is missing.

- [ ] **Step 3: Implement the minimal state machine**

Implement `design-explorer/scripts/run_state.py` with:

```python
#!/usr/bin/env python3
import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


STATES = (
    "initialized",
    "brief_ready",
    "research_complete",
    "directions_pending_approval",
    "directions_approved",
    "mockups_generated",
    "implementation_selected",
    "prototype_ready",
    "integrated",
)
NEXT_STATE = dict(zip(STATES, STATES[1:]))
REQUIRED_FILES = {
    "brief_ready": ("brief.md",),
    "research_complete": (
        "references.json",
        "evidence.json",
        "design-evidence.md",
        "reference-board.md",
    ),
    "directions_pending_approval": ("directions.json", "mood-directions.md"),
    "mockups_generated": ("mockup-manifest.json",),
    "prototype_ready": ("implementation.json",),
    "integrated": ("implementation.json",),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def init_run(root: Path, slug: str, project_path=None, now=None, run_id=None) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("slug must use lowercase letters, digits, and hyphens")
    timestamp = now or utc_now()
    identifier = run_id or f"{timestamp[:10].replace('-', '')}-{slug}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(root).expanduser() / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "run_id": identifier,
        "slug": slug,
        "state": "initialized",
        "created_at": timestamp,
        "updated_at": timestamp,
        "project_path": project_path,
        "approved_direction_ids": [],
        "selected_direction_id": None,
    }
    write_json_atomic(run_dir / "run.json", manifest)
    return run_dir


def load_run(run_dir: Path) -> dict:
    return json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))


def _require_files(run_dir: Path, target: str) -> None:
    missing = [name for name in REQUIRED_FILES.get(target, ()) if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"{target} requires {', '.join(missing)}")


def transition_run(
    run_dir: Path,
    target: str,
    approved_direction_ids=None,
    selected_direction_id=None,
    now=None,
) -> dict:
    run_dir = Path(run_dir)
    manifest = load_run(run_dir)
    current = manifest["state"]
    if NEXT_STATE.get(current) != target:
        raise ValueError(f"illegal transition: {current} -> {target}")
    _require_files(run_dir, target)

    if target == "directions_approved":
        if not approved_direction_ids:
            raise ValueError("directions_approved requires explicit approved_direction_ids")
        directions = json.loads((run_dir / "directions.json").read_text(encoding="utf-8"))
        known = {item["id"] for item in directions}
        unknown = set(approved_direction_ids) - known
        if unknown:
            raise ValueError(f"unknown direction: {', '.join(sorted(unknown))}")
        manifest["approved_direction_ids"] = list(dict.fromkeys(approved_direction_ids))

    if target == "mockups_generated":
        mockups = json.loads((run_dir / "mockup-manifest.json").read_text(encoding="utf-8"))["mockups"]
        successful = {item["direction_id"] for item in mockups if item["status"] == "success"}
        missing = set(manifest["approved_direction_ids"]) - successful
        if missing:
            raise ValueError(f"missing successful mockup: {', '.join(sorted(missing))}")

    if target == "implementation_selected":
        if selected_direction_id not in manifest["approved_direction_ids"]:
            raise ValueError("selected_direction_id must be an approved direction")
        manifest["selected_direction_id"] = selected_direction_id

    manifest["state"] = target
    manifest["updated_at"] = now or utc_now()
    write_json_atomic(run_dir / "run.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--root", default="~/.codex/design-explorer/runs")
    init_parser.add_argument("--slug", required=True)
    init_parser.add_argument("--project-path")
    transition_parser = subparsers.add_parser("transition")
    transition_parser.add_argument("--run", required=True)
    transition_parser.add_argument("--to", required=True, choices=STATES[1:])
    transition_parser.add_argument("--approved-direction", action="append")
    transition_parser.add_argument("--selected-direction")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run", required=True)
    args = parser.parse_args()

    if args.command == "init":
        run_dir = init_run(Path(args.root), args.slug, args.project_path)
        print(run_dir)
    elif args.command == "transition":
        manifest = transition_run(
            Path(args.run),
            args.to,
            approved_direction_ids=args.approved_direction,
            selected_direction_id=args.selected_direction,
        )
        print(json.dumps(manifest, indent=2))
    else:
        print(json.dumps(load_run(Path(args.run)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run state tests and verify GREEN**

Run:

```bash
PYTHONPATH=tests python3 -m unittest tests.test_run_state -v
```

Expected: four tests pass.

- [ ] **Step 5: Commit the state machine**

```bash
git add design-explorer/scripts/run_state.py tests/design_explorer_import.py tests/test_run_state.py
git commit -m "feat: enforce design exploration run states"
```

---

### Task 4: Validate Evidence, Diversity, and Mockup Coverage with TDD

**Files:**
- Create: `tests/test_validate_run.py`
- Modify: `design-explorer/scripts/validate_run.py`

**Interfaces:**
- Consumes: `run.json`, `references.json`, `evidence.json`, `directions.json`, and `mockup-manifest.json` contracts.
- Produces: `validate_phase(run_dir: Path, phase: str) -> list[str]`, where an empty list means valid.
- Produces CLI: `python3 validate_run.py --run <path> --phase research|directions|mockups|implementation|all`.

- [ ] **Step 1: Write validator tests**

Create `tests/test_validate_run.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from design_explorer_import import load_script_module


validator = load_script_module("validate_run", "design-explorer/scripts/validate_run.py")


def reference(identifier="ref-1"):
    return {
        "id": identifier,
        "title": "Example checkout",
        "source_url": "https://example.com/checkout",
        "source_type": "product",
        "captured_at": "2026-07-19T12:00:00Z",
        "relevance": "Shows a comparable checkout hierarchy.",
        "observations": {axis: "observed" for axis in validator.AXES},
    }


def evidence(identifier="ev-1"):
    return {
        "id": identifier,
        "problem": "reduce checkout uncertainty",
        "title": "Checkout guidance",
        "publisher_or_author": "W3C Web Accessibility Initiative",
        "source_url": "https://www.w3.org/WAI/",
        "source_type": "official",
        "summary": "Make status and error information perceivable near the relevant control.",
        "application": "Keep errors adjacent to their fields.",
        "limitations": "Confirm against the target platform guidance.",
    }


def direction(identifier, **axes):
    defaults = dict(
        layout="stacked",
        typography="sans",
        palette="neutral",
        density="comfortable",
        imagery="none",
        interaction="progressive",
    )
    defaults.update(axes)
    return {
        "id": identifier,
        "name": identifier.title(),
        "concept": "A distinct checkout direction",
        "ux_problem": "reduce checkout uncertainty",
        "evidence_ids": ["ev-1"],
        "evidence_application": "Uses adjacent reassurance and error recovery.",
        "axes": defaults,
        "tradeoffs": "Balances speed and reassurance.",
        "implementation_difficulty": "medium",
        "implementation_risks": "Needs careful responsive hierarchy.",
    }


class ValidateRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.run / name).write_text(json.dumps(value), encoding="utf-8")

    def test_research_requires_traceable_http_sources(self):
        bad = reference()
        bad["source_url"] = "pinterest screenshot"
        self.write("references.json", [bad])
        self.write("evidence.json", [evidence()])
        errors = validator.validate_phase(self.run, "research")
        self.assertTrue(any("source_url" in error for error in errors))

    def test_directions_require_five_and_three_axis_pairwise_difference(self):
        self.write("evidence.json", [evidence()])
        directions = [direction(f"d-{index}") for index in range(5)]
        self.write("directions.json", directions)
        errors = validator.validate_phase(self.run, "directions")
        self.assertTrue(any("fewer than three axes" in error for error in errors))

    def test_distinct_evidence_linked_directions_pass(self):
        self.write("evidence.json", [evidence()])
        directions = [
            direction("editorial"),
            direction("dense", layout="grid", density="dense", typography="serif"),
            direction("visual", layout="split", palette="vivid", imagery="photo"),
            direction("calm", typography="rounded", density="spacious", interaction="guided"),
            direction("dark", layout="cards", palette="dark", imagery="illustration", interaction="direct"),
        ]
        self.write("directions.json", directions)
        self.assertEqual(validator.validate_phase(self.run, "directions"), [])

    def test_mockups_cover_every_approved_direction(self):
        self.write("run.json", {"approved_direction_ids": ["a", "b"]})
        self.write(
            "mockup-manifest.json",
            {
                "mockups": [
                    {
                        "direction_id": "a",
                        "status": "success",
                        "viewport": "390x844",
                        "prompt_digest": "sha256:abc",
                        "output_ref": "mockups/a.png",
                    }
                ]
            },
        )
        errors = validator.validate_phase(self.run, "mockups")
        self.assertEqual(errors, ["missing successful mockups for: b"])

    def test_implementation_matches_selection_and_records_render_checks(self):
        self.write("run.json", {"selected_direction_id": "a"})
        self.write(
            "implementation.json",
            {
                "selected_direction_id": "b",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": {"rendered_viewports": [], "checks": {}},
            },
        )
        errors = validator.validate_phase(self.run, "implementation")
        self.assertTrue(any("selected direction" in error for error in errors))
        self.assertTrue(any("rendered_viewports" in error for error in errors))

        self.write(
            "implementation.json",
            {
                "selected_direction_id": "a",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": {
                    "rendered_viewports": ["390x844"],
                    "checks": {
                        "content": "pass",
                        "overflow": "pass",
                        "accessibility": "pass",
                    },
                },
            },
        )
        self.assertEqual(validator.validate_phase(self.run, "implementation"), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run validator tests and verify RED**

Run:

```bash
PYTHONPATH=tests python3 -m unittest tests.test_validate_run -v
```

Expected: import succeeds against the empty script, then fails because `AXES` or `validate_phase` is missing.

- [ ] **Step 3: Implement the minimal validator**

Implement `design-explorer/scripts/validate_run.py`:

```python
#!/usr/bin/env python3
import argparse
import json
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse


AXES = ("layout", "typography", "palette", "density", "imagery", "interaction")


def read_json(run_dir: Path, name: str, errors: list[str]):
    path = run_dir / name
    if not path.is_file():
        errors.append(f"missing {name}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        errors.append(f"invalid {name}: {error}")
        return None


def valid_url(value) -> bool:
    parsed = urlparse(value if isinstance(value, str) else "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_sources(items, label: str, errors: list[str]) -> None:
    if not isinstance(items, list) or not items:
        errors.append(f"{label} must be a non-empty list")
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        for field in ("id", "title", "source_url", "source_type"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{label}[{index}] missing {field}")
        if not valid_url(item.get("source_url")):
            errors.append(f"{label}[{index}] source_url must be http(s)")


def validate_research(run_dir: Path) -> list[str]:
    errors = []
    references = read_json(run_dir, "references.json", errors)
    evidence = read_json(run_dir, "evidence.json", errors)
    if references is not None:
        validate_sources(references, "references", errors)
        for index, item in enumerate(references if isinstance(references, list) else []):
            if not isinstance(item, dict):
                continue
            for field in ("captured_at", "relevance"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"references[{index}] missing {field}")
            observations = item.get("observations", {})
            missing = [axis for axis in AXES if not observations.get(axis)]
            if missing:
                errors.append(f"references[{index}] missing observations: {', '.join(missing)}")
    if evidence is not None:
        validate_sources(evidence, "evidence", errors)
        for index, item in enumerate(evidence if isinstance(evidence, list) else []):
            if not isinstance(item, dict):
                continue
            if item.get("source_type") not in {"official", "research", "observed"}:
                errors.append(f"evidence[{index}] has unsupported source_type")
            for field in (
                "problem",
                "publisher_or_author",
                "summary",
                "application",
                "limitations",
            ):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"evidence[{index}] missing {field}")
    return errors


def validate_directions(run_dir: Path) -> list[str]:
    errors = []
    evidence = read_json(run_dir, "evidence.json", errors)
    directions = read_json(run_dir, "directions.json", errors)
    evidence_ids = {item.get("id") for item in evidence or [] if isinstance(item, dict)}
    if not isinstance(directions, list) or len(directions) < 5:
        errors.append("directions must contain at least five items")
        return errors
    ids = set()
    for index, item in enumerate(directions):
        if not isinstance(item, dict):
            errors.append(f"directions[{index}] must be an object")
            continue
        for field in (
            "id",
            "name",
            "concept",
            "ux_problem",
            "evidence_application",
            "tradeoffs",
            "implementation_difficulty",
            "implementation_risks",
        ):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"directions[{index}] missing {field}")
        identifier = item.get("id")
        if identifier in ids:
            errors.append(f"duplicate direction id: {identifier}")
        ids.add(identifier)
        axes = item.get("axes", {})
        missing_axes = [axis for axis in AXES if not axes.get(axis)]
        if missing_axes:
            errors.append(f"directions[{index}] missing axes: {', '.join(missing_axes)}")
        links = item.get("evidence_ids", [])
        if not links or set(links) - evidence_ids:
            errors.append(f"directions[{index}] has missing or unknown evidence_ids")
    valid_directions = [item for item in directions if isinstance(item, dict)]
    for left, right in combinations(valid_directions, 2):
        difference = sum(left.get("axes", {}).get(axis) != right.get("axes", {}).get(axis) for axis in AXES)
        if difference < 3:
            errors.append(f"{left.get('id')} and {right.get('id')} differ on fewer than three axes")
    return errors


def validate_mockups(run_dir: Path) -> list[str]:
    errors = []
    run = read_json(run_dir, "run.json", errors)
    manifest = read_json(run_dir, "mockup-manifest.json", errors)
    if run is None or manifest is None:
        return errors
    if not isinstance(run, dict):
        errors.append("run.json must be an object")
        return errors
    if not isinstance(manifest, dict) or not isinstance(manifest.get("mockups"), list):
        errors.append("mockup-manifest.json must contain a mockups list")
        return errors
    successful = set()
    for index, item in enumerate(manifest["mockups"]):
        if not isinstance(item, dict):
            errors.append(f"mockups[{index}] must be an object")
            continue
        if item.get("status") == "success":
            successful.add(item.get("direction_id"))
            for field in ("viewport", "prompt_digest", "output_ref"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"mockups[{index}] missing {field}")
    missing = set(run.get("approved_direction_ids", [])) - successful
    if missing:
        errors.append(f"missing successful mockups for: {', '.join(sorted(missing))}")
    return errors


def validate_implementation(run_dir: Path) -> list[str]:
    errors = []
    run = read_json(run_dir, "run.json", errors)
    implementation = read_json(run_dir, "implementation.json", errors)
    if run is None or implementation is None:
        return errors
    if not isinstance(run, dict) or not isinstance(implementation, dict):
        errors.append("run.json and implementation.json must be objects")
        return errors
    if implementation.get("selected_direction_id") != run.get("selected_direction_id"):
        errors.append("implementation selected direction does not match run.json")
    if implementation.get("mode") not in {"project", "standalone"}:
        errors.append("implementation mode must be project or standalone")
    if not isinstance(implementation.get("preview_path"), str) or not implementation["preview_path"].strip():
        errors.append("implementation preview_path is required")
    verification = implementation.get("verification", {})
    if not verification.get("rendered_viewports"):
        errors.append("implementation rendered_viewports is required")
    checks = verification.get("checks", {})
    for name in ("content", "overflow", "accessibility"):
        if checks.get(name) != "pass":
            errors.append(f"implementation check must pass: {name}")
    return errors


def validate_phase(run_dir: Path, phase: str) -> list[str]:
    run_dir = Path(run_dir)
    if phase == "research":
        return validate_research(run_dir)
    if phase == "directions":
        return validate_directions(run_dir)
    if phase == "mockups":
        return validate_mockups(run_dir)
    if phase == "implementation":
        return validate_implementation(run_dir)
    if phase == "all":
        return (
            validate_research(run_dir)
            + validate_directions(run_dir)
            + validate_mockups(run_dir)
            + validate_implementation(run_dir)
        )
    raise ValueError(f"unknown phase: {phase}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("research", "directions", "mockups", "implementation", "all"),
    )
    args = parser.parse_args()
    errors = validate_phase(Path(args.run), args.phase)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"{args.phase} artifacts are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run validator and full unit tests**

Run:

```bash
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all state, validator, and discovery tests pass.

- [ ] **Step 5: Commit validation**

```bash
git add design-explorer/scripts/validate_run.py tests/test_validate_run.py
git commit -m "feat: validate design evidence and direction diversity"
```

---

### Task 5: Write the Evidence-First Skill Workflow

**Files:**
- Modify: `design-explorer/SKILL.md`
- Modify: `design-explorer/references/artifact-contracts.md`
- Modify: `design-explorer/references/research-evidence.md`
- Modify: `design-explorer/references/mockups-implementation.md`
- Modify: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes: Task 1 failures, Task 3 state CLI, and Task 4 validator CLI.
- Produces: Complete agent instructions for research, approval, mockups, preview implementation, and verification.

- [ ] **Step 1: Strengthen the failing Skill contract test**

Add this test method to `SkillContractTests`:

```python
    def test_skill_contains_required_gates_and_resource_routing(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        required = (
            "directions_pending_approval",
            "directions_approved",
            "Do not call image generation",
            "at least five",
            "three axes",
            "explicit approval",
            "isolated preview",
            "references/research-evidence.md",
            "references/mockups-implementation.md",
            "references/artifact-contracts.md",
        )
        for phrase in required:
            self.assertIn(phrase, text)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_skill_contract.SkillContractTests.test_skill_contains_required_gates_and_resource_routing -v
```

Expected: failure because the minimal Skill body lacks the required gate wording.

- [ ] **Step 3: Write `artifact-contracts.md`**

Document the run directory, state sequence, exact `run_state.py` commands, and structured file fields. Require `references.json`, `evidence.json`, and `directions.json` as machine-readable sources of truth, while `reference-board.md`, `design-evidence.md`, and `mood-directions.md` remain user-facing views.

Define these required fields exactly:

- reference: `id`, `title`, `source_url`, `source_type`, `captured_at`, `relevance`, and all six `observations` axes; `capture_path` is optional;
- evidence: `id`, `problem`, `title`, `publisher_or_author`, `source_url`, `source_type`, `summary`, `application`, and `limitations`; `published_or_updated_at` is optional when unavailable;
- direction: `id`, `name`, `concept`, `ux_problem`, `evidence_ids`, `evidence_application`, all six `axes`, `tradeoffs`, `implementation_difficulty`, and `implementation_risks`;
- successful mockup: `direction_id`, `status`, `viewport`, `prompt_digest`, and `output_ref`;
- implementation: `selected_direction_id`, `mode`, `preview_path`, and `verification` containing non-empty `rendered_viewports` plus passing `content`, `overflow`, and `accessibility` checks.

Include the exact commands:

```bash
python3 scripts/run_state.py init --slug <lowercase-hyphen-slug> --project-path <absolute-path>
python3 scripts/validate_run.py --run <run-dir> --phase research
python3 scripts/run_state.py transition --run <run-dir> --to research_complete
python3 scripts/validate_run.py --run <run-dir> --phase directions
python3 scripts/run_state.py transition --run <run-dir> --to directions_pending_approval
python3 scripts/run_state.py transition --run <run-dir> --to directions_approved --approved-direction <id>
python3 scripts/validate_run.py --run <run-dir> --phase mockups
python3 scripts/validate_run.py --run <run-dir> --phase implementation
```

State explicitly that credentials, cookies, API keys, and pairing tokens never belong in run artifacts.

- [ ] **Step 4: Write `research-evidence.md`**

Define this positive output recipe in order:

1. Capture the screen purpose, required content, viewport, preservation constraints, and implementation context in `brief.md`.
2. Search one target screen/pattern at a time using normal web search; use `chrome:control-chrome` only when signed-in Chrome state or visual inspection is needed.
3. Record direct URLs and observations for layout, typography, palette, density, imagery, and interaction in `references.json`.
4. Research official accessibility/platform guidance first, then only task-relevant credible research, then observed product patterns.
5. Record evidence and limitations in `evidence.json`; write a user-facing `design-evidence.md` that separates official guidance, research, observed patterns, and agent inference.
6. Create at least five directions. Every direction names the UX problem, linked evidence, application, six design axes, implementation difficulty, and trade-offs.
7. Run the research and directions validators before presenting the reference board, evidence summary, and mood directions.

Add explicit recovery rules for blocked login/CAPTCHA, weak or conflicting evidence, broken links, and unverifiable claims. Require pausing for manual login rather than bypassing access controls.

- [ ] **Step 5: Write `mockups-implementation.md`**

Define these rules:

- Read `run.json`; image generation is allowed only at `directions_approved`.
- Use the same content and viewport for every approved direction.
- Generate one full-screen UI mockup per approved direction in a single host-supported batch when possible.
- Before the image call, write pending entries to `mockup-manifest.json`; after the host returns artifacts, record each output path or provider artifact hint and status.
- Warn before sending user-provided or internal screenshots to an external image-generation provider when they may contain sensitive information.
- Follow the host's image-generation response rule and end the turn immediately after emitting generated images. Resume manifest/state work on the next user turn.
- Permit one technical retry per failed direction. Ask before additional variations or a larger budget.
- After selection, inspect the active repository's package manifests, routes, components, tokens, and dirty state.
- Add an isolated route, screen, story, or component; never overwrite production during exploration.
- With no suitable project, create a standalone Vite React TypeScript preview.
- Render at target viewports, capture screenshots, check content, hierarchy, overflow, responsiveness, and accessibility, and run relevant lint/typecheck/tests.
- Record the selected direction, preview path, rendered viewports, and content/overflow/accessibility check results in `implementation.json`; validate it before `prototype_ready`.
- Integrate into production only after explicit preview approval.

- [ ] **Step 6: Replace `SKILL.md` with the compact orchestrator**

Use this structure and wording, incorporating only baseline-specific counters observed in Task 1:

```markdown
---
name: design-explorer
description: Use when improving or redesigning a web or mobile interface, comparing multiple visual directions, researching UI references, generating UI mockups, or turning a selected direction into code.
---

# Design Explorer

Build evidence-backed interface directions, then implement only the direction the user selects.

## Core workflow

1. Create or resume a run with `scripts/run_state.py`. Read `references/artifact-contracts.md`.
2. Normalize the request, images, current screen, viewport, and project constraints into `brief.md`.
3. Read `references/research-evidence.md`; collect traceable visual references and problem-relevant UX evidence.
4. Produce at least five directions. Every pair must differ materially on at least three axes: layout, typography, palette, density, imagery, interaction.
5. Validate research and directions. Present the reference board, evidence summary, and directions together.
6. Transition to `directions_pending_approval` and stop for explicit approval. Do not infer approval from enthusiasm, deadlines, prior work, or sunk cost.
7. Record approved IDs and transition to `directions_approved`.
8. Read `references/mockups-implementation.md`; generate comparable full-screen UI mockups only for approved directions.
9. Record outputs, validate coverage, and ask the user to select or combine a direction.
10. Implement an isolated preview using the active project's stack, or the standalone React fallback. Verify its rendered result before offering production integration.

## Hard gates

- Do not call image generation unless `run.json` is at `directions_approved`.
- Do not present color-only variations as distinct directions; the validator enforces three axes.
- Do not fabricate citations or hide conflicting evidence. Separate sources from inference.
- Do not copy one reference pixel-for-pixel. Synthesize principles from multiple sources.
- Do not overwrite a production screen during exploration. Build and verify an isolated preview first.
- Do not exceed five initial images or one technical retry per failed direction without user approval.

Run `scripts/validate_run.py` before every state transition that consumes research, directions, mockups, or implementation output. Preserve unrelated user changes and retain direct source URLs in all user-facing research summaries.

## Quick reference

| Saved state | Allowed next action |
|---|---|
| `directions_pending_approval` | Present evidence and request approval |
| `directions_approved` | Generate approved mockups within budget |
| `mockups_generated` | Ask the user to select or combine a direction |
| `implementation_selected` | Build an isolated preview |

## Common mistakes

- Generating images from an enthusiastic reply without recording approved IDs.
- Treating five palettes on one layout as five directions.
- Citing a search-result thumbnail instead of the direct source.
- Editing the production screen before preview approval.
```

- [ ] **Step 7: Run all tests and validation**

Run:

```bash
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 /Users/donggyunyang/.codex/skills/.system/skill-creator/scripts/quick_validate.py design-explorer
wc -l design-explorer/SKILL.md
```

Expected: all unit tests pass, quick validation succeeds, and `SKILL.md` stays below 500 lines.

- [ ] **Step 8: Commit the complete workflow**

```bash
git add design-explorer tests/test_skill_contract.py
git commit -m "feat: add evidence-first design exploration workflow"
```

---

### Task 6: Forward-Test and Refine the Skill

**Files:**
- Create: `tests/forward-results.md`
- Modify when evidence requires: `design-explorer/SKILL.md`
- Modify when evidence requires: `design-explorer/references/*.md`
- Modify when evidence requires: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes: The exact five scenarios from Task 1 and the local Skill at `/Users/donggyunyang/Desktop/design-explorer-skill/design-explorer`.
- Produces: Verbatim GREEN results, a five-repetition approval-gate wording test, and focused changes tied to observed failures.

- [ ] **Step 1: Run fresh agents with the Skill and repeat the gate wording five times**

Dispatch one fresh agent for Scenarios 1, 2, 3, and 5, then dispatch five fresh agents for Scenario 4 to compare with its five no-guidance controls. Use `fork_turns="none"`. Prefix the scenario with:

```text
Use $design-explorer at /Users/donggyunyang/Desktop/design-explorer-skill/design-explorer for this task. Do not use Lazyweb. This is a real task; take the next concrete action allowed by the skill. Do not call paid or external image-generation tools during this validation.
```

Expected: each agent follows its invariant, cites the relevant gate or recipe, and avoids the prohibited action under pressure. The five Scenario 4 responses converge on explicit approval rather than producing materially different interpretations.

- [ ] **Step 2: Record GREEN results verbatim**

Create `tests/forward-results.md` with the exact response for each scenario, a pass/fail decision against its invariant, new rationalizations, and any ambiguous wording exposed by the agent.

- [ ] **Step 3: Refactor only evidenced gaps**

For every failed scenario, use the failure-shape rule:

- skipped rule under pressure → add an explicit counter and contract test;
- omitted field → add it to the required output recipe and validator;
- wrong output shape → replace prohibitions with an ordered positive recipe;
- conditional mistake → key the instruction to an observable `run.json` state.

Do not add speculative guidance unsupported by a baseline or forward-test failure.

- [ ] **Step 4: Re-run failed scenarios and all deterministic tests**

Re-dispatch only failed scenarios in fresh contexts, then run:

```bash
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 /Users/donggyunyang/.codex/skills/.system/skill-creator/scripts/quick_validate.py design-explorer
```

Expected: all scenarios pass, unit tests pass, and quick validation succeeds.

- [ ] **Step 5: Commit forward-tested refinements**

```bash
git add design-explorer tests/forward-results.md tests/test_skill_contract.py
git commit -m "test: forward-validate design explorer workflow"
```

---

### Task 7: Install and Run Acceptance Verification

**Files:**
- Copy after verification: `design-explorer/` to `~/.codex/skills/design-explorer/`

**Interfaces:**
- Consumes: A clean, validated local skill package.
- Produces: An auto-discoverable personal Codex/Orca skill and final verification evidence.

- [ ] **Step 1: Verify the complete repository**

Run:

```bash
git status --short
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 /Users/donggyunyang/.codex/skills/.system/skill-creator/scripts/quick_validate.py design-explorer
rg -n 'T''BD|T''ODO|PLACE''HOLDER|FIX''ME' design-explorer tests || true
```

Expected: clean Git status before verification changes, all tests pass, quick validation succeeds, and the placeholder scan returns no matches.

- [ ] **Step 2: Check the installation target safely**

Run:

```bash
if [ -e "$HOME/.codex/skills/design-explorer" ]; then
  printf '%s\n' 'Target already exists; stop and inspect before installation.'
  exit 1
fi
```

Expected: no output and exit code 0. If the target exists, stop rather than overwriting it.

- [ ] **Step 3: Install the validated Skill**

Run:

```bash
cp -R design-explorer "$HOME/.codex/skills/design-explorer"
python3 /Users/donggyunyang/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$HOME/.codex/skills/design-explorer"
```

Expected: the installed copy validates successfully.

- [ ] **Step 4: Run a zero-cost acceptance scenario**

In a fresh Codex/Orca conversation, invoke:

```text
Use $design-explorer to improve a mobile sign-up screen. Do not generate images yet. Research traceable references and UX evidence, then present five meaningfully different directions for approval.
```

Expected:

- a run directory is created;
- `brief.md`, research artifacts, evidence artifacts, and five validated directions exist;
- every web-derived claim has a direct URL;
- the run stops at `directions_pending_approval`;
- no image-generation call occurs;
- no production project file is modified.

- [ ] **Step 5: Record final repository state**

Run:

```bash
git status --short
git log --oneline --decorate -7
```

Expected: only intentionally recorded validation-result edits, if any, are present. Commit such evidence with:

```bash
git add tests
git commit -m "test: verify installed design explorer skill"
```

If no files changed, do not create an empty commit.

---

## Completion Checklist

- [ ] RED behavior was observed without the Skill and recorded verbatim.
- [ ] The official skill scaffold generated the package.
- [ ] State and artifact helpers were implemented test-first.
- [ ] The approval gate is enforced both by instructions and persisted state.
- [ ] Evidence provenance, five-direction minimum, and three-axis diversity are deterministic checks.
- [ ] The Skill was forward-tested in fresh contexts and refined only from observed gaps.
- [ ] The local and installed skill directories both pass `quick_validate.py`.
- [ ] The zero-cost acceptance run stops before image generation.
- [ ] The installed Skill is available at `~/.codex/skills/design-explorer`.
