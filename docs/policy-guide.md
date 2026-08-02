# Policy guide: organization baseline + repository overlay

This is the reference for writing and connecting a Merge Gate policy. It
covers the schema, the two-layer precedence model, a copy-pasteable template
for each layer, and how to wire a policy into the GitHub Action and the
Streamlit dashboard.

## The two layers

A decision can be shaped by up to two independently-owned policy documents:

```text
Organization baseline   (optional, shared by every connected repository)
        +
Repository overlay      (owned by the repository, in .merge-gate/policy.toml)
        ↓
Effective policy result
```

Both layers use the same rule schema (`policy_schema.py`). The only
difference is the TOML section header: an organization baseline starts with
`[organization]`, a repository overlay starts with `[project]`.

**Precedence is always most-restrictive-wins across both layers.** A
repository overlay can add stricter rules on top of the baseline, but it can
never relax a rule the organization baseline already matched — there is no
way to configure a repository policy that silently downgrades an
organization-level `block` or `human_review` result. If neither layer's
rules match a change, each layer's own `default_action` applies, and the
stricter of the two defaults wins.

Running with only a repository policy (no organization baseline configured)
behaves exactly as it did before organization policies existed — the
baseline layer is entirely optional.

## Rule schema

Every rule lives in a `[[rules]]` table:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique within the document. Must match `^[A-Z][A-Z0-9_-]*-\d+$` (e.g. `PAY-01`). |
| `title` | yes | Short human-readable name, shown in the dashboard and job summary. |
| `action` | yes | `auto_merge_candidate`, `human_review`, or `block`. |
| `reason` | yes | At least 8 characters. Shown verbatim as the audit reason. |
| `paths` | no | Glob patterns (fnmatch-style) matched against changed file paths. |
| `path_match` | no | `"any"` (default) or `"all"` — whether one or every changed file must match `paths`. |
| `title_terms` | no | Case-insensitive substrings matched against the PR title. |
| `required_teams` | no | Reviewer team/role names. Surfaced in the decision, not enforced by GitHub itself. |
| `requires_ci` | no | Documents that this rule assumes CI ran. (The deterministic gate already blocks/reviews on failed or missing CI globally — this is metadata, not a second gate.) |
| `category` | no | Free-text label (e.g. `"secrets"`, `"permissions"`) for your own reporting. |

A rule needs at least one of `paths` or `title_terms`. When several rules
match the same change, the **most restrictive matched action wins**
(`block` > `human_review` > `auto_merge_candidate`); their reasons and IDs
are joined in the result.

## Exceptions

An exception is a time-boxed, owner-attributed waiver for exactly one rule
in the *same* document — a repository cannot waive an organization rule, and
an organization cannot reach into a repository's exceptions:

```toml
[[exceptions]]
id = "EXC-01"
rule_id = "SECRET-01"
owner = "security-lead"
expires_on = 2026-06-30
reason = "Documented false positive for the vendored fixture file, tracked in SEC-4021."
paths = ["fixtures/sample.env"]   # optional: narrows the waiver to specific paths
```

- `expires_on` is a hard date. An expired exception is inert — silently, not
  as an error — so nothing breaks when someone forgets to renew it; the
  original rule just resumes applying.
- If `paths` is omitted, the exception applies wherever the rule matches.
- Waiving a rule only removes *that* rule from consideration. If another
  matched rule (or the document default) is still restrictive, that result
  still applies — an exception cannot force `auto_merge_candidate` by itself,
  it can only stop one specific rule from being the reason for escalation.

## Document-level fields

```toml
[organization]        # or [project] for a repository overlay
name = "Example Corp"
version = 3
default_action = "human_review"
default_reason = "Unclassified changes require a person until a rule clears them."
```

- `version` is an integer you bump on every meaningful change. It's recorded
  on every decision alongside a content hash of the exact file, so an
  auditor can always tell which policy text produced a given result.
- `default_action`/`default_reason` apply when nothing in `rules` matches.
  For an organization baseline, a conservative default (`human_review`) is
  what makes the baseline meaningful — it's the floor under every repository
  that hasn't been explicitly cleared for a category of change.

## Auto-merge execution

Decision-making remains advisory unless the policy at the PR's immutable base
commit explicitly opts into execution:

```toml
[project.execution]
enabled = true
merge_method = "squash"  # merge, squash, or rebase
```

If an organization baseline is configured, both the organization and project
documents must set `execution.enabled = true`. The organization baseline owns
the effective merge method, so a repository cannot weaken shared execution
governance. Policy opt-in alone is not enough: the action also needs a separate
`execution-token`.

Execution is attempted only for a final `auto_merge_candidate` with passing CI,
a complete diff, a non-draft same-repository PR, and unchanged head and base
SHAs. Merge Gate enables GitHub-native auto-merge; it does not bypass branch
protection or required approvals. Human-review and block results never call the
write API.

## Organization baseline template

Save as `.merge-gate/organization.toml` in whichever repository you point
`org-policy-repo` at (it does not need to be one of the repositories Merge
Gate evaluates):

```toml
[organization]
name = "Example Corp"
version = 1
default_action = "human_review"
default_reason = "Unclassified changes require a person until a rule clears them."

[[rules]]
id = "SECRET-01"
title = "Committed secret material"
action = "block"
reason = "Secret material must never be committed."
paths = [".env", ".env.*", "secrets/**"]

[[rules]]
id = "PAY-01"
title = "Payment behavior"
action = "human_review"
reason = "Payment changes require finance review."
paths = ["**/payments/**", "**/billing/**"]
required_teams = ["finance"]
requires_ci = true

[[rules]]
id = "DOC-01"
title = "Documentation-only changes"
action = "auto_merge_candidate"
reason = "Documentation changes may proceed after CI."
paths = ["docs/**", "README.md"]
path_match = "all"
requires_ci = true
```

## Repository overlay template

Save as `.merge-gate/policy.toml` in the repository Merge Gate evaluates.
See `clearledger-demo-repo/.merge-gate/policy.toml` for a complete real
example (`PAY-01`, `SEC-01`, `OPS-01`, `GOV-01`, `SECRET-01`, `DOC-01`); a
minimal starting point:

```toml
[project]
name = "YourRepo"
version = 1
default_action = "human_review"
default_reason = "Unclassified application changes require owner review."

[[rules]]
id = "DOC-01"
title = "Documentation only"
action = "auto_merge_candidate"
reason = "Documentation-only changes may proceed after CI."
paths = ["docs/**", "README.md"]
path_match = "all"

[[rules]]
id = "GOV-01"
title = "Merge policy governance"
action = "block"
reason = "Merge requirements cannot change without a separate governance process."
paths = [".merge-gate/**"]
```

## Connecting a repository

**Repository overlay** — always active, no extra configuration: Merge Gate
reads `.merge-gate/policy.toml` (or whatever `policy-path` you set) from the
pull request's immutable base commit.

**Organization baseline** — optional, off by default. Set these composite
action inputs (see `action.yml`):

```yaml
- uses: adapaania/merge-gate@main
  with:
    github-token: ${{ github.token }}
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    execution-token: ${{ secrets.MERGE_GATE_EXECUTION_TOKEN }}
    org-policy-repo: your-org/policy-baseline
    org-policy-ref: main
    org-policy-path: .merge-gate/organization.toml   # default shown
```

`org-policy-repo` needs no other special setup — it's read with the same
read-only `github-token` used for everything else, so the token must have at
least read access to that repository too.

`MERGE_GATE_EXECUTION_TOKEN` should be a separately managed GitHub App token or
fine-grained token limited to the target repository, with `Contents: write` and
`Pull requests: write`. Do not reuse a developer's broad CLI token. Omitting
the input leaves Merge Gate in advisory mode. If policy enables execution but a
verified candidate has no execution token, the job fails closed instead of
pretending the PR will merge.

**Streamlit dashboard** — the Live decision page picks up the same baseline
through environment variables, so the demo and the GitHub check stay
consistent:

```bash
MERGE_GATE_ORG_POLICY_REPO=your-org/policy-baseline
MERGE_GATE_ORG_POLICY_REF=main
MERGE_GATE_ORG_POLICY_PATH=.merge-gate/organization.toml
```

Leaving `MERGE_GATE_ORG_POLICY_REPO` unset (the default) runs exactly as
before — repository policy only, no organization layer.

## What gets recorded

Every decision carries, regardless of which layers were configured:

- `matched_organization_rules` / `matched_project_rules` — which rule IDs
  fired in each layer (shown in the dashboard and job summary as
  `org:RULE-ID` / `repo:RULE-ID`).
- `required_teams` — the union of `required_teams` from whichever rules won.
- `policy_sources` — for each configured layer: its scope, name, version,
  and a content hash of the exact policy text evaluated.

A missing, unreadable, or schema-invalid policy fails closed: parsing raises
before any decision is made, and the GitHub Action reports the run as failed
rather than silently evaluating with a partial or default policy.

## Testing a policy change

`tests/test_policy_schema.py` and `tests/test_effective_policy.py` are the
conformance suite for this schema: positive/negative/conflicting matches,
missing/invalid policy documents, exception expiry and scoping, and
counterfactual scenarios (the same PR evaluated under two different
organization policies, or two repositories under one organization). Add
cases there when you change matching or precedence behavior, not just when
you add a new field.
