# Merge Gate demo repository policies

These policies are fixtures for the advisory demo. They illustrate how a real
repository could provide versioned, retrievable review requirements.

## SEC-04: Authentication and authorization changes

Changes to authentication, authorization, session handling, token validation,
cryptographic keys, secrets, or permission boundaries require review by the
security team. Passing CI does not remove this requirement.

## DB-02: Database and migration safety

Destructive or irreversible schema changes require database-owner review and a
documented rollback plan. Additive, reversible migrations may proceed through
ordinary review when all migration checks pass.

## PAY-01: Payments and money movement

Changes affecting payment capture, refunds, billing, pricing, invoicing, or
financial ledgers require review by a payments owner. Small diff size is not a
reason to bypass this policy.

## INFRA-03: Infrastructure and deployment changes

Changes to deployment workflows, infrastructure as code, production
configuration, network policy, or access controls require platform review.
Failed infrastructure checks block the change.

## INC-05: Incident-linked components

Changes to components associated with a previous production incident require
human review until the component owner explicitly marks the incident control as
retired.

## LOW-01: Low-risk changes

Documentation-only, formatting-only, and test-only changes may be considered
low risk when they do not modify production configuration, generated runtime
artifacts, permissions, dependencies, or executable application behavior and
all required checks pass.

