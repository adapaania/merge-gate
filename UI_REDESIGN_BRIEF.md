# Merge Gate UI redesign brief

## Your task

Redesign the existing Merge Gate Streamlit interface so it feels calm, polished,
lightweight, and demo-ready.

Use your strongest product-design judgment. Do not treat the current page structure
as fixed. You may reorganize the interface if that produces a clearer experience,
but preserve all product behavior and safety controls.

Before editing:

1. Read `streamlit_app.py` and `.streamlit/config.toml`.
2. Run the app and inspect the current interface at desktop/laptop size.
3. Understand the decision pipeline before changing how it is presented.

## Product context

Merge Gate is not an AI code reviewer and it does not merge pull requests.

It is an advisory escalation gate for AI-authored pull requests. It uses:

- deterministic hard checks;
- retrieved repository policies;
- an independent structured LLM judgment;
- deterministic verification of the model's citations;
- evaluation metrics and human feedback.

It produces one of three advisory outcomes:

- auto-merge candidate;
- human review;
- blocked.

The central product question is:

> Does this pull request require human attention, based on verifiable evidence?

The primary audience is a demo-day evaluator seeing the product for the first time.
They should understand the decision and the value of the product within a few
seconds.

## Desired experience

The interface should feel:

- calm rather than dashboard-heavy;
- spacious without wasting the screen;
- trustworthy rather than flashy;
- technical enough to be credible, but understandable to a non-expert;
- visually consistent;
- comfortable to look at during a live presentation.

Aim for a restrained, contemporary product aesthetic. Prefer neutral surfaces,
subtle borders, generous spacing, clear typography, and one deliberate accent
color. Avoid a harsh pure-white canvas, heavy dark panels, excessive gradients,
glowing effects, or many competing colors.

Status colors should remain meaningful:

- green for an auto-merge candidate;
- amber/orange for human review;
- red for blocked or failed verification.

Color must not be the only way the outcome is communicated. Always include a clear
label and icon or supporting text.

## Primary user journey

The main path should be obvious:

1. Choose a pull request.
2. Optionally choose the evaluation dataset and judge mode.
3. Run the gate.
4. Read the final recommendation immediately.
5. Understand the most important reason for that recommendation.
6. Inspect evidence, policy retrieval, model output, or evaluation only when needed.

The first viewport should prioritize:

- the product name and one-sentence purpose;
- the pull-request selector and primary action;
- the selected pull request;
- the final gate outcome;
- a small summary of the evidence behind it.

Do not make users read a methodology document before understanding the result.

## Information hierarchy

### Primary information

Keep this immediately visible:

- pull-request title and ID;
- final gate action;
- concise final reason;
- CI status;
- changed-file count;
- diff size;
- whether the evidence citations were verified.

### Secondary information

Make this available with one clear interaction:

- layer-by-layer decision trace;
- changed files and diff excerpt;
- retrieved repository policies;
- judge confidence, reasoning, and uncertainties;
- human feedback form.

### Tertiary information

Use progressive disclosure for:

- raw structured JSON;
- full policy-comparison tables;
- scenario-level error inspection;
- detailed methodology;
- dataset caveats;
- cache and diagnostic information.

Do not place raw JSON or a large table in the default reading path.

## Functional behavior that must be preserved

Do not change the underlying product logic.

Preserve all of the following:

- both datasets in `DATASETS`;
- offline fixture and live Claude judge modes;
- offline mode as the safe default;
- no live API call until the user explicitly presses the run button;
- safe fallback to the offline fixture when the live judge is unavailable;
- session-state caching keyed by dataset, PR, and judge mode;
- deterministic hard checks;
- policy retrieval;
- structured judgment;
- citation verification;
- final action composition;
- evaluation metrics and policy comparison;
- human feedback capture;
- the statement that the tool is advisory and never performs a merge.

Do not modify:

- model prompts;
- policies or policy-retrieval behavior;
- evaluation labels or metric calculations;
- datasets;
- engine decision logic;
- LLM caching behavior.

If a UI change requires updating the Streamlit smoke test, change only the UI
expectations needed for the new design.

## Streamlit implementation constraints

- Prefer native Streamlit elements and `.streamlit/config.toml`.
- Use custom CSS only when a native Streamlit option cannot achieve the intended
  result. Keep any CSS small, stable, and documented.
- Do not rebuild ordinary Streamlit widgets in HTML.
- Do not add JavaScript.
- Do not use deprecated `st.components.v1`.
- Do not add `use_container_width`; use `width="stretch"` or `width="content"`.
- Prefer Material Symbols to decorative emoji.
- Use sentence casing for headings, tabs, labels, and buttons.
- Use captions for supporting context instead of large informational callouts.
- Use warnings and errors only for information that genuinely requires attention.
- Keep global settings in the sidebar, a popover, or another unobtrusive control.
- Use expanders or another clear progressive-disclosure pattern for diagnostics.
- Avoid more than four columns in any row.
- Avoid horizontal overflow at common laptop widths.
- Do not introduce new dependencies unless absolutely necessary.

## Creative freedom

You may:

- replace tabs with a clearer navigation pattern;
- change the page from wide to centered if it improves readability;
- redesign the result presentation;
- change spacing, typography, borders, theme tokens, and accent colors;
- replace metric cards with a calmer summary treatment;
- rename UI labels for clarity;
- reorganize evidence and evaluation sections;
- simplify or shorten explanatory copy;
- create small reusable rendering helpers inside `streamlit_app.py`.

Make choices based on the product story, not on preserving the current design.

## Things to avoid

- a wall of cards;
- too many badges;
- four equally prominent metrics when only one or two drive the decision;
- multiple heavy callout boxes stacked together;
- large blocks of methodology on the main screen;
- decorative visualizations that do not help explain safety versus autonomy;
- excessive uppercase text;
- tiny low-contrast text;
- presenting agent confidence as proof of safety;
- implying that the model alone makes the final decision;
- implying that the prototype is production-safe;
- removing access to evidence in the name of visual simplicity.

## Authorship and Git requirements

- Do not add an “authored by,” “built by,” or AI-attribution line anywhere.
- Do not add Claude, Codex, or another AI system as an author or collaborator.
- Do not add co-author metadata.
- Do not commit, push, create a branch, or open a pull request.
- Do not modify Git configuration or repository ownership metadata.

## Acceptance criteria

The redesign is successful when:

1. A new viewer can identify the selected PR and final action within five seconds.
2. The main screen has one obvious primary action.
3. Settings do not dominate the page.
4. The default view is visually calm and does not feel crowded.
5. Auto-merge candidate, human review, and blocked states are unmistakable.
6. The decision is supported by visible evidence, not just confidence.
7. Detailed evidence and evaluation remain easy to find.
8. Raw JSON and diagnostic tables are hidden by default.
9. The interface works at a common laptop viewport without horizontal overflow.
10. Live mode still requires an explicit click before spending API tokens.
11. All automated tests pass.
12. No browser-console or Streamlit rendering errors are introduced.

## Validation

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Then run or refresh:

```bash
.venv/bin/streamlit run streamlit_app.py --server.port 8501
```

Visually inspect at least:

- the default overview;
- an auto-merge candidate;
- a human-review case;
- a blocked case;
- the evidence view;
- the evaluation view;
- the collapsed and expanded settings state.

## Deliverable

Implement the redesign in the existing repository. At the end, provide:

- a concise explanation of the design direction;
- a list of files changed;
- the main interaction changes;
- test results;
- any remaining visual or usability limitation.
