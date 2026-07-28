"""Authorization rules for the sandbox service."""


def can_view_admin_audit(role: str) -> bool:
    """Return whether a role may view the administrator audit screen."""

    return role == "admin"
