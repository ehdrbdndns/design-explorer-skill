# Research and evidence workflow

Use this recipe after initializing a run and before any image generation.

1. Capture the screen purpose, required content, target viewports, required interactions, preservation constraints, supplied inputs, and implementation context in `brief.md`. Record the same normalized lists in `run.json`; `brief_ready` locks them as `brief_constraints` plus `brief_constraints_digest` and `brief_locked_at`, so later single-field drift fails closed. For a genuinely static UI with no interaction, include the exact line `Interactive requirements: none`; otherwise record at least one interaction. Note what must not change and whether screenshots need redaction.
2. Search one target screen or interaction pattern at a time with normal web search. Use `chrome:control-chrome` only when signed-in Chrome state or visual inspection is needed. Never bypass login, CAPTCHA, robots controls, or access restrictions.
3. Open the direct source behind each useful result. Before recording it, use the fetch/browser step to verify both the final redirect URL and resolved destination are public; do not follow or record a redirect to an internal address. Record the final direct public HTTP(S) URL, relevance, capture time, and concrete observations for layout, typography, palette, density, imagery, and interaction in `references.json`. The validator performs deterministic lexical/literal checks only: it rejects controls, URL credentials, special-use/local/wildcard host suffixes, and literal non-public IPs, but deliberately performs no live DNS lookup. Use `capture_path` only for a safe, sanitized relative artifact path without traversal or credentials.
4. Research in this order: official accessibility and platform guidance first; task-relevant credible research second; observed product patterns third. Prefer primary sources and current guidance. Scope research to the UX problem rather than collecting generic design inspiration.
5. Record each claim's problem, source, summary, intended application, and limitations in `evidence.json`. Write `design-evidence.md` as a user-facing synthesis with separate sections for official guidance, research, observed patterns, and agent inference. Retain evidence IDs and direct URLs.
6. Create at least five primary directions in `directions.json` and `mood-directions.md`, each with `kind: primary` and no derivation fields. Every direction names its UX problem, linked `evidence_ids`, evidence application, all six axes, implementation difficulty, implementation risks, and trade-offs. Link at least one `official` evidence item in every direction. Ensure every pair differs materially on at least three of layout, typography, palette, density, imagery, and interaction. If revision later adds a `derived` direction, append it after all directions named by its `derived_from_ids`. Each `combined_properties` axis must equal the named prior source's same axis after trim/case normalization, and every source must contribute at least one mapped axis.
7. Apply official accessibility and platform guidance as the common baseline. Add the required `baseline_exceptions` list to every direction; use `[]` normally. When a direction cannot meet a baseline, add an object with the exact `constraint` and a non-empty `justification`. An exception never removes the requirement to link official evidence. Disclose each exception in `mood-directions.md`; explicit direction approval is explicit approval of disclosed exceptions. Do not silently violate an official baseline.
8. Run the research validator and then the directions validator. Transitions rerun validation, so artifacts changed while awaiting approval cannot bypass the gate. Present `reference-board.md`, the evidence summary, and the mood directions together for review. In the response, present every direction as a complete block with:
   - Direction ID and name.
   - UX problem.
   - Evidence IDs and direct links.
   - Evidence application.
   - Explicit differences for layout, typography, palette, density, imagery, and interaction.
   - Trade-offs, difficulty, risks, and baseline exceptions.

   Artifact links do not replace these user-facing direction blocks. Transition to `directions_pending_approval` and stop for explicit approved IDs before generating images.

## Recovery rules

- **Blocked login or CAPTCHA:** Pause and ask the user to complete the login or challenge manually. Continue only after access is restored. Do not bypass the control, export cookies, or store credentials in artifacts.
- **Weak evidence:** Label confidence and limitations, narrow the claim, and seek a better primary or official source. If support remains weak, present the idea as agent inference rather than evidence.
- **Conflicting evidence:** Preserve both sources and their limitations. Explain which context each applies to; do not silently choose the convenient result.
- **Broken links:** Find the publisher's canonical replacement or archived official location when available. Update the direct URL and capture time. If the source cannot be verified, remove it from evidence-backed claims.
- **Unverifiable claims:** Do not cite or restate them as fact. Mark them as hypotheses for user research or omit them.
- **Sparse visual references:** Broaden one pattern-specific query at a time, across multiple products or platforms. Do not turn search-result thumbnails into citations and do not copy a single reference pixel-for-pixel.
