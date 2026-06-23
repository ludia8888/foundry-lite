"""Contract for ``MediaPolicyEvaluator`` (ADR-0001 invariant 3 / doc §12.1).

Every derivative/content-unit/embedding/index entry must inherit an envelope no
weaker than its source. Contract only this sprint. We lock that ``inherit_envelope``
preserves tenant/classification (and legal hold) and that reads yield an auditable
decision id.
"""

from __future__ import annotations

from foundry_lite.application.ports.media_policy import MediaPolicyEvaluator, MediaReadDecision
from foundry_lite.application.ports.media_repository import SecurityEnvelope


class _FakeMediaPolicy:
    """Policy that copies the source envelope verbatim and allows same-tenant reads."""

    def inherit_envelope(self, source: SecurityEnvelope) -> SecurityEnvelope:
        return source

    def evaluate_read(self, *, tenant_id: str, envelope: SecurityEnvelope, principal_id: str) -> MediaReadDecision:
        allowed = tenant_id == envelope.tenant_id
        return MediaReadDecision(
            is_allowed=allowed,
            security_decision_id="dec-1",
            reason=None if allowed else "cross_tenant",
        )


def test_media_policy_inherits_no_weaker_envelope_and_audits_reads() -> None:
    evaluator: MediaPolicyEvaluator = _FakeMediaPolicy()
    source = SecurityEnvelope(tenant_id="t", classification="confidential", policy_version="v1", has_legal_hold=True)

    inherited = evaluator.inherit_envelope(source)
    same_tenant = evaluator.evaluate_read(tenant_id="t", envelope=source, principal_id="p")
    other_tenant = evaluator.evaluate_read(tenant_id="other", envelope=source, principal_id="p")

    assert inherited.classification == "confidential" and inherited.has_legal_hold is True
    assert same_tenant.is_allowed is True and same_tenant.security_decision_id == "dec-1"
    assert other_tenant.is_allowed is False and other_tenant.reason == "cross_tenant"
