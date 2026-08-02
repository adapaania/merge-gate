# Live demo runbook

## Repositories

### Merge Gate

Publishes the reusable composite action at:

```text
adapaania/merge-gate@main
```

It owns the general safety controls, project-policy parser, structured judge,
evidence verifier, conservative composer, and GitHub job-summary renderer.

### ClearLedger

A public standalone payout-control repository is available at
https://github.com/adapaania/clearledger-demo and its proof PR is available at
https://github.com/adapaania/clearledger-demo/pull/1. Its source is retained
under `clearledger-demo-repo/`. It owns:

- application code and tests;
- `.merge-gate/policy.toml`;
- CODEOWNERS;
- its pull-request workflow;
- an `ANTHROPIC_API_KEY` GitHub Actions secret for live judgment;
- a `MERGE_GATE_EXECUTION_TOKEN` secret for the optional auto-merge phase;
- realistic PR scenario patches.

## What the project defines

ClearLedger's policy maps observable files to requirements:

| Rule | Scope | Action |
|---|---|---|
| `PAY-01` | payout limits, fees, settlement precision | Finance review |
| `SEC-01` | roles, authorization, security tests | Security review |
| `OPS-01` | CI and dependencies | Platform review |
| `GOV-01` | Merge Gate requirements | Governance review |
| `SECRET-01` | committed secret paths | Block |
| `DOC-01` | documentation-only changes | Candidate after CI |

The most restrictive matching project rule wins. General safety controls can
still make the result more restrictive.

## Live request path

```text
Developer opens ClearLedger PR
    ↓
GitHub runs ClearLedger tests
    ↓
Merge Gate composite action starts automatically
    ↓
read PR metadata, files, patches, and observed checks
    ↓
read .merge-gate/policy.toml from the base commit SHA
    ↓
apply prerequisite CI result
    ↓
match ClearLedger rules
    ↓
structured judge + typed evidence-source verification
    ↓
conservative final action
    ↓
GitHub job summary with evidence and execution trace
    ↓
verified candidate + policy opt-in → enable GitHub native auto-merge
```

## Demo sequence

1. Start on ClearLedger `main`.
2. Create `scenario/raise-payout-limit`.
3. Apply `scenarios/raise-payout-limit.patch`.
4. Run tests locally and show that they pass.
5. Push the branch and open a PR.
6. Watch **ClearLedger tests** run.
7. Watch **Merge Gate** start automatically afterward.
8. Open its job summary:
   - CI passed;
   - `PAY-01` and `SEC-01` matched;
   - final action is human review;
   - the tool/function table explains the entire run.
9. Open the Streamlit dashboard's default **Live decision** page and show the
   same GitHub checks, immutable project requirements, recomputed decision
   (after selecting **Run Merge Gate**), and tool/function trace without
   pasting a URL.
10. Repeat with `break-currency-precision.patch`:
   - tests fail;
   - Merge Gate still runs because the job uses `if: always()`;
   - the deterministic CI control returns block.
11. Use a non-draft documentation-only PR to demonstrate execution:
   - CI and the verified judge must agree on `auto_merge_candidate`;
   - the policy must have `[project.execution] enabled = true`;
   - the job summary records `execution: enabled`;
   - GitHub completes the squash merge after repository requirements pass.

## Publishing order

1. Commit and push the reusable action to `adapaania/merge-gate`.
2. Create the standalone ClearLedger repository.
3. Add `ANTHROPIC_API_KEY` as a ClearLedger Actions repository secret.
4. Add a narrowly scoped `MERGE_GATE_EXECUTION_TOKEN` Actions secret.
5. Enable repository auto-merge and configure branch protection/rulesets.
6. Copy the contents of `clearledger-demo-repo/` to its repository root.
7. Commit and push its baseline `main`.
8. Create and push one scenario branch.
9. Open the PR, mark an eligible candidate ready for review, and demonstrate.

No GitHub publication has been performed merely by creating these local source
files.
