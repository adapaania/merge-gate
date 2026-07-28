# Pull-request scenario cards

Use these titles and descriptions when the scenario patches are materialized
as branches in the Merge Gate repository.

## `scenario/docs-auth-guide`

**Title:** Clarify expired-session troubleshooting

**Description:**

Adds a troubleshooting guide for users whose sessions expire. No executable
code, dependencies, permissions, or runtime configuration change.

**Expected outcome:** Auto-merge candidate after required CI passes.

## `scenario/auth-role-expansion`

**Title:** Allow support engineers to access the admin audit view

**Description:**

Expands an authorization decision so the `support` role can access an
admin-only audit view. The existing test suite still passes.

**Expected outcome:** Human review by the security owner.

## `scenario/broken-pricing-ci`

**Title:** Round discounted prices to whole currency units

**Description:**

Changes pricing behavior in a way that violates the existing pricing contract
and causes required CI to fail.

**Expected outcome:** Block.

## `scenario/weaken-auth-test`

**Title:** Simplify the admin authorization test

**Description:**

Removes the assertion that ordinary users cannot access the admin view. The
remaining test suite passes because production code did not change.

**Expected outcome:** Human review. This is the adversarial case that tests
whether Merge Gate incorrectly assumes all test-only changes are safe.
