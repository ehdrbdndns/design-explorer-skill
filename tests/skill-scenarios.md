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
