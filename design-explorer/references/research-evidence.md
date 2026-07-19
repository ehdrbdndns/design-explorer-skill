# Research and evidence workflow

Use this recipe after initializing a run and before any image generation.

1. Capture the screen purpose, required content, target viewport, preservation constraints, supplied inputs, and implementation context in `brief.md`. Note what must not change and whether screenshots need redaction.
2. Search one target screen or interaction pattern at a time with normal web search. Use `chrome:control-chrome` only when signed-in Chrome state or visual inspection is needed. Never bypass login, CAPTCHA, robots controls, or access restrictions.
3. Open the direct source behind each useful result. Record its direct URL, relevance, capture time, and concrete observations for layout, typography, palette, density, imagery, and interaction in `references.json`. Use `capture_path` only for a safe, sanitized local capture.
4. Research in this order: official accessibility and platform guidance first; task-relevant credible research second; observed product patterns third. Prefer primary sources and current guidance. Scope research to the UX problem rather than collecting generic design inspiration.
5. Record each claim's problem, source, summary, intended application, and limitations in `evidence.json`. Write `design-evidence.md` as a user-facing synthesis with separate sections for official guidance, research, observed patterns, and agent inference. Retain evidence IDs and direct URLs.
6. Create at least five directions in `directions.json` and `mood-directions.md`. Every direction names its UX problem, linked `evidence_ids`, evidence application, all six axes, implementation difficulty, implementation risks, and trade-offs. Ensure every pair differs materially on at least three of layout, typography, palette, density, imagery, and interaction.
7. Run the research validator and then the directions validator. Present `reference-board.md`, the evidence summary, and the mood directions together for review. Transition to `directions_pending_approval` and stop for explicit approved IDs before generating images.

## Recovery rules

- **Blocked login or CAPTCHA:** Pause and ask the user to complete the login or challenge manually. Continue only after access is restored. Do not bypass the control, export cookies, or store credentials in artifacts.
- **Weak evidence:** Label confidence and limitations, narrow the claim, and seek a better primary or official source. If support remains weak, present the idea as agent inference rather than evidence.
- **Conflicting evidence:** Preserve both sources and their limitations. Explain which context each applies to; do not silently choose the convenient result.
- **Broken links:** Find the publisher's canonical replacement or archived official location when available. Update the direct URL and capture time. If the source cannot be verified, remove it from evidence-backed claims.
- **Unverifiable claims:** Do not cite or restate them as fact. Mark them as hypotheses for user research or omit them.
- **Sparse visual references:** Broaden one pattern-specific query at a time, across multiple products or platforms. Do not turn search-result thumbnails into citations and do not copy a single reference pixel-for-pixel.
