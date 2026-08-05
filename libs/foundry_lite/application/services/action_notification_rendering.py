"""Prepare immutable after-commit notification payloads from pre-edit values."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from foundry_lite.domain.action_runtime.action_effects import ActionEffectV3
from foundry_lite.domain.action_runtime.action_notification_templates import (
    render_notification_template_payload,
)


@dataclass(frozen=True, slots=True)
class PreparedActionEffect:
    """Effect contract plus safe evidence about its pre-commit rendering boundary."""

    effect: ActionEffectV3
    rendering_evidence: Mapping[str, object] | None = None


def prepare_after_effect(
    effect: ActionEffectV3,
    *,
    object_properties: Mapping[str, object],
    object_type: str,
    object_id: str,
    object_version: int | None,
    parameters: Mapping[str, object],
    actor_user_id: str,
    action_run_id: str,
    action_api_name: str,
) -> PreparedActionEffect:
    """Freeze notification text before commit and leave other effect payloads unchanged."""
    if effect.kind != "notification":
        return PreparedActionEffect(effect)
    rendered = render_notification_template_payload(
        effect.payload,
        object_properties=object_properties,
        parameters=parameters,
        actor_user_id=actor_user_id,
        action_run_id=action_run_id,
        action_api_name=action_api_name,
    )
    return PreparedActionEffect(
        replace(effect, payload=rendered),
        {
            "phase": "pre_commit",
            "sourceObjectType": object_type,
            "sourceObjectId": object_id,
            "sourceObjectVersion": object_version,
            "templateFingerprint": _fingerprint(effect.payload),
            "renderedPayloadFingerprint": _fingerprint(rendered),
        },
    )


def _fingerprint(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
