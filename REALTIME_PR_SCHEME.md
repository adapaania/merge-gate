# Real-time pull-request scheme

## Product decision

Build this in three distinct surfaces:

1. **Automatic PR check:** the target repository invokes Merge Gate from its
   `pull_request` workflow after project CI. This is the demo-day product path.
2. **Event-driven GitHub App:** a deployed service receives webhooks and
   publishes custom checks. This is the production-shaped successor.
3. **Replay/debug adapter:** a user may paste a historical PR URL into the
   dashboard for evaluation or troubleshooting. This is not the normal trigger.

**Implementation status:** the automatic composite GitHub Action and a
connected ClearLedger repository template are implemented. The target project
owns `.merge-gate/policy.toml`; Merge Gate fetches it from the immutable PR base
SHA, applies project CI, and writes the result plus tool/function trace to the
GitHub job summary. The Streamlit URL adapter remains a secondary replay tool.
The GitHub App remains future work.

## Single-repository demo

The Merge Gate repository contains the baseline sandbox under
`demo-pr-repo/`. Four scenario patches are stored under
`demo-pr-repo/scenarios/` and are materialized as branches from the same
repository's `main`:

| Scenario branch | CI | Desired decision | What it proves |
|---|---:|---|---|
| `scenario/docs-auth-guide` | Pass | Auto-merge candidate | Sensitive words in documentation do not imply a runtime auth change |
| `scenario/auth-role-expansion` | Pass | Human review | Small, passing changes can still modify a security boundary |
| `scenario/broken-pricing-ci` | Fail | Block | A failed required check cannot be overridden |
| `scenario/weaken-auth-test` | Pass | Human review | Test-only does not automatically mean low risk |

The fourth scenario was designed to expose a weakness in the original
`is_low_risk_only()` rule. The current rule now escalates sensitive test paths,
removed assertions/tests, deleted test files when deletion status is visible,
and added skip/xfail markers.

This older structure creates a self-referential test:

```text
Merge Gate repository PR
    ↓ changes demo-pr-repo/
Merge Gate fetches that PR from GitHub
    ↓
Merge Gate evaluates its own repository's live evidence
```

## Replay/debug ingestion

### User experience

Add a source selector:

```text
PR source:  [Demo scenarios] [Replay GitHub PR]
```

When `Replay GitHub PR` is selected:

1. Paste a URL such as `https://github.com/owner/repository/pull/42`.
2. Select `Fetch PR`.
3. Show the repository, PR number, author, base branch, head SHA, draft status,
   and last-fetched time.
4. Show which evidence was retrieved and which evidence remains unknown.
5. Select `Run gate`.
6. Display the advisory decision and evidence trace.

Fetching the PR and calling the LLM should remain separate actions. Refreshing
GitHub metadata must not silently spend model tokens.

### Runtime pipeline

```text
GitHub PR URL
    ↓
parse owner / repository / PR number
    ↓
fetch PR metadata and head SHA
    ↓
fetch changed files and patches
    ↓
fetch required checks and commit statuses
    ↓
fetch repository policy / ownership files at the base ref
    ↓
normalize into evidence with explicit unknowns
    ↓
deterministic hard controls
    ↓
policy retrieval
    ↓
independent structured judge
    ↓
citation verification
    ↓
advisory result stored by head SHA
```

### Suggested modules

```text
github_client.py
    parse_pr_url()
    get_pull_request()
    list_pull_request_files()
    get_commit_checks()
    get_file_at_ref()

live_pr.py
    GithubPullRequestEvidence
    build_decision_from_github()
    summarize_check_state()
    classify_evidence_completeness()

run_store.py
    AnalysisRun
    make_run_key()
    load_cached_run()
    save_run()
```

Keep the existing policy engine independent of the GitHub client. GitHub is an
evidence source, not part of the decision logic.

## Evidence mapping

| Gate evidence | GitHub source | Confidence |
|---|---|---|
| PR title | Pull-request metadata | Known |
| Description | Pull-request metadata | Known |
| Changed files | Pull-request files endpoint | Known |
| Additions/deletions | Pull-request metadata and files | Known |
| Diff excerpt | Per-file patch | Sometimes incomplete |
| Head commit | Pull-request metadata | Known |
| Observed CI | Check runs and commit statuses | Known only after observed checks finish |
| Required-check set | Branch protection/rulesets | Not fetched in Stage 1 |
| Code owners | `.github/CODEOWNERS` at the base ref | Known if present |
| Repository policy | Versioned policy file at the base ref | Known if present |
| Reversibility | PR template/rollback evidence | Usually unknown |
| Incident linkage | External service/incident mapping | Unknown in the MVP |
| Agent confidence | Not a reliable GitHub fact | Omit |

Do not manufacture values for missing evidence. The live model should represent
important unknowns explicitly:

```python
ci_status: Literal["passed", "failed", "pending", "unknown"]
reversibility: Literal["proven", "claimed", "unknown"]
incident_linkage: Literal["linked", "not_linked", "unknown"]
```

Fail-safe behavior:

- failed required CI → block;
- pending required CI → pending/no decision;
- missing required policy or ownership evidence → human review;
- claimed but unverified rollback → human review for sensitive changes;
- incomplete/truncated diff → human review when the missing portion could alter
  the risk classification.

## GitHub access model

### Initial public-repository demo

Public PR metadata and files can be fetched without credentials, subject to low
rate limits. This is enough to prove the adapter with a public sandbox.

### Private repository demo

Use a fine-grained token stored only in an environment variable:

```text
GITHUB_TOKEN
```

Never display the token in Streamlit, store it in session state, write it to a
cache, or commit it.

### Product-shaped version

Use a GitHub App installed on selected repositories with minimum permissions:

- Pull requests: read;
- Contents: read;
- Checks: read;
- Commit statuses: read;
- Metadata: read.

Do not request:

- Contents: write;
- Pull requests: write;
- Administration;
- Actions: write;
- merge permission.

Add `Checks: write` only in a later advisory stage if the product will publish a
non-blocking check result.

GitHub references:

- Pull-request REST API:
  <https://docs.github.com/en/rest/pulls/pulls>
- GitHub App permissions:
  <https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app>

## Stage 2: event-driven shadow mode

Streamlit remains the dashboard. A separate HTTP service receives GitHub
webhooks.

```text
GitHub App
    ↓
FastAPI webhook endpoint
    ↓ verify signature and deduplicate delivery
    ↓ enqueue work and return quickly
analysis worker
    ↓ fetch authoritative current PR state
    ↓ run Merge Gate
result store
    ↓
Streamlit dashboard
```

### Events

Re-evaluate when:

- a PR is opened;
- a draft is marked ready for review;
- a PR is reopened;
- new commits synchronize the PR;
- required checks complete;
- the base branch or policy version changes.

The webhook payload is a trigger, not the final source of truth. After receiving
an event, fetch the current PR, files, checks, and policy from GitHub before
analyzing.

### Webhook safety

- Validate `X-Hub-Signature-256` with HMAC-SHA256 before parsing the event.
- Use a high-entropy webhook secret stored outside the repository.
- Use `X-GitHub-Delivery` as a delivery deduplication key.
- Check both the event type and action.
- Return to GitHub quickly and perform analysis asynchronously.
- Use HTTPS.
- Log delivery ID, repository, PR number, and head SHA without logging secrets
  or private code.
- Treat PR titles, descriptions, diffs, code comments, and policy files as
  untrusted model input.

GitHub references:

- Validating deliveries:
  <https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries>
- Webhook best practices:
  <https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks>

## Run identity and caching

One PR may be analyzed many times. Use an immutable run key:

```text
repository_id
+ pull_request_number
+ head_sha
+ policy_commit_sha
+ prompt_version
+ model_id
+ deterministic_rule_version
```

If the exact key already exists, reuse the stored result. A new head SHA or
policy version creates a new analysis run.

Store:

- repository and PR number;
- base and head SHAs;
- GitHub fetch timestamp;
- check state;
- evidence completeness;
- policy version;
- prompt and model versions;
- deterministic result;
- model judgment;
- citation-verification result;
- final advisory action;
- latency, token usage, cost, and errors;
- later human decision or override.

## Policy improvement exposed by the sandbox

The current prototype treats all test-only changes as low risk. That is too
broad.

Before the live demo is presented as reliable, change the policy to distinguish:

### Potentially low risk

- tests added;
- assertions strengthened;
- coverage expanded;
- fixtures updated alongside a reviewed production change.

### Review required

- tests deleted;
- assertions removed or weakened;
- security/payment/permission tests changed;
- CI configuration changed;
- tests skipped or marked expected-to-fail;
- coverage or mutation score decreases.

The `scenario/weaken-auth-test` branch exists specifically to evaluate this
improvement.

## Recommended implementation sequence

### Increment 1 — local adapter contract (complete)

- Implemented URL parsing, normalized evidence models, mocked GitHub API tests,
  pending/unknown CI handling, and incomplete-diff handling.

### Increment 2 — public PR fetch (complete)

- The app fetches a PR only after an explicit button click.
- It displays repository, PR number, head SHA, author, branches, fetch time,
  files, patches, observed checks, and unknown evidence.
- Analysis state is keyed by repository, PR number, head SHA, and judge mode.
- GitHub access is read-only.

### Increment 3 — same-repository scenario PRs

- Commit the sandbox baseline on the Merge Gate repository's `main`.
- Create each scenario branch from that same `main`.
- Apply the corresponding patch from `demo-pr-repo/scenarios/`.
- Push the four scenario branches to the Merge Gate repository.
- Open four PRs using `demo-pr-repo/PR_SCENARIOS.md`.
- Verify that GitHub Actions produces the intended pass/fail results.

### Increment 4 — live shadow evaluation

- Run all four PRs through Merge Gate.
- Compare expected and observed decisions.
- Confirm the improved test-only rule on the live weakened-test PR.
- Record latency, cost, evidence completeness, and model output.

### Increment 5 — webhook receiver

- Register a read-only GitHub App.
- Add a FastAPI webhook service and queue.
- Store immutable runs.
- Keep the GitHub side read-only.

### Increment 6 — non-blocking GitHub check

Only after shadow-mode evaluation:

- request Checks write permission;
- publish an advisory check;
- include the final action, evidence summary, and dashboard link;
- do not make it a required branch-protection check yet.

## Demo script

1. Open the four scenario PRs in the Merge Gate repository.
2. Show that three have passing CI and one has failing CI.
3. Paste the safe documentation PR into Merge Gate.
4. Show the live repository, head SHA, files, checks, policy, and low-risk result.
5. Paste the authorization PR and show that small diff plus passing CI still
   requires a human.
6. Paste the failing-CI PR and show that the deterministic block overrides the
   model.
7. Paste the weakened-test PR and show either:
   - the current policy miss, followed by the improved rule; or
   - the improved gate correctly escalating it.
8. End by showing that Merge Gate made no GitHub writes and performed no merge.

## Success criteria

- A PR URL can be converted into traceable evidence without manual copying.
- Every result identifies the exact head SHA analyzed.
- Unknown evidence remains unknown.
- Failed required CI always blocks.
- Sensitive changes cannot be approved solely because the diff is small.
- Test weakening is not treated as automatically safe.
- Refreshing GitHub evidence does not silently call the LLM.
- Private code and tokens never appear in logs or caches.
- No GitHub write permission is required for shadow mode.
- The four same-repository sandbox scenarios produce explainable,
  reproducible results.
