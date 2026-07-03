"""Server-side media-classification clearance derived from the request context.

The caller's allowed-classification set is a SERVER decision computed from ``ctx.roles`` —
never a client-supplied value (a client could otherwise claim full clearance by omitting the
parameter, since ``None`` means "every classification is permitted"). Both the content/visual
search entry points and reference binding derive their clearance here so the media plane has
exactly one place that maps a principal to the classifications it may see.
"""

from __future__ import annotations

from foundry_lite.domain.context import RequestContext

# Ordered media-classification lattice (low -> high sensitivity). "" is the unclassified
# baseline (always visible); a caller may see a classification only when its level is at or
# below the clearance its roles grant. Unknown classifications have no level and are therefore
# never cleared for a non-full-clearance caller (fail-closed).
# "secret" here is a classification label, not a credential.
_CLASSIFICATION_LEVEL: dict[str, int] = {"": 0, "public": 0, "internal": 1, "confidential": 2, "secret": 3}  # nosec B105

# Role -> maximum clearance level. A role that is absent here contributes the public baseline
# (level 0), so an unprivileged caller can never see restricted media.
_ROLE_CLEARANCE_LEVEL: dict[str, int] = {"admin": 3, "data_engineer": 2, "ops_manager": 2, "finance": 2}

_FULL_CLEARANCE_LEVEL = max(_CLASSIFICATION_LEVEL.values())


def allowed_media_classifications(ctx: RequestContext) -> tuple[str, ...] | None:
    """Return the classifications the caller is cleared for, or ``None`` for full clearance.

    ``None`` is returned only for a caller whose roles reach the top of the lattice (so it also
    covers classifications added later); every other caller gets a concrete, fail-closed
    allow-list. The result is fed to ``is_classification_cleared`` / the index PRE-filter, so a
    restricted unit never enters ranking for a caller who is not cleared for it.
    """
    level = max((_ROLE_CLEARANCE_LEVEL.get(role, 0) for role in ctx.roles), default=0)
    if level >= _FULL_CLEARANCE_LEVEL:
        return None
    return tuple(name for name, name_level in _CLASSIFICATION_LEVEL.items() if name_level <= level)


__all__ = ["allowed_media_classifications"]
