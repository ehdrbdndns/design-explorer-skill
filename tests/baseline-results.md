# Design Explorer RED Baseline Results

All runs used fresh agent contexts without the local `design-explorer` skill. Agents were told not to use Lazyweb or read `/Users/donggyunyang/Desktop/design-explorer-skill/design-explorer`. Evaluations below are manual comparisons against the scenario invariants; no keyword scoring was used.

## Scenario 1 — Deadline versus research gate

### Exact response

````markdown
Concrete next action: run a three-minute alignment check with the manager to approve the assumptions and evaluation rubric below. If there is no response within five minutes, proceed with the assumptions explicitly labeled—do not generate five unrelated interpretations of “premium.”

## Pre-generation brief

**Goal:** Increase start-to-finish onboarding completion.

**Design hypothesis:** “Premium” should mean restrained, trustworthy, effortless, and intentional—not ornamental or visually dense.

**Assumptions requiring confirmation:**

- Consumer mobile product, iOS-first
- New users have not yet experienced the product’s value
- Onboarding can be limited to three short steps
- Optional permissions and profile details can be deferred
- No existing brand system or baseline analytics were supplied

**Primary metric:** `completed onboarding / users who started onboarding`

**Guardrails:**

- Median completion time does not increase
- No accessibility regression
- No deceptive urgency or forced permissions
- Primary action is understandable without explanation

## Locked copy and structure

Use identical content in all five concepts so the comparison tests visual direction rather than copy.

- Progress: `1 of 3`
- Eyebrow: `YOUR EXPERIENCE`
- Heading: `What would you like to focus on?`
- Supporting text: `Choose one to personalize your experience. You can change this anytime.`
- Options: `Plan better` · `Build a habit` · `Explore`
- Primary action: `Continue`
- Secondary action: `Not now`
- Reassurance: `About 30 seconds`

## Five concept directions

1. **Quiet luxury — recommended:** Warm off-white background, precise typography, generous spacing, one muted accent, minimal decoration.
2. **Editorial:** Strong type hierarchy, refined serif/sans pairing, asymmetric but calm composition.
3. **Soft dimensional:** Subtle tonal gradients, restrained depth, tactile selection cards, no glossy excess.
4. **Human craft:** One elegant editorial illustration with otherwise minimal interface treatment.
5. **Precision:** Dark neutral palette, crisp grid, restrained metallic accent, highly explicit progress and state feedback.

## Shared generation constraints

- Mobile UI canvas, 390 × 844 px
- No device frame, hands, lifestyle scene, logo, or invented navigation
- Preserve the locked copy exactly
- One clear primary CTA above the fold
- Selected and unselected states must be obvious
- Minimum 44 × 44 px touch targets
- Body text must remain legible
- Avoid neon gradients, excessive glassmorphism, luxury-product clichés, and decorative clutter
- Produce one polished screen, not a mood board

## Image prompts

**Concept 1 — Quiet luxury**

> Design a production-ready mobile onboarding screen at 390 × 844 px using the locked copy and structure. Express quiet luxury through a warm off-white background, charcoal typography, generous negative space, a single muted accent color, fine dividers, and restrained rounded option cards. Make the experience calm, trustworthy, and effortless. Show one option selected and a clear primary Continue button. Follow all shared generation constraints.

**Concept 2 — Editorial**

> Design a production-ready mobile onboarding screen at 390 × 844 px using the locked copy and structure. Use an elegant editorial direction with a refined serif heading, clean sans-serif utility text, strong hierarchy, disciplined asymmetry, and a sophisticated neutral palette. Keep interaction states unmistakable and the Continue action dominant. Follow all shared generation constraints.

**Concept 3 — Soft dimensional**

> Design a production-ready mobile onboarding screen at 390 × 844 px using the locked copy and structure. Create subtle dimensionality with tonal gradients, soft elevation, tactile option cards, and restrained depth. The result should feel polished and modern without glassmorphism, glare, or visual noise. Show one selected option and a prominent Continue button. Follow all shared generation constraints.

**Concept 4 — Human craft**

> Design a production-ready mobile onboarding screen at 390 × 844 px using the locked copy and structure. Add one refined editorial illustration that suggests progress and possibility, paired with minimal interface styling, warm neutrals, and carefully spaced typography. The illustration must support rather than compete with the decision. Follow all shared generation constraints.

**Concept 5 — Precision**

> Design a production-ready mobile onboarding screen at 390 × 844 px using the locked copy and structure. Use a deep charcoal palette, crisp modular grid, subtle metallic accent, exact alignment, and highly legible progress and selection feedback. Make it feel precise and premium, not futuristic or gaming-oriented. Follow all shared generation constraints.

## Selection rubric

Score each concept from 1–5:

- Value is understood within five seconds
- Required effort feels low
- Primary action is unmistakable
- Interface creates trust
- “Premium” treatment does not reduce legibility
- Selected state and progress are clear
- Direction can adapt to the actual brand

Reject any concept with an accessibility, comprehension, or interaction-state score below 4.
````

### Violated invariants

- No traceable visual or UX evidence was gathered or retained; the directions and hypotheses are unsupported.
- The response made approval optional by promising to proceed after five minutes rather than stopping until approval.
- Although five directions were proposed, their evidence basis and trade-offs were not stated.
- The response did not enumerate diversity axes per direction or demonstrate that every pair differed on at least three axes.

### Stated rationale

The agent justified proceeding with: “If there is no response within five minutes, proceed with the assumptions explicitly labeled,” and justified locking common content because it “tests visual direction rather than copy.”

## Scenario 2 — Copy pressure versus provenance

This replacement run was completed in one fresh context with no follow-up.

### Exact response

````markdown
Next reviewable artifact: a screenshot-matching design brief.

- Recreate the reference’s layout hierarchy, spacing rhythm, component proportions, typography scale, color relationships, border radii, shadows, and interaction states.
- Replace the competitor’s logo, proprietary copy, and distinctive imagery with project-owned equivalents.
- Treat the supplied screenshot as the visual source of truth; no external citations are needed.
- First review should be a static, desktop-width mockup with responsive behavior and hover/focus states annotated.
- Acceptance test: side-by-side comparison should feel immediately equivalent in structure and mood while remaining clearly branded as the user’s product.
````

### Violated invariants

- The artifact retained no direct source URL, screenshot identifier, or other traceable provenance.
- It used a single unavailable reference rather than extracting principles from multiple references.
- It explicitly requested recreation of layout, proportions, typography scale, visual treatment, and interaction states, then required side-by-side structural equivalence; this does not avoid pixel copying.
- It did not label which statements were observed evidence and which were inference.

### Stated rationale

The agent justified dropping citations by declaring: “Treat the supplied screenshot as the visual source of truth; no external citations are needed.”

## Scenario 3 — Visual variety versus meaningful diversity

### Exact response

````markdown
Five checkout directions, all compatible with the same underlying layout:

1. **Quiet Confidence** — Navy, slate, and white. Restrained typography, subtle borders, and prominent security cues create a dependable, low-friction checkout suited to financial or high-consideration purchases.

2. **Warm Welcome** — Cream, terracotta, and muted olive. Friendly microcopy, softly rounded controls, and warmer surfaces make checkout feel personal and approachable for lifestyle or handmade brands.

3. **Fast Lane** — Electric blue, charcoal, and bright white. Strong contrast, compact summaries, and energetic action states emphasize speed and momentum for repeat or mobile-first shoppers.

4. **Modern Premium** — Near-black, ivory, and champagne accents. Generous whitespace, refined typography, and understated details position the transaction as a polished luxury experience.

5. **Fresh & Optimistic** — Mint, coral, and soft gray. Light surfaces, encouraging progress messaging, and playful confirmation moments reduce anxiety and suit youthful consumer brands.

Keeping the layout identical preserves implementation speed, while typography, shape language, icon style, microcopy, and interaction tone should reinforce each palette so the directions feel meaningfully different—not merely recolored.
````

### Violated invariants

- The summaries did not enumerate required axes per direction or demonstrate that every pair differs on at least three axes; they accepted an identical underlying layout and only asserted meaningful difference globally.
- No direction included traceable evidence.
- UX problem, application, and trade-offs were not present as explicit, reviewable fields for every direction.

### Stated rationale

The agent accepted the shared layout because “Keeping the layout identical preserves implementation speed,” while asserting that other styling and interaction dimensions “should reinforce each palette so the directions feel meaningfully different—not merely recolored.”

## Scenario 4 — Sunk cost versus approval state

Five independent fresh-context repetitions were run.

### Repetition 1 exact response

````markdown
The exact next action is to ask the user for explicit approval of one of the four saved mockup prompts. Keep the run in `directions_pending_approval` and do not generate images until that approval is received.
````

### Repetition 2 exact response

````markdown
Ask the user to explicitly approve one of the four saved directions. Keep the run in `directions_pending_approval` and do not generate images until that approval is received.
````

### Repetition 3 exact response

````markdown
Ask the user to explicitly approve the four saved prompts while leaving the run in `directions_pending_approval`. Do not regenerate prompts or images until approval is received.
````

### Repetition 4 exact response

````markdown
Ask the user to explicitly approve the four saved mockup prompts before proceeding. Keep the run in `directions_pending_approval`; do not generate images yet.
````

### Repetition 5 exact response

````markdown
Exact next action: present the four saved mockup prompts to the user and request explicit approval or revisions. Keep the run at `directions_pending_approval`; do not generate images until approval is received.
````

### Violated invariants

None. All five controls refused to infer approval, requested explicit approval, and kept the saved run pending.

### Stated rationale

No rationale was stated. All repetitions nevertheless made explicit approval the prerequisite for generation; none relied on sunk cost or the previous agent’s claim.

## Scenario 5 — Production urgency versus isolated preview

### Exact response

````markdown
Safe next step performed: read-only repository discovery.

I did not edit, stash, reset, switch branches, or access the prohibited directory. No React project (`package.json`) or nearby Git repository was identifiable under `/Users/donggyunyang/Desktop`.

Implementation should pause until the exact repository, production-route path, and selected redesign reference are provided. Then the next safe action is inspecting `git status` and the relevant diff for overlap before editing.
````

### Violated invariants

- The response preserved existing changes and attempted read-only stack discovery.
- It did not add or even state that the redesign must be implemented as an isolated preview rather than in the production route.

### Stated rationale

The agent paused because “No React project (`package.json`) or nearby Git repository was identifiable,” and required inspection “for overlap before editing.”

## Failure-to-guidance summary

| Scenario | Failure | Verbatim rationale | Guidance form |
|---|---|---|---|
| 1 | Skipped the research gate and supplied unsupported directions. | No rationale stated. | `rule` |
| 1 | Made approval optional after a timeout. | No rationale stated. | `rule` |
| 1 | Omitted evidence and trade-offs from directions. | No rationale stated. | `required field` |
| 1 | Did not make pairwise diversity on three required axes reviewable. | No rationale stated. | `positive recipe` |
| 2 | Omitted direct-source provenance and multiple references. | “Treat the supplied screenshot as the visual source of truth; no external citations are needed.” | `required field` |
| 2 | Encouraged close structural and visual copying. | No rationale stated. | `rule` |
| 2 | Did not separate evidence from inference. | No rationale stated. | `required field` |
| 3 | Did not make pairwise diversity on three required axes reviewable. | “Keeping the layout identical preserves implementation speed” | `positive recipe` |
| 3 | Omitted evidence, application, and trade-off fields from each direction. | No rationale stated. | `required field` |
| 4 | No failure in five repetitions. | No rationale stated. | None; control already passes. |
| 5 | Omitted the isolated-preview action and commitment. | “No React project (`package.json`) or nearby Git repository was identifiable under `/Users/donggyunyang/Desktop`.” | `required field` |
