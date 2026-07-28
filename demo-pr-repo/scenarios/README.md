# Reproducible PR scenarios

These patches preserve the four demo pull-request changes inside the main Merge
Gate repository.

Apply a patch from the repository root:

```bash
git switch main
git switch -c scenario/docs-auth-guide
git apply demo-pr-repo/scenarios/docs-auth-guide.patch
python -m unittest discover -s demo-pr-repo/tests -t demo-pr-repo -v
git add demo-pr-repo/docs/auth/troubleshooting.md
git commit -m "Document expired-session troubleshooting"
git switch main
```

Each patch is intended for its own branch created from `main`:

| Branch | Patch | Suggested commit |
|---|---|---|
| `scenario/docs-auth-guide` | `docs-auth-guide.patch` | `Document expired-session troubleshooting` |
| `scenario/auth-role-expansion` | `auth-role-expansion.patch` | `Expand audit access to support role` |
| `scenario/broken-pricing-ci` | `broken-pricing-ci.patch` | `Round discounted prices to whole units` |
| `scenario/weaken-auth-test` | `weaken-auth-test.patch` | `Simplify admin authorization test` |

The patches are data fixtures on `main`; applying one modifies only the sandbox
files that should appear in that scenario's pull-request diff.

Create and commit each branch separately. The broken-pricing scenario is
expected to fail the sandbox test check; do not fix that failure on its scenario
branch.
