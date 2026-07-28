# Merge Gate live-PR sandbox project

This directory is a deliberately small Python service inside the Merge Gate
repository. Pull requests modify this project so Merge Gate can analyze real
PR evidence from its own repository.

The repository's `main` branch contains the stable baseline. Scenario branches
each represent one candidate pull request:

| Branch | Scenario | Expected control outcome |
|---|---|---|
| `scenario/docs-auth-guide` | Documentation-only authentication guide | Auto-merge candidate |
| `scenario/auth-role-expansion` | Authorization behavior changes while CI passes | Human review |
| `scenario/broken-pricing-ci` | Production behavior changes and CI fails | Block |
| `scenario/weaken-auth-test` | A security assertion is removed while CI passes | Human review |

The last scenario is intentionally deceptive. A policy that treats every
test-only change as low risk will make the wrong recommendation.

## Run the baseline tests

```bash
cd demo-pr-repo
python -m unittest discover -s tests -v
```

## Repository contract

- Required CI must pass.
- Changes under `src/auth.py` require a security owner.
- Pricing behavior changes require an application owner.
- Removing or weakening security assertions requires human review.
- This sandbox contains no deployment or merge automation.
- Repository-level CI and ownership rules live in the root `.github/`
  directory so GitHub applies them to the scenario pull requests.
