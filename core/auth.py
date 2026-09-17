"""
core/auth.py
-------------
App-level access control — deliberately separate from Dhan credentials.

Why this exists: Dhan's client ID/access token authenticate the dashboard
*to Dhan*. They do nothing to stop a stranger who reaches the dashboard's
URL from using YOUR already-connected session to place live orders. Any
deployment reachable over a network (Docker host, cloud VM, etc.) needs
its own login gate — that's what `APP_PASSWORD` + `verify_password` are
for. Purely local (`localhost`-only) use can leave `APP_PASSWORD` unset.
"""

from __future__ import annotations

import hmac


def is_auth_required(configured_password: str) -> bool:
    """No password configured -> no gate (e.g. pure localhost use)."""
    return bool(configured_password)


def verify_password(candidate: str, configured_password: str) -> bool:
    """
    Timing-safe comparison (hmac.compare_digest) so response time can't be
    used to guess the password character-by-character. Returns True
    immediately (no comparison needed) when no password is configured at
    all — see `is_auth_required` for the gating decision itself.
    """
    if not configured_password:
        return True
    if not candidate:
        return False
    return hmac.compare_digest(candidate, configured_password)
