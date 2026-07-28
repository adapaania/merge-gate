# Live PR scenarios

Create a branch from `main` and apply one patch from the repository root:

```bash
git switch -c scenario/raise-payout-limit
git apply scenarios/raise-payout-limit.patch
python -m unittest discover -v
git add src/clearledger/payouts.py tests/test_payouts.py
git commit -m "Raise automatic payout threshold"
git push -u origin scenario/raise-payout-limit
```

Then open a PR. GitHub automatically runs ClearLedger tests followed by Merge
Gate; no PR URL is pasted into another application.

| Patch | Expected CI | Expected gate |
|---|---|---|
| `docs-operations-runbook.patch` | Pass | Auto-merge candidate |
| `raise-payout-limit.patch` | Pass | Human review (`PAY-01`) |
| `break-currency-precision.patch` | Fail | Block |
| `weaken-authorization-test.patch` | Pass | Human review (`SEC-01`) |
