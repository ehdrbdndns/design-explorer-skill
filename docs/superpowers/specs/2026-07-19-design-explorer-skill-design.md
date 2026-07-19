# Design Explorer Skill — Design Specification

Date: 2026-07-19

Status: Approved in conversation

Working skill name: `design-explorer`

## 1. Purpose

Create an internal Codex/Orca skill that helps a user improve a web or mobile interface by researching real references, proposing at least five meaningfully different visual directions, generating full-screen UI mockup images after approval, and implementing the selected direction as working code.

The skill is a clean-room alternative inspired by the useful workflow of design-research products. It must not extract private prompts, bypass paid access, copy a proprietary screenshot database, or reproduce a third-party product's exact implementation.

## 2. Goals

- Run entirely through a Codex/Orca conversation.
- Accept a text brief, user-provided images, an existing project screen, or a combination of these inputs.
- Research references using normal web search and, when useful, the user's existing Chrome session for sites such as Google Images or Pinterest.
- Show visual references, UX evidence, and proposed mood directions before spending resources on image generation.
- Propose at least five directions that differ in layout and visual language, not merely color.
- Generate a full-page or full-mobile-screen static UI mockup for every approved direction.
- Turn a selected direction into working code that follows the active project's stack and design system.
- Fall back to a standalone React prototype when there is no suitable project.
- Preserve provenance, intermediate artifacts, and resumable state.
- Minimize cost through approval gates, caching, and bounded retries.

## 3. Non-goals

- Building a public SaaS product, billing system, account system, or multi-tenant backend.
- Maintaining a crawler or a proprietary, centralized screenshot database.
- Bulk downloading, republishing, or reselling copyrighted reference images.
- Reproducing a named product's screen pixel-for-pixel.
- Editing the user's production screen before a preview has been reviewed.
- Generating final production code for all five directions by default.

## 4. Primary User Experience

The normal workflow is a stateful sequence:

1. **Brief** — understand the target screen, product goal, platform, viewport, constraints, and what must be preserved.
2. **Research** — collect relevant visual references, official UX/accessibility guidance, and task-relevant research with source URLs.
3. **Direction proposal** — present a reference board, an evidence summary, and at least five distinct mood directions.
4. **Approval gate 1** — allow the user to approve, reject, edit, or combine directions. No image generation occurs before this gate passes.
5. **Mockup generation** — generate one comparable, full-screen UI mockup image per approved direction.
6. **Selection gate 2** — allow the user to select a direction, request bounded variations, or combine chosen traits.
7. **Prototype implementation** — create a safe preview using the active project's conventions, or a standalone React prototype.
8. **Render verification** — run the preview, capture it at target viewports, compare it with the selected direction, and correct material differences.
9. **Optional integration** — modify the real product screen only when the user explicitly requests integration.

## 5. Architecture

### 5.1 Skill orchestrator

`SKILL.md` defines triggers, workflow states, approval gates, tool routing, artifact contracts, and safety rules. It should stay concise and delegate mechanical work to focused scripts or referenced instructions.

### 5.2 Input normalizer

The input normalizer creates a consistent brief from:

- natural-language requests;
- user-provided screenshots or reference images;
- screenshots captured from a running local application;
- the active repository's existing screen and design system;
- explicit product, platform, viewport, and implementation constraints.

The resulting `brief.md` records the target, audience, job-to-be-done, required content, preservation constraints, desired change, viewport, and technical context. If a missing answer would materially change the design, the skill asks one concise question at a time.

### 5.3 Reference researcher

The researcher uses ordinary web search first and Chrome when a signed-in or visually inspected session is needed. It may capture visible results for internal analysis, but it does not run a bulk scraper or evade access controls.

Each shortlisted reference records:

- source URL;
- page or product name;
- source type;
- capture time;
- local capture path when a capture is permitted;
- layout, typography, color, density, imagery, and interaction observations;
- the reason it is relevant to the target screen.

The researcher must report low coverage honestly and broaden the search rather than invent evidence.

### 5.4 Design evidence researcher

The evidence researcher prevents the visual search from becoming style-only imitation. It creates `design-evidence.md` using three evidence layers:

1. **Official baselines** — applicable accessibility standards and platform guidance, such as W3C accessibility guidance and the target platform's official interface guidelines.
2. **Research and established UX principles** — peer-reviewed or otherwise credible work relevant to the screen's actual problem, such as readability, cognitive load, choice architecture, error prevention, trust, or task completion.
3. **Observed product patterns** — current real-product examples that show how comparable interfaces apply or intentionally depart from those principles.

The researcher must distinguish evidence from interpretation. It must not invent papers, authors, findings, quotations, or source links. Research is problem-driven rather than exhaustive: it investigates only principles that can materially affect the target screen.

Every evidence entry includes:

- the UX problem or decision it informs;
- source title, publisher or author, and direct URL;
- source type and publication/update date when available;
- a concise paraphrase of the relevant finding or guideline;
- the proposed application to the target interface;
- limitations, uncertainty, or trade-offs.

Official accessibility and platform constraints form a common usability baseline for all directions. Mood exploration may vary visual expression and interaction character, but it may not silently violate that baseline. A deliberate exception must be identified, justified, and approved by the user.

### 5.5 Reference analyzer and mood director

The analyzer converts visual references into reusable design traits rather than copying pixels. The mood director then proposes at least five named directions using the same product requirements.

Every direction includes:

- a name and one-sentence concept;
- a product/design hypothesis;
- layout and hierarchy rules;
- typography direction;
- palette and contrast direction;
- image or illustration treatment;
- density and spacing rules;
- interaction and motion character;
- reference provenance;
- the UX problem it is intended to improve;
- applicable evidence and how the direction applies it;
- usability, accessibility, and aesthetic trade-offs;
- implementation difficulty and risks.

For diversity, every pair of directions must differ materially on at least three of these axes: layout, typography, palette, density, imagery, interaction. Directions that only recolor the same composition are invalid and must be revised before presentation.

### 5.6 Approval gate controller

The run manifest records the current state and approved direction IDs. The first approval view includes the visual reference board, `design-evidence.md`, and the proposed directions. Image-generation tools are unavailable to the workflow until `directions_approved` is recorded. Rejected directions are not generated. Users may combine explicit traits from multiple directions into a new approved direction.

### 5.7 UI mockup generator

The generator produces a static image that looks like a complete web page or mobile screen, not merely a decorative asset. All directions use the same required content, viewport, and comparable presentation.

The generator receives a structured design description. Third-party reference images are represented primarily through analyzed traits and source attribution, not supplied wholesale as an instruction to copy. User-owned images may be used directly when requested. If an internal screenshot may contain sensitive information, the skill warns before sending it to an external image-generation provider.

The default generation budget is one image for each of five approved directions. Automatic retries are bounded to one retry for a technical failure and must not silently create extra variations. Further variations require a user request and are normally limited to selected directions.

### 5.8 Implementation adapter

When an active project exists, the adapter inspects package manifests, framework configuration, existing components, tokens, routes, and styling conventions. It creates an isolated preview route, screen, story, or component consistent with the repository. It does not overwrite the current production screen during exploration.

When no usable project exists, the fallback is a standalone Vite React TypeScript prototype using simple local styling and only necessary dependencies.

The adapter must preserve unrelated user changes. Integration into the real screen is a separate, explicit step after preview approval.

### 5.9 Render verifier

The verifier starts the relevant local application, captures the implemented preview at the target viewport, and compares it with the selected design direction. It checks content completeness, hierarchy, layout, typography, color, responsive behavior, overflow, and basic accessibility. It runs the repository's relevant lint, typecheck, and test commands when available.

## 6. Run State and Artifacts

Research runs are stored outside product repositories by default:

```text
~/.codex/design-explorer/runs/<run-id>/
├── run.json
├── brief.md
├── references.json
├── reference-board.md
├── design-evidence.md
├── captures/
├── mood-directions.md
├── mockup-manifest.json
├── mockups/
└── implementation.json
```

`run.json` contains the workflow state, timestamps, target project path when present, approved directions, selected direction, tool outcomes, and artifact paths. It must not contain browser credentials, cookies, pairing tokens, or model API keys.

Valid states are:

```text
initialized
brief_ready
research_complete
directions_pending_approval
directions_approved
mockups_generated
implementation_selected
prototype_ready
integrated
```

An interrupted run resumes from the last completed state. A state advances only after required artifacts validate successfully.

## 7. Provenance and Copyright Rules

- Every web-derived reference must retain its direct source URL.
- Captures are internal working material, not a distributable screenshot library.
- The tool extracts design principles and combines multiple references rather than cloning one source.
- It does not circumvent login, paywalls, robots protections, CAPTCHA, or rate limits.
- It does not claim ownership of reference material.
- Generated and implemented outputs must avoid copied trademarks, private data, and recognizable proprietary assets unless the user owns or is authorized to use them.

## 8. Failure Handling

- **Chrome login or CAPTCHA:** pause and tell the user what manual action is required; resume afterward.
- **Insufficient references:** mark coverage as low, simplify or broaden the query, and show the closest evidence with a limitation note.
- **Weak or conflicting UX evidence:** label the uncertainty, preserve competing interpretations, and present the decision as a trade-off rather than a fact.
- **Unverifiable research claim:** omit the claim or clearly label it as an inference; never fabricate a citation.
- **Dead source image:** keep the source metadata and analysis, mark the capture unavailable, and continue when enough other evidence exists.
- **Image generation failure:** record the failure, perform at most one automatic retry for a technical error, then ask before spending more.
- **Inconsistent mockup content:** regenerate only the affected direction with the same brief and a corrected constraint.
- **Unknown project stack:** explain the uncertainty and use the standalone React fallback rather than guessing destructive changes.
- **Dirty repository:** preserve all existing changes and add isolated preview files only. If safe isolation is impossible, stop before editing.
- **Preview cannot render:** retain artifacts, report the failing command and error, and do not claim completion.

## 9. Cost Controls

- No image generation before direction approval.
- Default initial generation is five directions with one mockup each.
- No silent extra images or unbounded retries.
- Additional generations are focused on selected directions.
- Reference analysis and approved directions are cached in the run directory.
- Verified UX evidence is cached with its source metadata and can be reused when it remains applicable.
- A resumed run reuses valid artifacts instead of repeating searches or generations.
- The user is told before a requested action materially exceeds the default generation budget.

## 10. Validation Strategy

### 10.1 Skill behavior tests

- The skill triggers for design improvement, multiple visual directions, reference research, and design-to-code requests.
- It accepts text-only, image-only, and mixed inputs.
- It asks only for missing information that would materially alter the result.
- It cannot enter mockup generation without recorded approval.
- It resumes correctly from every persisted workflow state.

### 10.2 Artifact contract tests

- `references.json` entries include valid source URLs and required analysis fields.
- `design-evidence.md` separates official guidance, research, observed patterns, and the skill's own inferences.
- Every evidence-based claim has a traceable source and an explicit application to the target screen.
- `mood-directions.md` contains at least five valid directions by default.
- The diversity check rejects directions that fail the three-axis rule.
- `mockup-manifest.json` maps every generated file to a direction and prompt configuration.
- Run artifacts contain no secrets.

### 10.3 Integration tests

- Browser research succeeds with public search results.
- Relevant official guidance and research are retrieved without fabricated or broken citations.
- A blocked or signed-in browser flow pauses cleanly for user action.
- Approved directions invoke image generation; unapproved directions do not.
- An existing representative React project receives only isolated preview additions.
- A no-project run creates and serves the standalone React fallback.
- The verifier captures desktop and/or mobile target viewports without overflow.

### 10.4 Acceptance criteria

The first release is acceptable when:

1. A user can start with a prompt, an image, or an existing screen.
2. Every presented visual reference and evidence-based UX claim has traceable provenance.
3. All directions satisfy the shared accessibility and platform baseline, or clearly disclose and justify an exception.
4. The skill presents at least five directions that pass the diversity rule.
5. No image-generation call occurs before explicit approval of the references, evidence summary, and directions.
6. Each approved direction receives a comparable full-screen UI mockup.
7. A selected direction becomes a runnable preview without overwriting the production screen.
8. The rendered preview is captured and checked at the target viewport.
9. Interrupted runs resume without repeating valid completed work.
10. Costs remain bounded by the documented defaults unless the user explicitly expands them.

## 11. Delivery Boundary for Version 1

Version 1 delivers the global `design-explorer` skill, its focused helper scripts and schemas, documentation, and tests for the state machine and artifact validation. It uses the tools already available in Codex/Orca for browser control, screenshots, image generation, repository inspection, and local preview verification. It does not add a network service, shared database, crawler, or public web interface.
