# Merge Gate proposed changes

Status: proposal  
Scope: product policy, demo UI, tool use, and system-level evaluation

## Objective

Merge Gate should be presented as a configurable pull-request control system,
not as a Claude prompt wrapper or a collection of evaluation controls.

The product should:

1. apply policies defined by each organization;
2. collect and verify live pull-request evidence;
3. use an LLM only for semantic risk analysis;
4. apply policy and hard controls deterministically;
5. expose a clear, auditable decision;
6. demonstrate its quality through multi-repository evaluation.

## 1. Organization-defined policy

### Current implementation

The target repository owns `.merge-gate/policy.toml`. Merge Gate reads the
policy from the pull request's immutable base commit. Current rules support:

- file-path matching;
- pull-request title-term matching;
- `auto_merge_candidate`, `human_review`, and `block` actions;
- a conservative conflict rule in which the most restrictive match wins;
- a default action for unmatched changes.

This proves repository-specific policy, but it is not yet a complete
organization-level policy system.

### Proposed policy hierarchy

Merge Gate should calculate an effective policy from:

```text
Organization baseline
        +
Repository-specific additions
        +
Verified pull-request evidence
        +
Verified semantic risk findings
        ↓
Deterministic final action
```

The organization baseline should contain rules that every connected repository
must follow. A repository policy may add stricter rules. It should not silently
weaken an organization-level `block` or `human_review` rule.

### Proposed rule capabilities

Extend the policy contract to support:

- required reviewer teams or roles;
- CI requirements;
- diff-completeness requirements;
- dependency, workflow, schema, permission, and secret-change categories;
- explicit exceptions with owners and expiry dates;
- policy source, version, and content hash in every decision;
- a safe failure when policy is missing, invalid, or unavailable.

Conceptual example:

```toml
[organization]
name = "Example Corp"
version = 3
default_action = "human_review"

[[rules]]
id = "PAY-01"
title = "Payment behavior"
action = "human_review"
paths = ["payments/**"]
required_teams = ["finance"]
requires_ci = true

[[rules]]
id = "SECRET-01"
title = "Committed secret material"
action = "block"
paths = [".env", ".env.*", "secrets/**"]

[[rules]]
id = "DOC-01"
title = "Documentation-only changes"
action = "auto_merge_candidate"
paths = ["docs/**", "README.md"]
path_match = "all"
requires_ci = true
```

This example is a target design, not the current accepted schema.

### Policy design principles

- Policies are versioned code, not free-form prompts.
- The LLM may identify semantic facts, but it may not override policy.
- Hard failures such as failed CI remain deterministic.
- Every decision records the exact effective policy that produced it.
- The same pull request may correctly receive different decisions under
  different organizations' policies.

### Policy acceptance criteria

- Two repositories can use different policies without changing Merge Gate.
- An organization baseline can be shared by multiple repositories.
- Repository rules may tighten the baseline.
- Unauthorized policy weakening fails safely.
- Conflicting matches have deterministic precedence.
- The UI and GitHub summary show matched rule IDs and required reviewers.
- Policy conformance has positive, negative, conflict, and missing-policy tests.

## 2. Simplified public UI

### Current problem

The public dashboard exposes internal development controls:

- Live PR;
- Evaluation fixtures;
- Replay another PR;
- Recompute evaluation fixture;
- Live decision;
- Evidence and policy;
- Evaluation;
- Methodology.

This makes the project feel like a laboratory and obscures the product's main
story.

### Proposed public demo

The primary page should tell one vertical story:

1. pull-request identity and observed GitHub checks;
2. one Merge Gate evaluation action;
3. final recommendation;
4. matched organization and repository policies;
5. verified evidence and citations;
6. a collapsed execution trace.

The main page should have at most two primary controls:

- **Refresh from GitHub**
- **Run Merge Gate**

The normal GitHub product path remains automatic. A pull-request event runs
Merge Gate without requiring a dashboard button. **Run Merge Gate** exists only
for a manual demo or explicit rerun.

### Rename the Claude action

Replace **Analyze with Claude** with **Run Merge Gate**.

Claude should appear as one replaceable implementation detail:

```text
Semantic judge: Claude Haiku
Judge confidence: 95%
Evidence citations verified: 4
```

The product is Merge Gate. Its name and primary action should not depend on the
current model provider.

### Proposed result layout

```text
Merge Gate
Live pull-request control

[Pull request summary]
[Observed GitHub checks]

[Run Merge Gate]

[Final decision]
Human review
Required owner: Finance

[Why this decision]
- PAY-01 matched
- CI passed
- Payout threshold changed
- Four evidence citations verified

[Execution trace — collapsed]
```

Use native Streamlit containers, badges, captions, and a collapsible status or
expander. Avoid additional CSS unless the native components cannot express the
required hierarchy.

### Remove or relocate

| Current control | Proposed treatment |
|---|---|
| Live PR selector | Remove; live is the default |
| Evaluation fixtures | Remove from the product page |
| Recompute fixture | Keep only in the evaluation harness |
| Replay another PR | Move to a developer-only page or mode |
| Live decision tab | Make it the primary page |
| Evidence and policy tab | Render below the decision |
| Evaluation tab | Move to a separate system-evaluation page |
| Methodology tab | Remove and link to documentation |
| Tool calls | Keep in one collapsed execution trace |

### Proposed app structure

If two public views are retained, use modern Streamlit navigation:

```text
streamlit_app.py
app_pages/
    live_decision.py
    system_evaluation.py
```

The public navigation should contain only:

- **Live decision**
- **System evaluation**

Replay and fixture controls should not appear in normal demo navigation.

## 3. Tools and agentic workflow

### What exists now

Merge Gate already uses a controlled sequence of external reads and internal
functions:

| Tool or function | Responsibility |
|---|---|
| GitHub REST client | Fetch PR metadata, files, diffs, checks, and statuses |
| Policy loader | Read policy at the immutable base commit |
| Policy matcher | Identify applicable repository requirements |
| Deterministic gates | Enforce CI and hard safety conditions |
| Structured LLM judge | Identify semantic risks and cite evidence |
| Citation verifier | Confirm cited files and policy rules exist |
| Decision composer | Produce the conservative final action |
| Execution trace | Record calls, outcomes, and timing |

The current application orchestrates these functions in a fixed order. Claude
does not currently choose and call tools autonomously. The execution trace is
therefore a function and integration trace, not proof of a fully autonomous
tool-using agent.

### Proposed hybrid agent design

Keep deterministic preflight and composition, but allow a read-only evidence
agent to request additional context when necessary:

```text
Deterministic preflight
        ↓
Read-only evidence agent
        ↓
Structured semantic judgment
        ↓
Citation and policy verification
        ↓
Deterministic composer
```

Potential read-only tools:

- `get_pr_metadata`
- `list_changed_files`
- `get_file_diff`
- `read_file_at_base`
- `read_file_at_head`
- `get_check_runs`
- `load_effective_policy`
- `find_related_tests`
- `read_codeowners`
- `retrieve_incident_context`

The agent should have:

- no merge, approval, comment, or repository-write tools;
- a maximum call budget;
- timeouts and rate limits;
- sanitized tool outputs;
- immutable base and head SHAs;
- a requirement to cite tool evidence;
- a fail-closed result when required evidence cannot be retrieved.

### Tool-use acceptance criteria

- **Run Merge Gate** displays the actual pipeline rather than a generic model
  spinner.
- Each tool call records its name, purpose, duration, and safe result summary.
- The semantic judge can request additional evidence only from its allowlist.
- Tool failures become explicit unknowns and cause conservative escalation.
- The LLM cannot change policy or the deterministic final-action precedence.

## 4. Evaluate Merge Gate as a whole

### Current evidence

The project currently has:

- a 40-example synthetic stress-test set;
- a 14-example synthetic held-out set;
- labels written by the project creator;
- one connected live demonstration repository;
- an evaluation harness for deterministic policies and cached live judgments.

This demonstrates the pipeline and evaluation mechanics. It does not establish
real-world accuracy or cross-repository generalization.

On the current 14-example synthetic held-out set, the deterministic
raw-evidence gate reports zero missed escalations, one unnecessary escalation,
100% critical recall, and 36% autonomous coverage. This is a small,
self-authored test result. It must not be presented as proof of the full live
hybrid system.

### Evaluation question

Ordinary accuracy is not the primary product objective. A system that sends
every pull request to a person can appear safe while providing no automation.

The main system-level question is:

> How many safe pull requests can Merge Gate handle autonomously while keeping
> unsafe autonomous decisions below an acceptable limit?

### Evaluation unit

Each benchmark record should contain:

- repository and pull-request identity;
- organization and repository policy versions;
- immutable base and head SHAs;
- normalized PR evidence;
- expected action;
- required reviewer team, when applicable;
- human rationale;
- independent reviewer IDs and adjudication status;
- Merge Gate prediction, confidence, citations, trace, latency, and cost.

Actual merge history is not sufficient ground truth. A merged pull request may
still have required additional review or may have contained an undetected
problem.

### Required datasets

Maintain separate sets:

1. **Development set** for changing prompts, rules, and thresholds.
2. **Held-out set** that is not used during development.
3. **Unseen-repository set** for testing generalization.
4. **Critical safety suite** covering permissions, tenant isolation, payments,
   secrets, destructive operations, CI failures, and weakened tests.
5. **Counterfactual policy suite** that evaluates the same PR under different
   organization policies.
6. **Robustness suite** covering prompt injection, incomplete diffs, missing
   policies, API failures, large files, and contradictory evidence.

Split by repository or time period, not by randomly mixing similar PRs from the
same repository across development and test sets.

### Baselines

Compare the complete system against:

- always review;
- CI-only;
- confidence threshold;
- diff-size threshold;
- path-only deterministic policy;
- LLM-only judgment;
- deterministic policy without the LLM;
- the complete verified hybrid pipeline.

### Primary system metrics

#### Safety

- **Unsafe autonomy rate:** unsafe PRs predicted as autonomous divided by all
  autonomous predictions.
- **Critical escalation recall:** critical PRs escalated or blocked divided by
  all critical PRs.
- **Missed escalations:** count and severity of unsafe autonomous decisions.
- **Block correctness:** whether deterministic block conditions were applied
  correctly.

#### Utility

- **Safe autonomous coverage:** safe PRs handled autonomously divided by all
  safe PRs.
- **False escalation rate:** safe PRs unnecessarily sent to a person.
- **Escalation precision:** escalated PRs that genuinely required escalation.

#### Trust

- **Policy compliance:** final actions consistent with the effective policy.
- **Evidence correctness:** citations that exist and support the finding.
- **Confidence calibration:** whether stated confidence matches observed
  correctness.
- **Consistency:** agreement across repeated runs on identical evidence.
- **Human override rate:** frequency and direction of reviewer disagreement.

#### Operations

- provider and tool failure rate;
- end-to-end latency;
- model and infrastructure cost per PR;
- GitHub rate-limit consumption;
- percentage of decisions with incomplete evidence.

### Primary reporting format

Report a safety-versus-automation curve rather than one accuracy number.

The eventual product claim should resemble:

> At X% safe autonomous coverage, Merge Gate escalated Y% of critical changes
> on repositories that were not used during development.

Every result should also show:

- sample size;
- repository count;
- risk-bucket breakdown;
- confidence intervals;
- reviewer agreement;
- performance on unseen repositories;
- failure and abstention rates.

### Evaluation scale

#### Demo-day evidence

- one connected live repository for the integration story;
- 60–100 labeled PR examples across approximately three repository types;
- at least two independent reviewers for the final held-out set;
- deterministic, LLM-only, and hybrid baseline comparison;
- an honest limitation statement.

#### Stronger portfolio evidence

- 200–500 historical PRs;
- five to ten repositories;
- multiple policy packs;
- time-based and unseen-repository testing;
- confidence intervals and reviewer-agreement reporting.

#### Product pilot

- shadow-mode use inside real organizations;
- no merge authority;
- comparison with actual reviewer decisions;
- override and incident analysis;
- policy calibration per organization before any autonomy is considered.

## 5. Proposed implementation order

### Phase 1 — Demo clarity

1. Rename **Analyze with Claude** to **Run Merge Gate**.
2. Remove fixture, replay, and methodology controls from the main page.
3. Replace the tab-heavy UI with one vertical decision flow.
4. Show the tool pipeline in a collapsed status or execution-trace section.
5. Keep evaluation as a separate page.
6. Continue using the live ClearLedger PR as the default proof.

### Phase 2 — Policy product

1. Define an organization-baseline policy schema.
2. Define repository overlay and precedence rules.
3. Add required reviewers and richer conditions.
4. Record effective policy source, version, and hash.
5. Add conformance and counterfactual policy tests.
6. Publish a policy template and connection guide.

### Phase 3 — Agentic evidence collection

1. Register the read-only evidence-tool allowlist.
2. Add a bounded evidence-gathering loop.
3. Preserve deterministic preflight, verification, and composition.
4. Expose tool calls and failures in the audit trace.
5. Evaluate whether the agentic loop improves results over fixed orchestration.

### Phase 4 — Credible system evaluation

1. Define the benchmark record schema.
2. Label real historical PRs with independent reviewers.
3. Add repository-level and time-based splits.
4. Run all baselines and ablations.
5. Report risk versus autonomous coverage.
6. Run shadow pilots before considering additional authority.

## 6. Demo-day definition of done

- The public UI has one obvious story and no fixture controls.
- The primary action is **Run Merge Gate**, not **Analyze with Claude**.
- A live GitHub PR triggers the automatic action without a pasted URL.
- The dashboard shows matched project policy, verified evidence, final action,
  and a concise tool trace.
- ClearLedger demonstrates that repository-defined policy changes the result.
- The evaluation page clearly separates synthetic results from live-model
  results.
- Claims state that current datasets are small and synthetic.
- The presentation explains the multi-repository benchmark plan.
- Merge Gate remains advisory and read-only.

## 7. Explicit non-goals for this iteration

- A visual policy editor.
- Automatic merging.
- Repository write access.
- Model-selected write tools.
- A claim of production-grade accuracy.
- Replacing deterministic controls with LLM judgment.
- Adding MCP solely to make the architecture appear more agentic.

The immediate priority is clarity and evidence: configurable policy, a simple
live decision experience, visible tool use, and an honest system-level
evaluation plan.
