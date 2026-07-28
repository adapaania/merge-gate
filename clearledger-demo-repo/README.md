# ClearLedger

ClearLedger is a small, production-shaped payout-control service used to
demonstrate Merge Gate against a realistic second repository.

It models three important controls:

- only finance roles may create payouts;
- payouts above the automatic threshold require manual release;
- settlement amounts retain currency precision.

This repository does not move real money. Its purpose is to make the merge
decision problem concrete and auditable during the demo.

## Pull-request control

The repository owns its requirements in
`.merge-gate/policy.toml`. Every pull request automatically runs:

1. ClearLedger tests;
2. the reusable `adapaania/merge-gate` action;
3. a GitHub job summary containing the decision and execution trace.

Developers do not paste a PR URL into Merge Gate. The `pull_request` event
provides the repository, PR number, base commit, and head commit automatically.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -v
```

## Demo scenarios

Reproducible PR patches are stored under `scenarios/`. Apply exactly one patch
to a new branch created from `main`, commit it, and open a PR.

| Scenario | CI | Expected Merge Gate result |
|---|---|---|
| Documentation runbook | Pass | Auto-merge candidate |
| Raise automatic payout limit | Pass | Human review |
| Break currency precision | Fail | Block |
| Weaken authorization tests | Pass | Human review |

This directory is the source for a standalone GitHub repository. It is kept
without a nested `.git` directory while it lives inside the Merge Gate
workspace.
